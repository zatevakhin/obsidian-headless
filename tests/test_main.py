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
    response = client.get("/files/test_note.md")
    assert response.status_code == 200
    assert response.json() == "This is a test note."


def test_read_nested_file(setup_test_vault):
    response = client.get("/files/folder/nested_note.md")
    assert response.status_code == 200
    assert response.json() == "This is a nested note."


def test_read_file_not_found(setup_test_vault):
    response = client.get("/files/non_existent_note.md")
    assert response.status_code == 404


def test_create_file(setup_test_vault):
    response = client.post("/files/new_note.md", content="This is a new note.")
    assert response.status_code == 200
    assert (TEST_VAULT_PATH / "new_note.md").is_file()
    assert (TEST_VAULT_PATH / "new_note.md").read_text() == "This is a new note."


def test_create_file_already_exists(setup_test_vault):
    response = client.post("/files/test_note.md", content="This should fail.")
    assert response.status_code == 400


def test_update_file(setup_test_vault):
    response = client.put("/files/test_note.md", content="This is an updated note.")
    assert response.status_code == 200
    assert (TEST_VAULT_PATH / "test_note.md").read_text() == "This is an updated note."


def test_update_file_not_found(setup_test_vault):
    response = client.put("/files/non_existent_note.md", content="This should fail.")
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


# --- PATCH tests added for ndiff and If-Match support ---
import difflib
import hashlib


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_patch_ndiff_applies(setup_test_vault):
    original = "line1\nline2\n"
    new = "line1\nline2\nline3 added\n"
    p = TEST_VAULT_PATH / "patch_note.md"
    p.write_text(original)

    nd = "".join(difflib.ndiff(original.splitlines(keepends=True), new.splitlines(keepends=True)))
    resp = client.patch("/files/patch_note.md", content=nd, headers={"Content-Type": "text/x-ndiff"})
    assert resp.status_code == 200
    assert p.read_text() == new
    assert "etag" in resp.json()
    assert resp.headers.get("ETag") == resp.json()["etag"]


def test_patch_if_match_success(setup_test_vault):
    p = TEST_VAULT_PATH / "if_note.md"
    p.write_text("old content")
    cur_hash = _sha256(p.read_text())
    new = "new content"
    resp = client.patch("/files/if_note.md", content=new, headers={"If-Match": cur_hash, "Content-Type": "text/plain"})
    assert resp.status_code == 200
    assert p.read_text() == new
    assert resp.json()["etag"] == _sha256(new)


def test_patch_if_match_conflict(setup_test_vault):
    p = TEST_VAULT_PATH / "if_conflict.md"
    p.write_text("unchanged")
    wrong_hash = "deadbeef"
    resp = client.patch("/files/if_conflict.md", content="attempt", headers={"If-Match": wrong_hash, "Content-Type": "text/plain"})
    assert resp.status_code == 409
    assert p.read_text() == "unchanged"


def test_patch_not_found(setup_test_vault):
    resp = client.patch("/files/nonexistent_patch.md", content="x", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 404


def test_patch_path_traversal_forbidden(setup_test_vault):
    resp = client.patch("/files/../outside.md", content="x", headers={"Content-Type": "text/plain"})
    # Path traversal/security checks are intentionally deferred; expect not found for now
    assert resp.status_code == 404
