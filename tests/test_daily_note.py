import pytest
from fastapi.testclient import TestClient
from pathlib import Path
import shutil
from datetime import datetime
from main import app, VAULT_PATH

# Create a test vault for the tests
TEST_VAULT_PATH = Path("test_vault_for_daily_note_tests")

import yaml

@pytest.fixture(scope="session", autouse=True)
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
  location: "daily_notes/{now:%Y-%m-%d}.md"
"""
    config_path = TEST_VAULT_PATH / "config.yaml"
    config_path.write_text(config_content)
    with open(config_path, "r") as f:
        main.CONFIG = yaml.safe_load(f)

    yield

    shutil.rmtree(TEST_VAULT_PATH)


client = TestClient(app)


def test_get_daily_note_creates_new_note():
    # Ensure the note does not exist before the test
    today_str = datetime.now().strftime("%Y-%m-%d")
    note_path = TEST_VAULT_PATH / f"daily_notes/{today_str}.md"
    if note_path.exists():
        note_path.unlink()

    response = client.get("/api/daily-note")
    assert response.status_code == 200
    assert response.json() == ""  # New note should be empty
    assert note_path.exists()


def test_get_daily_note_returns_existing_note():
    today_str = datetime.now().strftime("%Y-%m-%d")
    note_path = TEST_VAULT_PATH / f"daily_notes/{today_str}.md"
    note_content = "This is a test daily note."
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(note_content)

    response = client.get("/api/daily-note")
    assert response.status_code == 200
    assert response.json() == note_content