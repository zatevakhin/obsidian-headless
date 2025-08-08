import click
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pathlib import Path
import os
import sys
from datetime import datetime

import yaml
from string import Formatter

app = FastAPI()

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


@app.get("/api/daily-note")
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
        full_path.touch()
    return JSONResponse(content=full_path.read_text())


@app.get("/files/{file_path:path}")
def read_file(file_path: str):
    # TODO: Add security checks to prevent directory traversal
    full_path = VAULT_PATH / file_path
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return JSONResponse(content=full_path.read_text())


@app.post("/files/{file_path:path}")
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


@app.put("/files/{file_path:path}")
async def update_file(file_path: str, request: Request):
    # TODO: Add security checks
    # TODO: Add PATCH endpoint with difflib support for partial updates
    content = await request.body()
    full_path = VAULT_PATH / file_path
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    full_path.write_text(content.decode())
    return {"message": "File updated successfully"}


@app.get("/search/content")
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


@app.get("/search/filename")
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
        click.echo("Config file must contain 'server' and 'vault.location' keys", err=True)
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
