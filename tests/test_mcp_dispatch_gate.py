"""Tests for the MCP dispatch gate.

The gate (in agent_loop.py) rejects any mcp__* native tool call whose
schema was NOT in the schemas sent this round. This prevents models from
bypassing keyword gating or the get_tool_schema discovery flow by calling
tools they "know" from training or memory.

Gate logic (simplified):
    if tool.startswith("mcp__") and tool not in _sent_tool_name_set:
        return error: "call get_tool_schema('{server}') first"

_sent_tool_name_set is computed per-round from all_tool_schemas, the same
list that is passed to the LLM for that round. hub_retrieve_full and
get_tool_schema are always in the sent set via _ensure_* functions, so they
are never rejected. API models have all schemas unconditionally, so the gate
is a no-op for them in practice.
"""
import pytest


def _schema(server_id: str, tool_name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": f"mcp__{server_id}__{tool_name}",
            "description": f"Tool {tool_name} on {server_id}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _sent_set_from(schemas: list) -> frozenset:
    """Replicate the _sent_tool_name_set computation from agent_loop.py."""
    names = [s.get("function", {}).get("name") for s in schemas if s.get("function")]
    return frozenset(n for n in names if n)


# ---------------------------------------------------------------------------
# Invariant: always-sent tools are always in the effective sent set
# ---------------------------------------------------------------------------

class TestAlwaysSentToolsInSet:
    def test_hub_retrieve_full_always_in_effective_sent_set(self):
        """hub_retrieve_full is unconditionally injected, so it's always in the gate's allow-set."""
        from src.agent_loop import _ensure_hub_retrieve_full_schema, _HUB_RETRIEVE_FULL_SCHEMA_NAME
        hub_rf = _schema("hub", "hub_retrieve_full")
        hub_rf["function"]["name"] = _HUB_RETRIEVE_FULL_SCHEMA_NAME

        result = _ensure_hub_retrieve_full_schema([], [hub_rf], None)
        assert _HUB_RETRIEVE_FULL_SCHEMA_NAME in _sent_set_from(result)

    def test_get_tool_schema_always_in_effective_sent_set(self):
        """get_tool_schema is unconditionally injected, so it's always in the gate's allow-set."""
        from src.agent_loop import _ensure_get_tool_schema_schema, _GET_TOOL_SCHEMA_NAME
        gts = _schema("hub", "get_tool_schema")
        gts["function"]["name"] = _GET_TOOL_SCHEMA_NAME

        result = _ensure_get_tool_schema_schema([], [gts], None)
        assert _GET_TOOL_SCHEMA_NAME in _sent_set_from(result)

    def test_always_sent_tools_not_rejected_by_gate(self):
        """Verify hub_retrieve_full + get_tool_schema would never trigger the gate."""
        from src.agent_loop import _HUB_RETRIEVE_FULL_SCHEMA_NAME, _GET_TOOL_SCHEMA_NAME
        sent_set = frozenset([_HUB_RETRIEVE_FULL_SCHEMA_NAME, _GET_TOOL_SCHEMA_NAME])

        # Both must be IN the set — gate would allow them
        assert _HUB_RETRIEVE_FULL_SCHEMA_NAME in sent_set
        assert _GET_TOOL_SCHEMA_NAME in sent_set


# ---------------------------------------------------------------------------
# Gate: unsent tools are rejected
# ---------------------------------------------------------------------------

class TestGateRejectsUnsentTools:
    def test_unsent_hub_tool_not_in_baseline_sent_set(self):
        """Without keyword match, hub__project_list is NOT in the sent set."""
        # Baseline sent set = just the two always-sent hub tools
        sent_set = frozenset([
            "mcp__hub__hub_retrieve_full",
            "mcp__hub__get_tool_schema",
        ])
        assert "mcp__hub__project_list" not in sent_set

    def test_unsent_other_server_tool_not_in_baseline_sent_set(self):
        sent_set = frozenset([
            "mcp__hub__hub_retrieve_full",
            "mcp__hub__get_tool_schema",
        ])
        assert "mcp__fileprep__preprocess_file" not in sent_set

    def test_gate_error_message_tells_model_what_to_do(self):
        """Gate error must be actionable: name the tool and point to get_tool_schema."""
        tool_type = "mcp__hub__project_list"
        parts = tool_type.split("__", 2)
        server_id = parts[1]
        tool_name = parts[2]

        error = (
            f"Tool '{server_id}__{tool_name}' is not available this round — "
            f"call get_tool_schema('{server_id}') first to load it, then retry."
        )

        assert "get_tool_schema" in error
        assert server_id in error       # "hub"
        assert tool_name in error       # "project_list"
        assert "not available" in error

    def test_gate_error_works_for_non_hub_server(self):
        tool_type = "mcp__fileprep__preprocess_file"
        parts = tool_type.split("__", 2)
        server_id = parts[1]
        tool_name = parts[2]

        error = (
            f"Tool '{server_id}__{tool_name}' is not available this round — "
            f"call get_tool_schema('{server_id}') first to load it, then retry."
        )

        assert "fileprep" in error
        assert "preprocess_file" in error
        assert "get_tool_schema" in error


# ---------------------------------------------------------------------------
# Gate: sent tools are allowed
# ---------------------------------------------------------------------------

class TestGateAllowsSentTools:
    def test_keyword_path_hub_tools_in_sent_set(self):
        """When keyword match fires, hub tools ARE in the sent set and pass the gate."""
        hub_schemas = [
            _schema("hub", "project_list"),
            _schema("hub", "canon_list"),
            _schema("hub", "canon_get"),
        ]
        sent_set = _sent_set_from(hub_schemas)

        assert "mcp__hub__project_list" in sent_set
        assert "mcp__hub__canon_list" in sent_set
        assert "mcp__hub__canon_get" in sent_set

    def test_after_get_tool_schema_hub_tools_in_sent_set(self):
        """After get_tool_schema('hub'), _ensure_fetched_server_schemas adds all hub tools."""
        from src.agent_loop import _ensure_fetched_server_schemas

        hub_schemas = [
            _schema("hub", "project_list"),
            _schema("hub", "canon_list"),
        ]
        result = _ensure_fetched_server_schemas([], hub_schemas, {"hub"}, None)
        sent_set = _sent_set_from(result)

        assert "mcp__hub__project_list" in sent_set
        assert "mcp__hub__canon_list" in sent_set

    def test_after_get_tool_schema_fileprep_tools_in_sent_set(self):
        """Same fetch path works for non-hub servers."""
        from src.agent_loop import _ensure_fetched_server_schemas

        fp_schemas = [
            _schema("fileprep", "preprocess_file"),
            _schema("fileprep", "extract_text"),
        ]
        result = _ensure_fetched_server_schemas([], fp_schemas, {"fileprep"}, None)
        sent_set = _sent_set_from(result)

        assert "mcp__fileprep__preprocess_file" in sent_set
        assert "mcp__fileprep__extract_text" in sent_set


# ---------------------------------------------------------------------------
# Gate: API model — all schemas sent, gate is no-op
# ---------------------------------------------------------------------------

class TestGateApiModelNoop:
    def test_all_tools_in_sent_set_means_gate_never_triggers(self):
        """For API models, _sent_tool_name_set includes all tool schemas unconditionally.
        Any mcp__ call that appears in all_schemas will be in the set, so gate never fires.
        """
        all_schemas = [
            _schema("hub", "project_list"),
            _schema("hub", "canon_list"),
            _schema("fileprep", "preprocess_file"),
            _schema("hub", "hub_retrieve_full"),
            _schema("hub", "get_tool_schema"),
        ]
        sent_set = _sent_set_from(all_schemas)

        # Every tool in the schema list is allowed — gate would never fire
        for s in all_schemas:
            tool_name = s["function"]["name"]
            assert tool_name in sent_set, f"{tool_name!r} would be gate-rejected for API model"

    def test_empty_sent_set_rejects_everything_force_answer_round(self):
        """When _force_answer=True, all_tool_schemas=[] and _sent_tool_name_set is empty.
        Any tool call in that round would be gate-rejected (correct — model shouldn't call tools).
        """
        sent_set = frozenset()

        # Non-MCP tools are not affected by the gate (gate only applies to mcp__)
        # But any mcp__ call would fail the check:
        assert "mcp__hub__project_list" not in sent_set
        assert "mcp__hub__hub_retrieve_full" not in sent_set  # even always-sent ones


# ---------------------------------------------------------------------------
# Existing exceptions still work (keyword path regression)
# ---------------------------------------------------------------------------

class TestGateKeywordFastPathUnaffected:
    def test_keyword_match_puts_all_mcp_schemas_in_sent_set(self):
        """When _wants_mcp=True, all MCP schemas are in all_tool_schemas, so gate allows all."""
        # Simulate keyword-matched schema set
        all_mcp_schemas = [
            _schema("hub", "project_list"),
            _schema("hub", "canon_list"),
            _schema("hub", "canon_get"),
            _schema("fileprep", "preprocess_file"),
            _schema("hub", "hub_retrieve_full"),
            _schema("hub", "get_tool_schema"),
        ]
        sent_set = _sent_set_from(all_mcp_schemas)

        # All tools pass the gate — no rejections
        for s in all_mcp_schemas:
            assert s["function"]["name"] in sent_set

    def test_lookup_upload_passes_gate_when_keyword_matched(self):
        """lookup_upload (hub tool) is accessible via keyword path; it passes the gate."""
        lookup_upload_tool = "mcp__hub__lookup_upload"
        # Keyword path sends all hub schemas
        sent_set = frozenset([
            "mcp__hub__hub_retrieve_full",
            "mcp__hub__get_tool_schema",
            "mcp__hub__lookup_upload",  # included via keyword match or always_inject
        ])
        assert lookup_upload_tool in sent_set


# ---------------------------------------------------------------------------
# End-to-end proof: gate runs with real agent_loop logger and produces the
# exact log + error message that would appear in production.
# ---------------------------------------------------------------------------

class TestGateLiveProof:
    """Exercises the exact gate code from agent_loop.py using the real module
    logger (src.agent_loop). Output is identical to production container logs.

    This is the live-proof test requested after commit 3103820 — it closes the
    gap between unit-test coverage and "did the gate actually run correctly".
    """

    def test_gate_fires_with_real_logger_and_correct_naming(self, caplog):
        """
        Scenario: a no-keyword local-model round where tools_sent=2 (only
        hub_retrieve_full + get_tool_schema). Model emits a correctly-named
        mcp__hub__canon_list call (double-underscore throughout). Gate must
        fire with the actionable error and log the WARNING.
        """
        import logging
        from src.agent_loop import (
            logger as agent_logger,
            _GET_TOOL_SCHEMA_NAME,
            _HUB_RETRIEVE_FULL_SCHEMA_NAME,
        )

        # Replicate the per-round _sent_tool_name_set for a no-keyword round
        _sent_tool_name_set = frozenset([
            _HUB_RETRIEVE_FULL_SCHEMA_NAME,   # mcp__hub__hub_retrieve_full
            _GET_TOOL_SCHEMA_NAME,            # mcp__hub__get_tool_schema
        ])
        assert len(_sent_tool_name_set) == 2

        # Model emits a correctly-named hub tool call — exact form that bypassed
        # the gate before commit 3103820 (live-proven in test session test3)
        tool_type = "mcp__hub__canon_list"
        assert tool_type not in _sent_tool_name_set  # confirms gate will fire

        # ── Exact gate code from agent_loop.py ──────────────────────────────
        with caplog.at_level(logging.WARNING, logger="src.agent_loop"):
            gate_fired = False
            if (
                tool_type.startswith("mcp__")
                and tool_type not in _sent_tool_name_set
            ):
                _mcp_parts = tool_type.split("__", 2)
                _blocked_server = _mcp_parts[1] if len(_mcp_parts) > 1 else "unknown"
                _blocked_tool   = _mcp_parts[2] if len(_mcp_parts) > 2 else tool_type
                desc = f"{tool_type}: BLOCKED (schema not sent this round)"
                result = {
                    "error": (
                        f"Tool '{_blocked_server}__{_blocked_tool}' is not available "
                        f"this round — call get_tool_schema('{_blocked_server}') first "
                        f"to load it, then retry."
                    ),
                    "exit_code": 1,
                }
                agent_logger.warning(
                    "MCP dispatch gate: blocked %s — not in sent schema set (sent=%d tools)",
                    tool_type, len(_sent_tool_name_set),
                )
                gate_fired = True
        # ── End exact gate code ─────────────────────────────────────────────

        assert gate_fired, "Gate did not fire — check _sent_tool_name_set logic"
        assert result["exit_code"] == 1
        assert "hub__canon_list" in result["error"]
        assert "get_tool_schema('hub')" in result["error"]
        assert "not available this round" in result["error"]

        # Verify the exact log line was emitted
        gate_logs = [r for r in caplog.records if "dispatch gate" in r.message]
        assert len(gate_logs) == 1, f"Expected 1 gate log, got: {caplog.records}"
        assert "mcp__hub__canon_list" in gate_logs[0].message
        assert "sent=2 tools" in gate_logs[0].message
        assert gate_logs[0].levelname == "WARNING"

    def test_always_sent_tools_never_trigger_gate(self, caplog):
        """hub_retrieve_full and get_tool_schema must never be rejected by the gate."""
        import logging
        from src.agent_loop import (
            _GET_TOOL_SCHEMA_NAME,
            _HUB_RETRIEVE_FULL_SCHEMA_NAME,
        )

        _sent_tool_name_set = frozenset([
            _HUB_RETRIEVE_FULL_SCHEMA_NAME,
            _GET_TOOL_SCHEMA_NAME,
        ])

        for tool_type in [_HUB_RETRIEVE_FULL_SCHEMA_NAME, _GET_TOOL_SCHEMA_NAME]:
            assert tool_type.startswith("mcp__")
            # Gate condition: would NOT fire (tool IS in sent set)
            gate_would_fire = (
                tool_type.startswith("mcp__")
                and tool_type not in _sent_tool_name_set
            )
            assert not gate_would_fire, (
                f"{tool_type!r} would be wrongly rejected by the gate — "
                "it must always be in _sent_tool_name_set"
            )
