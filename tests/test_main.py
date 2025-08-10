import pytest
from fastapi.testclient import TestClient
from pathlib import Path
import shutil
from datetime import datetime
from main import app, VAULT_PATH

# Create a test vault for the tests
TEST_VAULT_PATH = Path("test_vault_for_tests")


import yaml


@pytest.fixture(scope="session")
def setup_test_vault():
    """Create a test vault with some files before tests run, and clean up after."""
    TEST_VAULT_PATH.mkdir(exist_ok=True)

    # Set the global VAULT_PATH in the main module
    # This is a bit of a hack, but necessary for the current structure
    import main

    main.VAULT_PATH = TEST_VAULT_PATH

    # Create a dummy config file for tests
    config_content = """
    daily_note:
      location: "daily/{now:%Y}/{now:%Y-%m-%d}.md"
    """
    config_path = TEST_VAULT_PATH / "config.yaml"
    config_path.write_text(config_content)
    with open(config_path, "r") as f:
        main.CONFIG = yaml.safe_load(f)

    (TEST_VAULT_PATH / "test_note.md").write_text("This is a test note.")
    (TEST_VAULT_PATH / "another_note.md").write_text("This is another note.")
    (TEST_VAULT_PATH / "folder").mkdir(exist_ok=True)
    (TEST_VAULT_PATH / "folder/nested_note.md").write_text("This is a nested note.")

    yield

    shutil.rmtree(TEST_VAULT_PATH)


client = TestClient(app)


def test_read_file(setup_test_vault):
    response = client.request("GET", "/files", json={"path": "test_note.md"})
    assert response.status_code == 200
    assert response.json() == "This is a test note."


def test_read_nested_file(setup_test_vault):
    response = client.request("GET", "/files", json={"path": "folder/nested_note.md"})
    assert response.status_code == 200
    assert response.json() == "This is a nested note."


def test_read_file_not_found(setup_test_vault):
    response = client.request("GET", "/files", json={"path": "non_existent_note.md"})
    assert response.status_code == 404


def test_create_file(setup_test_vault):
    payload = {"path": "new_note.md", "content": "This is a new note."}
    response = client.post("/files", json=payload)
    assert response.status_code == 200
    assert (TEST_VAULT_PATH / "new_note.md").is_file()
    assert (TEST_VAULT_PATH / "new_note.md").read_text() == "This is a new note."


def test_create_file_already_exists(setup_test_vault):
    payload = {"path": "test_note.md", "content": "This should fail."}
    response = client.post("/files", json=payload)
    assert response.status_code == 400


def test_update_file(setup_test_vault):
    payload = {"path": "test_note.md", "content": "This is an updated note."}
    response = client.put("/files", json=payload)
    assert response.status_code == 200
    assert (TEST_VAULT_PATH / "test_note.md").read_text() == "This is an updated note."


def test_update_file_not_found(setup_test_vault):
    payload = {"path": "non_existent_note.md", "content": "This should fail."}
    response = client.put("/files", json=payload)
    assert response.status_code == 404


def test_search_filename(setup_test_vault):
    response = client.get("/search/filename?q=test")
    assert response.status_code == 200
    assert "test_note.md" in response.json()


def test_search_content(setup_test_vault):
    response = client.get("/search/content?q=note")
    assert response.status_code == 200
    assert "test_note.md" in response.json()
    assert "another_note.md" in response.json()
    assert "folder/nested_note.md" in response.json()


def test_daily_note_path_generation(setup_test_vault):
    config_content = """
    daily_note:
      location: "daily/{now:%Y}/{now:%Y-%m-%d}.md"
    """
    config_path = TEST_VAULT_PATH / "config.yaml"
    config_path.write_text(config_content)
    import main

    with open(config_path, "r") as f:
        main.CONFIG = yaml.safe_load(f)
    main.VAULT_PATH = TEST_VAULT_PATH

    now = datetime.now()
    expected_path = f"daily/{now.year}/{now.year}-{now.month:02}-{now.day:02}.md"

    formatter = main.SafeFormatter()
    location_template = main.CONFIG.get("daily_note", {}).get(
        "location", "daily/{now:%Y}/{now:%Y-%m-%d}.md"
    )
    file_name = formatter.format(location_template, now=now)

    assert file_name == expected_path


# --- PATCH tests added for diff and If-Match support ---
import difflib
import hashlib


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_patch_diff_applies(setup_test_vault):
    original = "line1\nline2\n"
    new = "line1\nline2\nline3 added\n"
    p = TEST_VAULT_PATH / "patch_note.md"
    p.write_text(original)

    # Use unified diff format instead of diff
    d = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/patch_note.md",
            tofile="b/patch_note.md",
            lineterm="\n",
        )
    )
    resp = client.patch("/files", json={"path": "patch_note.md", "diff": d})
    assert resp.status_code == 200
    assert p.read_text() == new
    assert "etag" in resp.json()
    assert resp.headers.get("ETag") == resp.json()["etag"]


def test_patch_diff_applies_without_check(setup_test_vault):
    original = "old content\n"
    new = "new content\n"
    p = TEST_VAULT_PATH / "if_note.md"
    p.write_text(original)
    # Use unified diff format instead of diff
    d = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/if_note.md",
            tofile="b/if_note.md",
            lineterm="\n",
        )
    )
    resp = client.patch("/files", json={"path": "if_note.md", "diff": d})
    assert resp.status_code == 200
    assert p.read_text() == new
    assert resp.json()["etag"] == _sha256(new)


def test_patch_not_found(setup_test_vault):
    # Use unified diff format
    d = "".join(
        difflib.unified_diff(
            ["x\n"],
            ["y\n"],
            fromfile="a/nonexistent_patch.md",
            tofile="b/nonexistent_patch.md",
            lineterm="\n",
        )
    )
    resp = client.patch("/files", json={"path": "nonexistent_patch.md", "diff": d})
    assert resp.status_code == 404


def test_patch_path_traversal_forbidden(setup_test_vault):
    # Use unified diff format
    d = "".join(
        difflib.unified_diff(
            ["x\n"],
            ["y\n"],
            fromfile="a/outside.md",
            tofile="b/outside.md",
            lineterm="\n",
        )
    )
    resp = client.patch("/files", json={"path": "../outside.md", "diff": d})
    assert resp.status_code == 400


def test_patch_handles_diff_without_keepends(setup_test_vault):
    # This test is no longer relevant for unified diff format
    # Unified diff always includes proper line endings in the format
    # We'll test that malformed unified diffs are rejected
    original = "a\nb\n"
    new = "a\nb\nc added\n"
    p = TEST_VAULT_PATH / "no_keepends.md"
    p.write_text(original)

    # Create a malformed diff (missing headers)
    d = "@@ -1,2 +1,3 @@\n a\n b\n+c added"
    resp = client.patch("/files", json={"path": "no_keepends.md", "diff": d})
    # Server should reject malformed unified diffs
    assert resp.status_code == 400
    # File should be left unchanged when patch is rejected
    assert p.read_text() == original


def test_patch_handles_escaped_newlines_and_mixed_payload(setup_test_vault):
    # Simulate a payload where newlines are escaped (\\n) or mixed with real newlines
    original = "one\ntwo\n"
    new = "one\ntwo\nthree added\n"
    p = TEST_VAULT_PATH / "mixed_escape.md"
    p.write_text(original)

    # Proper unified diff but then JSON-escaped (simulating a buggy client)
    proper_d = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/mixed_escape.md",
            tofile="b/mixed_escape.md",
            lineterm="\n",
        )
    )
    # Fully escape newlines to simulate JSON escaping
    escaped_d = proper_d.replace("\n", "\\n")

    resp = client.patch("/files", json={"path": "mixed_escape.md", "diff": escaped_d})
    assert resp.status_code == 200
    assert p.read_text() == new


def test_patch_handles_crlf_variants(setup_test_vault):
    # Ensure CRLF line endings from Windows clients are handled
    original = "r1\r\nr2\r\n"
    # Create new content with an added line
    new = "r1\nr2\nr3 added\n"
    p = TEST_VAULT_PATH / "crlf.md"
    p.write_text(original)

    # Create unified diff - use LF for the diff itself
    d = "".join(
        difflib.unified_diff(
            original.replace("\r\n", "\n").splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/crlf.md",
            tofile="b/crlf.md",
            lineterm="\n",
        )
    )
    resp = client.patch("/files", json={"path": "crlf.md", "diff": d})
    assert resp.status_code == 200
    # The patched file should have the new content
    assert p.read_text() == new


# --- Trash and Delete endpoint tests ---
def test_trash_file_moves_to_trash(setup_test_vault):
    p = TEST_VAULT_PATH / "to_trash.md"
    p.write_text("delete me\n")

    resp = client.post("/files/trash", json={"path": "to_trash.md"})
    assert resp.status_code == 200
    # File should no longer exist at original location
    assert not p.exists()
    # Should exist under .trash/to_trash.md
    trash_path = TEST_VAULT_PATH / ".trash" / "to_trash.md"
    assert trash_path.is_file()
    assert trash_path.read_text() == "delete me\n"


def test_delete_file_permanently_removes(setup_test_vault):
    p = TEST_VAULT_PATH / "to_delete.md"
    p.write_text("remove me\n")

    resp = client.request("DELETE", "/files", json={"path": "to_delete.md"})
    assert resp.status_code == 200
    assert not p.exists()
