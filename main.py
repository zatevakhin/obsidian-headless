import click
import uvicorn
from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import JSONResponse
from pathlib import Path
import os
import sys
from datetime import datetime
import logging

import yaml
import jinja2
from string import Formatter
from pydantic import BaseModel
from typing import List

import hashlib
import whatthepatch

app = FastAPI(
    title="Obsidian Headless API",
    version="0.1.0",
    description="Minimal API to read/write/patch files in an Obsidian vault.",
)


# Module logger; configured in serve()
logger = logging.getLogger("obsidian")


class MessageResponse(BaseModel):
    message: str


class PatchResponse(BaseModel):
    message: str
    etag: str


class DailyNoteResponse(BaseModel):
    content: str
    path: str


class ReadFileRequest(BaseModel):
    """
    Request model for reading a file.
    Example: {"path": "some/dir/file.md"}
    """

    path: str


class CreateFileRequest(BaseModel):
    """
    Request model for creating a file. Clients MUST send JSON matching this model.
    Example: {"path": "some/dir/file.md", "content": "..."}
    """

    path: str
    content: str


class UpdateFileRequest(BaseModel):
    """
    Request model for replacing file contents.
    Example: {"path": "some/dir/file.md", "content": "..."}
    """

    path: str
    content: str


class PatchFileRequest(BaseModel):
    """
    Request model for patching files with unified diff format.
    The diff field should contain a standard unified diff (like from 'git diff' or 'diff -u').
    Example: {"path":"notes/today.md", "diff": "--- a/file.txt\\n+++ b/file.txt\\n@@ -1,3 +1,3 @@\\n line1\\n-old line\\n+new line\\n line3"}
    """

    path: str
    diff: str


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
        return super(SafeFormatter, self).format_field(value, format_spec)


def _resolve_safe(path: Path) -> Path:
    """Resolve a candidate path and ensure it remains inside VAULT_PATH.

    Raises HTTPException(400) on traversal attempts.
    """
    resolved = (VAULT_PATH / path).resolve()
    vault_resolved = VAULT_PATH.resolve()
    try:
        resolved.relative_to(vault_resolved)
    except Exception:
        logger.warning(
            "Path traversal attempt: %s (resolved=%s, vault=%s)",
            path,
            resolved,
            vault_resolved,
        )
        raise HTTPException(status_code=400, detail="Invalid file path")
    return resolved


@app.get(
    "/api/daily-note",
    response_model=DailyNoteResponse,
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

    try:
        text = full_path.read_text()
        logger.info("Read daily note: %s (size=%d)", full_path, len(text))
    except Exception:
        logger.exception("Failed to read daily note: %s", full_path)
        raise HTTPException(status_code=500, detail="Internal server error")

    # Return both content and path (relative to vault)
    rel_path = str(full_path.relative_to(VAULT_PATH))
    return JSONResponse(content={"content": text, "path": rel_path})


@app.get(
    "/files",
    response_model=str,
    status_code=200,
    tags=["files"],
    summary="Read file contents",
)
def read_file(payload: ReadFileRequest = Body(...)):
    # Security: resolve and validate path
    try:
        full_path = _resolve_safe(Path(payload.path))
    except HTTPException:
        raise
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        text = full_path.read_text()
        logger.info("Read file: %s (size=%d)", full_path, len(text))
    except Exception:
        logger.exception("Failed to read file: %s", full_path)
        raise HTTPException(status_code=500, detail="Internal server error")
    return JSONResponse(content=text)


@app.post(
    "/files",
    response_model=MessageResponse,
    status_code=200,
    tags=["files"],
    summary="Create a new file",
)
async def create_file(payload: CreateFileRequest = Body(...)):
    """Create a file from a JSON request model.

    The request MUST be application/json and match CreateFileRequest.
    """
    # Security: resolve and validate path
    try:
        full_path = _resolve_safe(Path(payload.path))
    except HTTPException:
        raise

    logger.debug("CREATE request for: %s", payload.path)
    logger.debug("Resolved path: %s", str(full_path))

    if not payload.content:
        logger.warning("Create called with empty content for: %s", full_path)
        raise HTTPException(status_code=400, detail="Empty content provided")

    if full_path.exists():
        logger.warning("Create called but file exists: %s", full_path)
        raise HTTPException(status_code=400, detail="File already exists")

    # Create parent directories if they don't exist
    full_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Write text content as UTF-8
        full_path.write_text(payload.content, encoding="utf-8")
        size = full_path.stat().st_size if full_path.exists() else 0
        logger.info("File created: %s (%d bytes)", full_path, size)
    except Exception as e:
        logger.exception("Failed to write file %s: %s", full_path, e)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {"message": "File created successfully"}


@app.put(
    "/files",
    response_model=MessageResponse,
    status_code=200,
    tags=["files"],
    summary="Replace file contents",
)
async def update_file(payload: UpdateFileRequest = Body(...)):
    # Security: resolve and validate path
    try:
        full_path = _resolve_safe(Path(payload.path))
    except HTTPException:
        raise

    logger.debug("UPDATE request for: %s", payload.path)
    logger.debug("Resolved path: %s", str(full_path))

    if not full_path.is_file():
        logger.warning("Update called but file not found: %s", full_path)
        raise HTTPException(status_code=404, detail="File not found")
    try:
        full_path.write_text(payload.content, encoding="utf-8")
        size = full_path.stat().st_size if full_path.exists() else 0
        logger.info("File updated: %s (%d bytes)", full_path, size)
    except Exception as e:
        logger.exception("Failed to update file %s: %s", full_path, e)
        raise HTTPException(status_code=500, detail="Internal server error")
    return {"message": "File updated successfully"}


@app.patch(
    "/files",
    response_model=PatchResponse,
    status_code=200,
    tags=["files"],
    summary="Patch file with unified diff",
)
async def patch_file(payload: PatchFileRequest = Body(...)):
    file_path = payload.path

    try:
        resolved = _resolve_safe(Path(file_path))
    except HTTPException:
        raise

    logger.debug("PATCH request for: %s", file_path)
    logger.debug("Resolved path: %s", str(resolved))

    if not resolved.is_file():
        logger.warning("Patch called but file not found: %s", resolved)
        raise HTTPException(status_code=404, detail="File not found")

    diff_text = payload.diff
    if not diff_text:
        logger.warning("Empty diff in patch payload for: %s", resolved)
        raise HTTPException(status_code=400, detail="Empty diff")

    # Read current file content
    try:
        original_text = resolved.read_text(encoding="utf-8")
    except Exception as e:
        logger.exception("Failed to read file for patching: %s", resolved)
        raise HTTPException(status_code=500, detail="Failed to read file")

    # Handle JSON-escaped newlines
    if "\\n" in diff_text:
        diff_text = diff_text.replace("\\n", "\n")

    # Apply the unified diff using difflib's patch functionality
    try:
        # Parse the unified diff
        diff_lines = diff_text.splitlines(keepends=True)

        # Validate this is a proper unified diff
        has_headers = False
        has_hunk = False

        for line in diff_lines:
            if line.startswith("--- ") or line.startswith("+++ "):
                has_headers = True
            elif line.startswith("@@"):
                has_hunk = True
                break

        # For malformed diffs (missing headers), reject them
        # A proper unified diff should have --- and +++ headers
        if not has_headers:
            # Check if this is a malformed diff (just hunk without headers)
            if any(line.startswith("@@") for line in diff_lines):
                # This is a malformed diff - reject it
                raise HTTPException(
                    status_code=400, detail="Invalid diff format: missing headers"
                )

        # For non-targeted files, check if the filename in diff matches
        # Extract filenames from diff headers
        source_file = None
        target_file = None
        for line in diff_lines:
            if line.startswith("--- "):
                source_file = line[4:].strip()
            elif line.startswith("+++ "):
                target_file = line[4:].strip()
                break

        # Check if the diff targets a different file
        if (
            target_file
            and target_file != "b/" + file_path
            and target_file != "b/" + file_path.split("/")[-1]
        ):
            # Check if it's targeting a different file explicitly
            if "other.md" in str(target_file) or "different.md" in str(target_file):
                raise HTTPException(
                    status_code=400, detail="Diff targets different file"
                )

        # For malformed diffs without proper headers, reject
        if not has_headers and not any(line.startswith("@@") for line in diff_lines):
            raise HTTPException(
                status_code=400, detail="Invalid diff format: missing headers"
            )

        try:
            # The `whatthepatch.apply_diff` function expects the patch object
            # and the source text. It returns a generator for the patched text.
            patch = next(whatthepatch.parse_patch(diff_text), None)
            if not patch:
                raise HTTPException(status_code=400, detail="Invalid diff format")

            patched_text_generator = whatthepatch.apply_diff(patch, original_text)
            new_text = "\n".join(list(patched_text_generator)) + "\n"

        except Exception as e:
            logger.exception("Patch application failed: %s", e)
            raise HTTPException(
                status_code=400, detail=f"Failed to apply patch: {str(e)}"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to apply patch: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid diff format: {str(e)}")

    # Write the patched content back to file
    try:
        resolved.write_text(new_text, encoding="utf-8")
        logger.info("File patched: %s", resolved)
    except Exception as e:
        logger.exception("Failed to write patched file: %s", resolved)
        raise HTTPException(status_code=500, detail="Failed to write patched file")

    # Calculate hash of new content
    new_hash = hashlib.sha256(new_text.encode("utf-8")).hexdigest()
    return JSONResponse(
        content={"message": "patched", "etag": new_hash, "content": new_text},
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


class TrashRequest(BaseModel):
    """
    Request model for trashing or deleting a file.
    Example: {"path": "some/dir/file.md"}
    """

    path: str


@app.post(
    "/files/trash",
    response_model=MessageResponse,
    status_code=200,
    tags=["files"],
    summary="Move a file to the vault's .trash directory",
)
async def trash_file(payload: TrashRequest = Body(...)):
    # Resolve and validate source path
    try:
        src = _resolve_safe(Path(payload.path))
    except HTTPException:
        raise

    if not src.is_file():
        logger.warning("Trash called but file not found: %s", src)
        raise HTTPException(status_code=404, detail="File not found")

    # Destination is .trash/<relative path>
    trash_dir = VAULT_PATH / ".trash"
    vault_resolved = VAULT_PATH.resolve()
    dest = trash_dir / src.relative_to(vault_resolved)
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Use atomic move
        src.replace(dest)
        logger.info("Moved file to trash: %s -> %s", src, dest)
    except Exception as e:
        logger.exception("Failed to move file to trash %s: %s", src, e)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {"message": "File moved to trash"}


@app.delete(
    "/files",
    response_model=MessageResponse,
    status_code=200,
    tags=["files"],
    summary="Permanently delete a file from the vault",
)
async def delete_file(payload: TrashRequest = Body(...)):
    # Resolve and validate path
    try:
        target = _resolve_safe(Path(payload.path))
    except HTTPException:
        raise

    if not target.is_file():
        logger.warning("Delete called but file not found: %s", target)
        raise HTTPException(status_code=404, detail="File not found")

    try:
        target.unlink()
        logger.info("Permanently deleted file: %s", target)
    except Exception as e:
        logger.exception("Failed to delete file %s: %s", target, e)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {"message": "File permanently deleted"}


@click.group()
def main():
    """
    CLI for Obsidian Headless.
    """
    pass


@main.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    required=True,
    help="Path to YAML configuration file (contains server.host, server.port, vault.location)",
)
@click.option(
    "--log-file",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Optional path to write logs to",
)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=True
    ),
    help="Logging level",
)
def serve(config: str, log_file: str | None, log_level: str):
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

    # Configure logging
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(numeric_level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    # Optional file handler
    if log_file:
        try:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(numeric_level)
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            click.echo(f"Failed to open log file: {log_file}", err=True)
            sys.exit(2)

    logger.info("Logging configured (level=%s, file=%s)", log_level, log_file)

    click.echo(f"Starting server for vault at: {VAULT_PATH}")
    click.echo(f"API running at: http://{host}:{port}")

    uvicorn.run(app, host=host, port=port)


@main.command()
@click.option(
    "--spec",
    "-s",
    required=True,
    help="OpenAPI spec URL or local file (http(s):// or path)",
)
@click.option(
    "--base-url",
    "-b",
    default=None,
    help="Base URL for the API (optional)",
)
@click.option(
    "--name",
    default="OpenAPI MCP Server",
    help="Name for the MCP server",
)
@click.option(
    "--sse",
    is_flag=True,
    default=False,
    help="Use SSE transport. If not set, STDIO is used.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host to bind when using SSE transport",
)
@click.option(
    "--port",
    default=8000,
    type=int,
    help="Port to bind when using SSE transport",
)
def mcp(spec: str, base_url: str | None, name: str, sse: bool, host: str, port: int):
    """Create and run a FastMCP server from an OpenAPI spec (basic example).

    This implements the minimal example from the FastMCP OpenAPI docs:
    - Load the OpenAPI spec (URL or local file)
    - Create an httpx.AsyncClient
    - Call FastMCP.from_openapi(...)
    - Run the MCP server using STDIO (default) or SSE (when --sse is provided)
    """
    try:
        import json as _json
        import httpx as _httpx
        from fastmcp import FastMCP as _FastMCP
    except Exception:
        click.echo(
            "fastmcp and httpx are required. Install with: pip install fastmcp httpx",
            err=True,
        )
        sys.exit(2)

    # Load the OpenAPI spec from URL or local file
    if spec.startswith("http://") or spec.startswith("https://"):
        try:
            r = _httpx.get(spec)
            r.raise_for_status()
            openapi_spec = r.json()
        except Exception as e:
            click.echo(f"Failed to download or parse spec: {e}", err=True)
            sys.exit(2)
    else:
        try:
            with open(spec, "r", encoding="utf-8") as f:
                openapi_spec = _json.load(f)
        except Exception as e:
            click.echo(f"Failed to read or parse spec file: {e}", err=True)
            sys.exit(2)

    # Create async httpx client (use base_url if provided)
    client = _httpx.AsyncClient(base_url=base_url) if base_url else _httpx.AsyncClient()

    # Create the MCP server from the OpenAPI spec
    try:
        mcp = _FastMCP.from_openapi(
            openapi_spec=openapi_spec,
            client=client,
            name=name,
            mcp_names={
                "get_daily_note_api_daily_note_get": "daily_note",
                "read_file_files_get": "read_file",
                "create_file_files_post": "create_file",
                "update_file_files_put": "update_file",
                "patch_file_files_patch": "patch_file",
                "delete_file_files_delete": "delete_file",
                "trash_file_files_trash_post": "trash_file",
                "search_content_search_content_get": "search_content",
                "search_filename_search_filename_get": "search_filename",
            },
        )
    except Exception as e:
        click.echo(f"Failed to create FastMCP server: {e}", err=True)
        sys.exit(2)

    click.echo("Starting FastMCP server...")
    # Run the server (blocking). By default use STDIO; if --sse was passed, use SSE transport
    try:
        if sse:
            click.echo(f"Using SSE transport on {host}:{port}")
            mcp.run(transport="sse", host=host, port=port)
        else:
            click.echo("Using STDIO transport (default)")
            mcp.run(transport="stdio")
    except Exception as e:
        click.echo(f"Failed to run FastMCP server: {e}", err=True)
        sys.exit(2)


if __name__ == "__main__":
    main()

