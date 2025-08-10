import hashlib
from fastapi.testclient import TestClient
import difflib
from pathlib import Path
import json

import obsidian_headless.main as main

client = TestClient(main.app)


def make_unified_diff(a_lines, b_lines, path="file.md"):
    # difflib.unified_diff returns an iterator of lines
    return "".join(
        difflib.unified_diff(
            a_lines, b_lines, fromfile="a/" + path, tofile="b/" + path, lineterm="\n"
        )
    )


def test_patch_applies_unified_diff(tmp_path):
    # Arrange: create a small file in a temporary vault and point server at it
    vault = tmp_path / "vault"
    vault.mkdir()
    main.VAULT_PATH = vault

    target = vault / "notes.md"
    original = "# Title\n\nLine one\nLine two\n"
    target.write_text(original, encoding="utf-8")

    # Create new content with a small change
    new = "# Title\n\nLine one modified\nLine two\n"

    diff_text = make_unified_diff(
        original.splitlines(keepends=True),
        new.splitlines(keepends=True),
        path="notes.md",
    )

    # Act: call patch endpoint
    resp = client.patch("/files", json={"path": "notes.md", "diff": diff_text})

    # Assert
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["message"] == "patched"

    final = target.read_text(encoding="utf-8")
    assert final == new
    # etag matches content
    expected_hash = hashlib.sha256(new.encode("utf-8")).hexdigest()
    assert resp.headers.get("etag") == expected_hash


def test_patch_rejects_non_targeted_file(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    main.VAULT_PATH = vault

    target = vault / "a.md"
    target.write_text("A\nB\nC\n", encoding="utf-8")

    # create a diff that targets a different file
    new = "A\nB changed\nC\n"
    diff_text = make_unified_diff(
        "A\nB\nC\n".splitlines(keepends=True),
        new.splitlines(keepends=True),
        path="other.md",
    )

    resp = client.patch("/files", json={"path": "a.md", "diff": diff_text})
    assert resp.status_code == 400


def test_patch_handles_json_escaped_newlines(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    main.VAULT_PATH = vault

    target = vault / "doc.md"
    original = "Foo\nBar\nBaz\n"
    target.write_text(original, encoding="utf-8")

    new = "Foo\nBar updated\nBaz\n"
    diff_text = make_unified_diff(
        original.splitlines(keepends=True), new.splitlines(keepends=True), path="doc.md"
    )

    # simulate a client that JSON-escapes newlines (literal \n sequences)
    escaped = diff_text.replace("\n", "\\n")

    resp = client.patch("/files", json={"path": "doc.md", "diff": escaped})
    assert resp.status_code == 200, resp.text
    assert target.read_text(encoding="utf-8") == new


def test_patch_prevents_content_duplication(tmp_path):
    """Test that patch doesn't create duplicate content when applying changes."""
    vault = tmp_path / "vault"
    vault.mkdir()
    main.VAULT_PATH = vault

    target = vault / "test.md"
    original = "Line 1\nLine 2\nLine 3\n"
    target.write_text(original, encoding="utf-8")

    new = "Line 1\nLine 2 modified\nLine 3\n"
    diff_text = make_unified_diff(
        original.splitlines(keepends=True),
        new.splitlines(keepends=True),
        path="test.md",
    )

    resp = client.patch("/files", json={"path": "test.md", "diff": diff_text})
    assert resp.status_code == 200, resp.text

    final = target.read_text(encoding="utf-8")
    
    # Ensure no duplicate lines
    lines = final.splitlines()
    assert len(lines) == len(set(lines)), "Should not have duplicate lines"
    assert final == new, "Should match expected result exactly"
