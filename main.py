import click
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pathlib import Path
import os
from datetime import datetime

import yaml
from string import Formatter

app = FastAPI()

# This will be set by the CLI command
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
@click.argument(
    "vault_dir", type=click.Path(exists=True, file_okay=False, resolve_path=True)
)
@click.option("--host", default="127.0.0.1", help="Host to bind the server to.")
@click.option("--port", default=8000, help="Port to bind the server to.")
def main(vault_dir: str, host: str, port: int):
    """
    Run the FastAPI server for the Obsidian vault.
    """
    global VAULT_PATH, CONFIG
    VAULT_PATH = Path(vault_dir)

    config_path = VAULT_PATH / "config.yaml"
    if config_path.is_file():
        with open(config_path, "r") as f:
            CONFIG = yaml.safe_load(f)

    click.echo(f"Starting server for vault at: {VAULT_PATH}")
    click.echo(f"API running at: http://{host}:{port}")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
