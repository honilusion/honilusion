"""Token Compression Session 2 — the compressor wired into format_tool_result.

Covers the pure functions in src/tool_output_compressor.py directly
(detect_array_of_objects, row_crush, is_prose_oversized) and the
compress_tool_result orchestrator with store_tool_output mocked out (its own
behavior — TTL sweep, 1MB cap, ref_id format — is already covered by
tests/test_hub_tool_output_cache.py from Session 1; here we only need to
verify the orchestrator calls it correctly and reacts to its return value).
"""
import json

import pytest

from src.tool_output_compressor import (
    detect_array_of_objects,
    row_crush,
    is_prose_oversized,
    compress_tool_result,
    ROW_CRUSH_TRIGGER_CHARS,
    PROSE_TRIGGER_CHARS,
    PROSE_EXCERPT_CHARS,
)


# ---------------------------------------------------------------------------
# detect_array_of_objects
# ---------------------------------------------------------------------------

class TestDetectArrayOfObjects:
    def test_uniform_array(self):
        rows = [{"id": i, "name": f"item{i}"} for i in range(10)]
        assert detect_array_of_objects(rows) == rows

    def test_non_uniform_array_rejected(self):
        # Every row has a completely different key -- no key-set covers
        # >=80% of rows, so this isn't "similar objects".
        rows = [{"a": 1}, {"b": 2}, {"c": 3}, {"d": 4}, {"e": 5}]
        assert detect_array_of_objects(rows) is None

    def test_mostly_uniform_array_accepted(self):
        # 9/10 rows share the same key-set (90% >= 80% threshold); one
        # outlier row shouldn't disqualify the whole array.
        rows = [{"id": i, "name": f"item{i}"} for i in range(9)]
        rows.append({"totally": "different", "shape": True})
        assert detect_array_of_objects(rows) == rows

    def test_empty_array(self):
        assert detect_array_of_objects([]) is None

    def test_array_of_non_dicts_rejected(self):
        assert detect_array_of_objects([1, 2, 3, "four"]) is None

    def test_nested_array_under_a_key(self):
        rows = [{"id": i} for i in range(5)]
        data = {"response": "Found 5 things", "documents": rows, "exit_code": 0}
        assert detect_array_of_objects(data) == rows

    def test_top_level_non_array(self):
        assert detect_array_of_objects("just a plain string") is None
        assert detect_array_of_objects(42) is None
        assert detect_array_of_objects(None) is None

    def test_dict_with_no_qualifying_list(self):
        data = {"response": "hi", "count": 5, "tags": ["a", "b", "c"]}
        # "tags" is a list but not of dicts -- doesn't qualify.
        assert detect_array_of_objects(data) is None


# ---------------------------------------------------------------------------
# row_crush
# ---------------------------------------------------------------------------

class TestRowCrush:
    def test_exact_keep_counts_on_long_array(self):
        rows = list(range(100))
        kept, omitted = row_crush(rows, keep_first=5, keep_last=3, sample_mid=3)
        assert kept[:5] == [0, 1, 2, 3, 4]
        assert kept[-3:] == [97, 98, 99]
        assert len(kept) == 11  # 5 + 3 middle samples + 3
        assert omitted == 100 - 11

    def test_shorter_than_keep_counts_unchanged(self):
        rows = list(range(5))
        kept, omitted = row_crush(rows, keep_first=5, keep_last=3, sample_mid=3)
        assert kept == rows
        assert omitted == 0

    def test_exactly_at_keep_first_plus_keep_last_unchanged(self):
        rows = list(range(8))  # keep_first(5) + keep_last(3) == 8
        kept, omitted = row_crush(rows, keep_first=5, keep_last=3, sample_mid=3)
        assert kept == rows
        assert omitted == 0

    def test_one_over_threshold_omits_middle(self):
        rows = list(range(9))  # one more than keep_first+keep_last
        kept, omitted = row_crush(rows, keep_first=5, keep_last=3, sample_mid=3)
        assert omitted >= 0
        assert kept[:5] == [0, 1, 2, 3, 4]
        assert kept[-3:] == [6, 7, 8]

    def test_never_negative_omitted_count(self):
        for n in range(0, 15):
            _, omitted = row_crush(list(range(n)), keep_first=5, keep_last=3, sample_mid=3)
            assert omitted >= 0

    def test_empty_array(self):
        kept, omitted = row_crush([], keep_first=5, keep_last=3, sample_mid=3)
        assert kept == []
        assert omitted == 0


# ---------------------------------------------------------------------------
# is_prose_oversized
# ---------------------------------------------------------------------------

class TestIsProseOversized:
    def test_exactly_at_threshold_not_oversized(self):
        assert is_prose_oversized("x" * PROSE_TRIGGER_CHARS) is False

    def test_one_over_threshold_is_oversized(self):
        assert is_prose_oversized("x" * (PROSE_TRIGGER_CHARS + 1)) is True

    def test_well_under_threshold(self):
        assert is_prose_oversized("short text") is False


# ---------------------------------------------------------------------------
# compress_tool_result
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_store_and_handled_keys(monkeypatch):
    """compress_tool_result lazily imports store_tool_output and
    _FORMATTER_HANDLED_KEYS at call time -- patch the real module attributes
    so the lazy `from X import Y` picks up the mocks."""
    import core.tool_output_cache as cache_mod
    import src.tool_execution as te_mod

    calls = []

    def _fake_store(payload, tool_name, session_id, chars_before=None, chars_after=None):
        calls.append({
            "payload": payload, "tool_name": tool_name, "session_id": session_id,
            "chars_before": chars_before, "chars_after": chars_after,
        })
        return "tc-deadbeef"

    monkeypatch.setattr(cache_mod, "store_tool_output", _fake_store)
    return calls


class TestCompressToolResult:
    def test_api_model_passthrough_unchanged(self, _mock_store_and_handled_keys):
        result = {"response": "x" * 10000, "documents": [{"id": i} for i in range(50)]}
        out = compress_tool_result(result, is_api_model=True, tool_name="manage_documents")
        assert out is result  # untouched, not even copied
        assert _mock_store_and_handled_keys == []  # store_tool_output never called

    def test_local_model_small_payload_passthrough(self, _mock_store_and_handled_keys):
        result = {"response": "small", "documents": [{"id": 1}, {"id": 2}]}
        out = compress_tool_result(result, is_api_model=False, tool_name="manage_documents")
        assert out == result
        assert _mock_store_and_handled_keys == []

    def test_local_model_big_array_row_crushed(self, _mock_store_and_handled_keys):
        rows = [{"id": i, "name": f"item-{i}", "status": "done"} for i in range(200)]
        assert len(json.dumps(rows)) > ROW_CRUSH_TRIGGER_CHARS
        result = {"response": "Found 200 things", "documents": rows, "exit_code": 0}
        out = compress_tool_result(result, is_api_model=False, tool_name="manage_documents", session_id="sess-1")
        assert out is not result  # copied since it was modified
        crushed = out["documents"]
        assert len(crushed) < len(rows)
        marker_entries = [r for r in crushed if isinstance(r, str)]
        assert len(marker_entries) == 1
        assert "hub_retrieve_full" in marker_entries[0]
        assert "tc-deadbeef" in marker_entries[0]
        assert "similar rows omitted" in marker_entries[0]
        # store_tool_output was called with the FULL original array
        assert len(_mock_store_and_handled_keys) == 1
        call = _mock_store_and_handled_keys[0]
        assert call["tool_name"] == "manage_documents"
        assert call["session_id"] == "sess-1"
        assert json.loads(call["payload"]) == rows
        assert call["chars_before"] == len(json.dumps(rows, default=str, ensure_ascii=False))
        # response text (a _FORMATTER_HANDLED_KEYS key) is untouched
        assert out["response"] == "Found 200 things"

    def test_local_model_big_prose_excerpted(self, _mock_store_and_handled_keys):
        big_text = "word " * 2000  # well over PROSE_TRIGGER_CHARS
        result = {"stdout": big_text, "stderr": "", "exit_code": 0}
        out = compress_tool_result(result, is_api_model=False, tool_name="bash", session_id=None)
        assert out is not result
        assert out["stdout"] != big_text
        assert out["stdout"].startswith("[showing first 1000 chars")
        assert "tc-deadbeef" in out["stdout"]
        assert out["stdout"].endswith(big_text[:PROSE_EXCERPT_CHARS])
        assert len(_mock_store_and_handled_keys) == 1
        assert _mock_store_and_handled_keys[0]["payload"] == big_text
        assert _mock_store_and_handled_keys[0]["chars_before"] == len(big_text)

    def test_ref_id_in_marker_matches_cache_call(self, _mock_store_and_handled_keys):
        rows = [{"id": i, "v": "x" * 50} for i in range(100)]
        result = {"response": "ok", "documents": rows}
        out = compress_tool_result(result, is_api_model=False, tool_name="t", session_id=None)
        marker = next(r for r in out["documents"] if isinstance(r, str))
        assert "tc-deadbeef" in marker  # the ref_id our fake store_tool_output returns

    def test_small_array_under_trigger_passthrough(self, _mock_store_and_handled_keys):
        rows = [{"id": i} for i in range(5)]  # well under 2000 chars dumped
        result = {"response": "ok", "documents": rows}
        out = compress_tool_result(result, is_api_model=False, tool_name="t", session_id=None)
        assert out["documents"] == rows
        assert _mock_store_and_handled_keys == []

    def test_non_dict_result_passthrough(self, _mock_store_and_handled_keys):
        assert compress_tool_result("a plain string", is_api_model=False, tool_name="t") == "a plain string"
        assert compress_tool_result(None, is_api_model=False, tool_name="t") is None


# ---------------------------------------------------------------------------
# Fail-safe: format_tool_result must never break a tool round
# ---------------------------------------------------------------------------

class TestFormatToolResultFailSafe:
    def test_forced_compressor_exception_falls_back_to_original(self, monkeypatch):
        import src.tool_execution as te

        def _boom(*args, **kwargs):
            raise RuntimeError("forced failure for fail-safe test")

        monkeypatch.setattr("src.tool_output_compressor.compress_tool_result", _boom)

        result = {"output": "some ordinary tool output", "exit_code": 0}
        formatted = te.format_tool_result("some_tool", result, is_api_model=False, session_id="s1")
        # The round completes normally with the original, uncompressed content.
        assert "some ordinary tool output" in formatted
        assert "### some_tool" in formatted

    def test_no_exception_path_still_works_normally(self):
        import src.tool_execution as te
        result = {"output": "hello", "exit_code": 0}
        formatted = te.format_tool_result("echo", result, is_api_model=True)
        assert "hello" in formatted
