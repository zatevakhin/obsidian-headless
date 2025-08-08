import click
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pathlib import Path
import os
import sys
from datetime import datetime

import yaml
import jinja2
from string import Formatter
from pydantic import BaseModel
from typing import List

import hashlib
import tempfile
import difflib

app = FastAPI(
    title="Obsidian Headless API",
    version="0.1.0",
    description="Minimal API to read/write/patch files in an Obsidian vault.",
)


class MessageResponse(BaseModel):
    message: str


class PatchResponse(BaseModel):
    message: str
    etag: str


# This will be set from the configuration file
VAULT_PATH = Path()
CONFIG = {}


class SafeFormatter(Formatter):
    def get_field(self, field_name, args, kwargs):
        # Allow access to 'now'
        if field_name == "now":
            return datetime.now(), field_name
        raise ValueError(f"Invalid field name: {field_name}")

    def format_field(self, value, format_spec):
        if isinstance(value, datetime):
            return value.strftime(format_spec)
        return super().format_field(value, format_spec)


@app.get(
    "/api/daily-note",
    response_model=str,
    status_code=200,
    tags=["daily"],
    summary="Get or create today's daily note",
)
def get_daily_note():
    # TODO: Add security checks to prevent directory traversal
    location_template = CONFIG.get("daily_note", {}).get(
        "location", "daily/{now:%Y}/{now:%Y-%m-%d}.md"
    )

    formatter = SafeFormatter()
    file_name = formatter.format(location_template, now=datetime.now())

    full_path = VAULT_PATH / file_name

    full_path.parent.mkdir(parents=True, exist_ok=True)
    if not full_path.is_file():
        # If a template is configured, try to render it into the new daily note
        template_path = CONFIG.get("daily_note", {}).get("template")
        if template_path:
            # Respect the configured path exactly. If it's absolute, use it; if
            # relative, resolve it relative to the repository root (this file's dir).
            tpl_candidate = Path(template_path)
            if not tpl_candidate.is_absolute():
                repo_root = Path(__file__).resolve().parent
                tpl_candidate = repo_root / template_path

            # Require Jinja2 to be installed; render the template.
            if tpl_candidate.is_file():
                tpl_text = tpl_candidate.read_text()
                rendered = jinja2.Template(tpl_text).render(now=datetime.now())
                full_path.write_text(rendered)
            else:
                # Template path configured but file not found; create empty file
                full_path.touch()
        else:
            full_path.touch()
    return JSONResponse(content=full_path.read_text())


@app.get(
    "/files/{file_path:path}",
    response_model=str,
    status_code=200,
    tags=["files"],
    summary="Read file contents",
)
def read_file(file_path: str):
    # TODO: Add security checks to prevent directory traversal
    full_path = VAULT_PATH / file_path
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return JSONResponse(content=full_path.read_text())


@app.post(
    "/files/{file_path:path}",
    response_model=MessageResponse,
    status_code=200,
    tags=["files"],
    summary="Create a new file",
)
async def create_file(file_path: str, request: Request):
    # TODO: Add security checks
    content = await request.body()
    full_path = VAULT_PATH / file_path
    if full_path.exists():
        raise HTTPException(status_code=400, detail="File already exists")

    # Create parent directories if they don't exist
    full_path.parent.mkdir(parents=True, exist_ok=True)

    full_path.write_text(content.decode())
    return {"message": "File created successfully"}


@app.put(
    "/files/{file_path:path}",
    response_model=MessageResponse,
    status_code=200,
    tags=["files"],
    summary="Replace file contents",
)
async def update_file(file_path: str, request: Request):
    # TODO: Add security checks
    # TODO: Add PATCH endpoint with difflib support for partial updates
    content = await request.body()
    full_path = VAULT_PATH / file_path
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    full_path.write_text(content.decode())
    return {"message": "File updated successfully"}


@app.patch(
    "/files/{file_path:path}",
    response_model=PatchResponse,
    status_code=200,
    tags=["files"],
    summary="Patch or replace file (ndiff or If-Match)",
)
async def patch_file(file_path: str, request: Request):
    # Security: resolve target path and ensure it's inside VAULT_PATH
    resolved = (VAULT_PATH / file_path).resolve()
    vault_resolved = VAULT_PATH.resolve()
    # TODO: Add security checks to prevent directory traversal. Skipping checks in this first iteration.

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")

    # read current content and compute hash
    try:
        current_text = resolved.read_text(encoding="utf-8")
    except Exception:
        current_text = ""
    current_hash = hashlib.sha256(current_text.encode("utf-8")).hexdigest()

    # If-Match full replace mode (optimistic concurrency)
    if_match = request.headers.get("if-match")
    if if_match:
        if if_match != current_hash:
            raise HTTPException(status_code=409, detail="ETag mismatch")
        new_text = body.decode("utf-8")
    else:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("text/x-ndiff"):
            ndiff_lines = body.decode("utf-8").splitlines(keepends=True)
            # difflib.restore(..., 2) creates the "new" file
            patched_lines = list(difflib.restore(ndiff_lines, 2))
            new_text = "".join(patched_lines)
        else:
            raise HTTPException(status_code=415, detail="Unsupported patch type")

    # atomic write to temp + replace
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(resolved.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp_path, str(resolved))
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    new_hash = hashlib.sha256(new_text.encode("utf-8")).hexdigest()
    return JSONResponse(
        status_code=200,
        content={"message": "patched", "etag": new_hash},
        headers={"ETag": new_hash},
    )


@app.get(
    "/search/content",
    response_model=List[str],
    status_code=200,
    tags=["search"],
    summary="Search file content",
)
def search_content(q: str):
    # TODO: Add security checks
    matches = []
    for root, _, files in os.walk(VAULT_PATH):
        for file in files:
            if file.endswith(".md"):
                full_path = Path(root) / file
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    if q in f.read():
                        matches.append(str(full_path.relative_to(VAULT_PATH)))
    return matches


@app.get(
    "/search/filename",
    response_model=List[str],
    status_code=200,
    tags=["search"],
    summary="Search filenames",
)
def search_filename(q: str):
    # TODO: Add security checks
    matches = []
    for root, _, files in os.walk(VAULT_PATH):
        for file in files:
            if q in file:
                full_path = Path(root) / file
                matches.append(str(full_path.relative_to(VAULT_PATH)))
    return matches


@click.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    required=True,
    help="Path to YAML configuration file (contains server.host, server.port, vault.location)",
)
def main(config: str):
    """
    Run the FastAPI server for the Obsidian vault.
    """
    global VAULT_PATH, CONFIG

    # Load configuration only from the explicit path provided via --config
    if not config or not os.path.isfile(config):
        click.echo(f"Config file not found: {config}", err=True)
        sys.exit(2)

    with open(config, "r", encoding="utf-8") as f:
        CONFIG = yaml.safe_load(f) or {}

    # Normalize daily_note: accept vault.daily_note or top-level daily_note
    CONFIG.setdefault("daily_note", CONFIG.get("vault", {}).get("daily_note", {}))

    # Validate required sections
    server_cfg = CONFIG.get("server")
    vault_cfg = CONFIG.get("vault")
    if not server_cfg or not vault_cfg or "location" not in vault_cfg:
        click.echo(
            "Config file must contain 'server' and 'vault.location' keys", err=True
        )
        sys.exit(2)

    # Derive server values from config
    host = server_cfg.get("host")
    try:
        port = int(server_cfg.get("port"))
    except Exception:
        click.echo("Invalid 'port' in config; must be an integer", err=True)
        sys.exit(2)

    # Vault path comes from config
    VAULT_PATH = Path(vault_cfg.get("location"))

    click.echo(f"Starting server for vault at: {VAULT_PATH}")
    click.echo(f"API running at: http://{host}:{port}")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
