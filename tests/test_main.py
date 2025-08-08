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
