# obsidian-headless

A tiny API + MCP to manage notes in an Obsidian-style vault without the Obsidian app.

## What it does
- Runs a FastAPI server exposing endpoints to `read`/`create`/`update`/`patch`/`delete` files in a local vault.
- Can generate a "daily note" from a Jinja2 template when it doesn't exist.
- Provides a small MCP wrapper to expose the API via FastMCP.

## Quick usage

1) Install for development:

```bash
pip install -e .
```

2) Run the CLI to serve the API:

```bash
obsidian-headless serve -c path/to/config.yaml
```

   The config must include 'server' and 'vault.location' keys. Example:

```yaml
server:
  host: 127.0.0.1
  port: 8000
vault:
  location: ./vault
  daily_note:
    template: templates/daily_note.md.jinja
```

3) Call the API (examples):

```
GET /api/daily-note      -> returns (and creates if absent) today's note
POST /files              -> create a file (JSON body: {"path":..., "content":...})
PUT /files               -> replace file contents
PATCH /files             -> apply unified diff to a file
POST /files/trash        -> move file to .trash
DELETE /files            -> permanently delete file
```

## MCP tools

This project exposes the following MCP tools via FastMCP (available when the MCP server is running):

- `obsidian_read_file`: Read file contents from the vault
- `obsidian_update_file`: Replace file contents in the vault
- `obsidian_create_file`: Create a new file in the vault
- `obsidian_delete_file`: Permanently delete a file from the vault
- `obsidian_patch_file`: Apply a unified diff patch to a file in the vault
- `obsidian_search_content`: Search file contents in the vault
- `obsidian_search_filename`: Search filenames in the vault
- `obsidian_trash_file`: Move a file to the vault's .trash directory
- `obsidian_daily_note`: Get or create today's daily note


## Development

- Run tests:

```bash
PYTHONPATH=. pytest -q
```

- Lint checks:

```bash
ruff check .
```

## License

See LICENSE in the project root.
