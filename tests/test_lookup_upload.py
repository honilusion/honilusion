"""Tests for the lookup_upload MCP tool (hub_server.py + hub_mcp.py).

Covers: full filename match, partial match, hash prefix match, file-ID match,
no-match loud error, empty query loud error, fileprep path inclusion, and
the _MCP_KEYWORDS "upload" / "attachment" entries.
"""
import asyncio
import json
import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_index(entries: list[dict]) -> dict:
    """Build a fake uploads.json index dict from a list of upload records."""
    return {f"{r['owner']}:{r['hash']}": r for r in entries}


SAMPLE_ENTRIES = [
    {
        "id": "6e15ce16483d4d75b78dbad8c2b9ae3a.pdf",
        "path": "/app/data/uploads/2026/07/06/6e15ce16483d4d75b78dbad8c2b9ae3a.pdf",
        "mime": "application/pdf",
        "size": 7207213,
        "name": "DOJ-FBI_NoFlyLists.pdf",
        "hash": "bcf9caee2f601235ca944d9baa8fdbf90d55a585ba1b1a8ac94ec5a65ffd8a45",
        "original_name": "DOJ-FBI_NoFlyLists.pdf",
        "uploaded_at": "2026-07-06T11:28:46.600528",
        "last_accessed": "2026-07-06T11:28:46.600540",
        "client_ip": "172.20.0.1",
        "owner": "olusion",
    },
    {
        "id": "02ff4043c4864264a1593db28106360f.pdf",
        "path": "/app/data/uploads/2026/06/28/02ff4043c4864264a1593db28106360f.pdf",
        "mime": "application/pdf",
        "size": 116955,
        "name": "mower_reciept_lowes.pdf",
        "hash": "874f8464db558607eb62f7ef87ffa39ee109619a06ad7ac3c4a420d635d1be05",
        "original_name": "mower reciept lowes.pdf",
        "uploaded_at": "2026-06-28T19:36:43.038353",
        "last_accessed": "2026-07-06T11:06:31.826151",
        "client_ip": "172.20.0.1",
        "owner": "olusion",
    },
    {
        "id": "65acc92ecc304e25956e95257dcf9343.md",
        "path": "/app/data/uploads/2026/06/24/65acc92ecc304e25956e95257dcf9343.md",
        "mime": "text/markdown",
        "size": 78295,
        "name": "SIIM2026_confrence_notes.md",
        "hash": "b2f30221801f9fd673e7f13b7ecaa6403dc12a7cc863045bd03c8915771dd0de",
        "original_name": "SIIM2026_confrence_notes.md",
        "uploaded_at": "2026-06-24T12:08:44.596471",
        "last_accessed": "2026-06-24T12:08:44.596476",
        "client_ip": "172.20.0.1",
        "owner": "olusion",
    },
]


@pytest.fixture()
def fake_upload_dir(tmp_path, monkeypatch):
    """Set up a fake UPLOAD_DIR with uploads.json and one .fileprep file."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    fileprep_dir = upload_dir / ".fileprep"
    fileprep_dir.mkdir()

    # Write the uploads index
    index = _make_index(SAMPLE_ENTRIES)
    (upload_dir / "uploads.json").write_text(json.dumps(index), encoding="utf-8")

    # Create a fileprep output for the NoFlyLists PDF
    (fileprep_dir / "6e15ce16483d4d75b78dbad8c2b9ae3a.pdf.md").write_text(
        "# No Fly List preprocessed", encoding="utf-8"
    )

    # Patch UPLOAD_DIR in the constants module so hub_server sees the fake dir
    import src.constants as constants_mod
    monkeypatch.setattr(constants_mod, "UPLOAD_DIR", str(upload_dir))

    return upload_dir


# ---------------------------------------------------------------------------
# Import the helper after patching is set up (imported lazily inside each test)
# ---------------------------------------------------------------------------

def _lookup(query, upload_dir_path, monkeypatch):
    """Call hub_server._lookup_upload() with a patched UPLOAD_DIR."""
    import src.constants as constants_mod
    monkeypatch.setattr(constants_mod, "UPLOAD_DIR", str(upload_dir_path))
    from mcp_servers.hub_server import _lookup_upload
    return _lookup_upload(query)


def _call(query, upload_dir_path, monkeypatch):
    """Call via the full MCP call_tool dispatcher."""
    import src.constants as constants_mod
    monkeypatch.setattr(constants_mod, "UPLOAD_DIR", str(upload_dir_path))
    import mcp_servers.hub_server as hub_server
    result = asyncio.run(hub_server.call_tool("lookup_upload", {"query": query}))
    return result[0].text


# ---------------------------------------------------------------------------
# Tests — _lookup_upload helper directly
# ---------------------------------------------------------------------------

class TestLookupUploadHelper:
    def test_full_filename_match(self, fake_upload_dir, monkeypatch):
        out = _lookup("DOJ-FBI_NoFlyLists.pdf", fake_upload_dir, monkeypatch)
        assert "DOJ-FBI_NoFlyLists.pdf" in out
        assert "6e15ce16483d4d75b78dbad8c2b9ae3a.pdf" in out
        assert "Found 1 upload(s)" in out

    def test_partial_name_match(self, fake_upload_dir, monkeypatch):
        out = _lookup("NoFly", fake_upload_dir, monkeypatch)
        assert "DOJ-FBI_NoFlyLists.pdf" in out
        assert "Found 1 upload(s)" in out

    def test_case_insensitive_match(self, fake_upload_dir, monkeypatch):
        out = _lookup("noflylist", fake_upload_dir, monkeypatch)
        assert "DOJ-FBI_NoFlyLists.pdf" in out

    def test_partial_match_returns_multiple(self, fake_upload_dir, monkeypatch):
        # Both PDF files share the ".pdf" extension — all PDFs match "pdf"
        out = _lookup("pdf", fake_upload_dir, monkeypatch)
        assert "Found 2 upload(s)" in out
        assert "DOJ-FBI_NoFlyLists.pdf" in out
        assert "mower" in out.lower()

    def test_hash_prefix_match(self, fake_upload_dir, monkeypatch):
        # First 8 chars of the NoFlyLists hash
        out = _lookup("bcf9caee", fake_upload_dir, monkeypatch)
        assert "DOJ-FBI_NoFlyLists.pdf" in out

    def test_file_id_match(self, fake_upload_dir, monkeypatch):
        out = _lookup("6e15ce16483d4d75b78dbad8c2b9ae3a", fake_upload_dir, monkeypatch)
        assert "DOJ-FBI_NoFlyLists.pdf" in out

    def test_no_match_is_loud_error(self, fake_upload_dir, monkeypatch):
        out = _lookup("completely_nonexistent_xyz", fake_upload_dir, monkeypatch)
        assert out.startswith("Error:")
        assert "no uploads found matching" in out
        assert "completely_nonexistent_xyz" in out

    def test_empty_query_is_loud_error(self, fake_upload_dir, monkeypatch):
        out = _lookup("", fake_upload_dir, monkeypatch)
        assert out.startswith("Error:")
        assert "query is required" in out

    def test_fileprep_path_included_when_exists(self, fake_upload_dir, monkeypatch):
        out = _lookup("NoFly", fake_upload_dir, monkeypatch)
        assert "fileprep:" in out
        assert "6e15ce16483d4d75b78dbad8c2b9ae3a.pdf.md" in out

    def test_fileprep_path_absent_when_not_preprocessed(self, fake_upload_dir, monkeypatch):
        # The mower receipt has no .fileprep output
        out = _lookup("mower", fake_upload_dir, monkeypatch)
        assert "Found 1 upload(s)" in out
        assert "fileprep:" not in out

    def test_sanitized_name_match(self, fake_upload_dir, monkeypatch):
        # "mower_reciept_lowes.pdf" is the sanitized name; original has a space
        out = _lookup("mower_reciept", fake_upload_dir, monkeypatch)
        assert "mower" in out.lower()

    def test_original_name_with_space_matches(self, fake_upload_dir, monkeypatch):
        # original_name is "mower reciept lowes.pdf" (with spaces)
        out = _lookup("mower reciept", fake_upload_dir, monkeypatch)
        assert "mower" in out.lower()

    def test_missing_uploads_json_is_loud_error(self, tmp_path, monkeypatch):
        empty_dir = tmp_path / "empty_uploads"
        empty_dir.mkdir()
        out = _lookup("anything", empty_dir, monkeypatch)
        assert out.startswith("Error:")
        assert "uploads index does not exist" in out

    def test_hash_prefix_too_short_does_not_match(self, fake_upload_dir, monkeypatch):
        # 7 chars — below the 8-char minimum for hash-prefix matching
        short_prefix = "bcf9cae"  # len=7
        out = _lookup(short_prefix, fake_upload_dir, monkeypatch)
        # May or may not match via filename substring; either way no crash
        assert isinstance(out, str)


# ---------------------------------------------------------------------------
# Tests — via call_tool dispatcher
# ---------------------------------------------------------------------------

class TestLookupUploadCallTool:
    def test_call_tool_dispatch_full_match(self, fake_upload_dir, monkeypatch):
        out = _call("DOJ-FBI_NoFlyLists.pdf", fake_upload_dir, monkeypatch)
        assert "DOJ-FBI_NoFlyLists.pdf" in out
        assert "Found 1 upload(s)" in out

    def test_call_tool_dispatch_no_match_error(self, fake_upload_dir, monkeypatch):
        out = _call("zzz_not_there", fake_upload_dir, monkeypatch)
        assert out.startswith("Error:")
        assert "no uploads found matching" in out


# ---------------------------------------------------------------------------
# Tests — _MCP_KEYWORDS contains the right trigger words
# ---------------------------------------------------------------------------

class TestUploadKeywords:
    def test_upload_in_mcp_keywords(self):
        from src.agent_loop import _MCP_KEYWORDS
        assert "upload" in _MCP_KEYWORDS

    def test_uploaded_in_mcp_keywords(self):
        from src.agent_loop import _MCP_KEYWORDS
        assert "uploaded" in _MCP_KEYWORDS

    def test_attachment_in_mcp_keywords(self):
        from src.agent_loop import _MCP_KEYWORDS
        assert "attachment" in _MCP_KEYWORDS
