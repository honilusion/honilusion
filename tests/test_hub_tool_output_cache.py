"""Agent Hub tool-output cache — store_tool_output() and hub_retrieve_full.

Covers the write path (core/tool_output_cache.py) and the MCP read path
(mcp_servers/hub_server.py's hub_retrieve_full dispatch): store/retrieve
round-trip, ref_id format, miss and malformed-ref loud errors, the 14-day
TTL sweep-on-write, and the 1 MB truncation flag.
"""
import asyncio
import re
from datetime import timedelta

import pytest

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb
from core.database import HubToolOutputCache
from core.tool_output_cache import store_tool_output, REF_PREFIX, MAX_PAYLOAD_CHARS
import mcp_servers.hub_server as hub_server

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    db = _TS()
    try:
        db.query(HubToolOutputCache).delete()
        db.commit()
    finally:
        db.close()
    yield


def _retrieve(ref_id):
    return asyncio.run(hub_server.call_tool("hub_retrieve_full", {"ref_id": ref_id}))[0].text


def test_ref_id_format():
    ref_id = store_tool_output("hello world", "some_tool", "sess-1")
    assert re.fullmatch(r"tc-[0-9a-f]{8}", ref_id)


def test_store_retrieve_round_trip():
    ref_id = store_tool_output("the payload contents", "my_tool", "sess-1")
    out = _retrieve(ref_id)
    assert "the payload contents" in out
    assert "my_tool" in out
    assert "Truncated: False" in out


def test_miss_returns_loud_error():
    out = _retrieve("tc-00000000")
    assert "unsupported" in out
    assert "not found" in out


def test_malformed_ref_returns_loud_error():
    for bad in ["not-a-ref", "tc-123", "tc-zzzzzzzz", "", "TC-abcd1234"]:
        out = _retrieve(bad)
        assert "unsupported" in out
        assert "malformed" in out


def test_truncation_flag_set_over_1mb():
    payload = "x" * (MAX_PAYLOAD_CHARS + 500)
    ref_id = store_tool_output(payload, "big_tool", None)
    db = _TS()
    try:
        row = db.query(HubToolOutputCache).filter(HubToolOutputCache.ref_id == ref_id).first()
        assert row.truncated is True
        assert row.size_chars == MAX_PAYLOAD_CHARS + 500
        assert len(row.payload) == MAX_PAYLOAD_CHARS
    finally:
        db.close()
    out = _retrieve(ref_id)
    assert "Truncated: True" in out
    assert "exceeded 1 MB" in out


def test_ttl_sweep_evicts_backdated_rows_on_write():
    db = _TS()
    try:
        old = HubToolOutputCache(
            ref_id=REF_PREFIX + "deadbeef",
            session_id=None,
            tool_name="stale_tool",
            payload="stale",
            size_chars=5,
            truncated=False,
            created_at=cdb.utcnow_naive() - timedelta(days=15),
        )
        db.add(old)
        db.commit()
    finally:
        db.close()

    store_tool_output("fresh payload", "fresh_tool", None)

    db = _TS()
    try:
        assert db.query(HubToolOutputCache).filter(
            HubToolOutputCache.ref_id == REF_PREFIX + "deadbeef"
        ).first() is None
    finally:
        db.close()
