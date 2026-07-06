"""Tests for the MCP Tool Index + get_tool_schema feature.

Covers:
- _build_mcp_server_index() output format and content
- _ensure_get_tool_schema_schema() unconditional injection (dedup, disabled, absent)
- _ensure_fetched_server_schemas() injects schemas for fetched servers (dedup)
- _handle_get_tool_schema() happy path, unknown-server loud error, valid names list
- get_tool_schema Tool entry exists in hub_server.py list_tools()
- Keyword fast-path regression: keyword match bypasses get_tool_schema need
- _GET_TOOL_SCHEMA_NAME constant matches hub_server Tool entry name
"""
import json
import pytest

# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

def _schema(server_id: str, tool_name: str) -> dict:
    """Minimal OpenAI-format tool schema."""
    return {
        "type": "function",
        "function": {
            "name": f"mcp__{server_id}__{tool_name}",
            "description": f"Tool {tool_name} on server {server_id}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class _FakeMcpManager:
    """Minimal stub that satisfies _build_mcp_server_index and _handle_get_tool_schema."""

    def __init__(self, servers: dict, schemas: list):
        # servers: {server_id: {"status": "connected", "name": "Display Name"}}
        self._servers = servers
        self._schemas = schemas
        # _tools is used for tool count
        self._tools = {}
        for s in schemas:
            fn_name = s.get("function", {}).get("name", "")
            parts = fn_name.split("__", 2)
            if len(parts) >= 2:
                sid = parts[1]
                self._tools.setdefault(sid, []).append(s)

    def get_all_statuses(self):
        return self._servers

    def get_all_openai_schemas(self):
        return self._schemas


# ---------------------------------------------------------------------------
# _build_mcp_server_index
# ---------------------------------------------------------------------------

class TestBuildMcpServerIndex:
    def _index(self, servers, schemas=None):
        from src.agent_loop import _build_mcp_server_index
        mgr = _FakeMcpManager(servers, schemas or [])
        return _build_mcp_server_index(mgr)

    def test_returns_empty_on_none_manager(self):
        from src.agent_loop import _build_mcp_server_index
        assert _build_mcp_server_index(None) == ""

    def test_returns_empty_when_no_connected_servers(self):
        result = self._index({"hub": {"status": "disconnected", "name": "Hub"}})
        assert result == ""

    def test_contains_header(self):
        servers = {"hub": {"status": "connected", "name": "Built-in: Agent Hub"}}
        schemas = [_schema("hub", "inbox_send")]
        result = self._index(servers, schemas)
        assert "## Available MCP servers" in result

    def test_lists_each_connected_server(self):
        servers = {
            "hub": {"status": "connected", "name": "Built-in: Agent Hub"},
            "fileprep": {"status": "connected", "name": "fileprep"},
        }
        schemas = [_schema("hub", "inbox_send"), _schema("fileprep", "preprocess")]
        result = self._index(servers, schemas)
        assert "`hub`" in result
        assert "`fileprep`" in result

    def test_includes_tool_count(self):
        servers = {"hub": {"status": "connected", "name": "Hub"}}
        schemas = [_schema("hub", "tool_a"), _schema("hub", "tool_b")]
        result = self._index(servers, schemas)
        assert "(2 tools)" in result

    def test_uses_known_purpose_for_hub(self):
        from src.agent_loop import _MCP_SERVER_PURPOSES
        servers = {"hub": {"status": "connected", "name": "Hub"}}
        result = self._index(servers, [_schema("hub", "t")])
        assert _MCP_SERVER_PURPOSES["hub"][:20] in result

    def test_fallback_purpose_for_unknown_server(self):
        servers = {"custom_srv": {"status": "connected", "name": "My Custom Server"}}
        result = self._index(servers, [_schema("custom_srv", "t")])
        assert "My Custom Server" in result

    def test_disconnected_server_not_listed(self):
        servers = {
            "hub": {"status": "connected", "name": "Hub"},
            "broken": {"status": "error", "name": "Broken"},
        }
        result = self._index(servers, [_schema("hub", "t")])
        assert "`broken`" not in result

    def test_mentions_get_tool_schema_in_header(self):
        servers = {"hub": {"status": "connected", "name": "Hub"}}
        result = self._index(servers, [_schema("hub", "t")])
        assert "get_tool_schema" in result


# ---------------------------------------------------------------------------
# _ensure_get_tool_schema_schema
# ---------------------------------------------------------------------------

class TestEnsureGetToolSchemaSchema:
    def setup_method(self):
        from src.agent_loop import _GET_TOOL_SCHEMA_NAME
        self.gts_name = _GET_TOOL_SCHEMA_NAME
        self.gts_schema = _schema("hub", "get_tool_schema")
        # Patch the function name to match the constant
        self.gts_schema["function"]["name"] = _GET_TOOL_SCHEMA_NAME

    def _run(self, existing, mcp_schemas, disabled=None):
        from src.agent_loop import _ensure_get_tool_schema_schema
        return _ensure_get_tool_schema_schema(existing, mcp_schemas, disabled)

    def test_injects_when_not_present(self):
        result = self._run([], [self.gts_schema])
        names = [s["function"]["name"] for s in result]
        assert self.gts_name in names

    def test_noop_when_already_present(self):
        result = self._run([self.gts_schema], [self.gts_schema])
        assert len(result) == 1  # no duplicate

    def test_noop_when_absent_from_mcp_schemas(self):
        result = self._run([], [])
        assert result == []

    def test_noop_when_disabled(self):
        result = self._run([], [self.gts_schema], disabled={self.gts_name})
        assert result == []

    def test_does_not_duplicate_existing_tools(self):
        other = _schema("hub", "inbox_send")
        result = self._run([other], [other, self.gts_schema])
        names = [s["function"]["name"] for s in result]
        assert names.count("mcp__hub__inbox_send") == 1
        assert self.gts_name in names


# ---------------------------------------------------------------------------
# _ensure_fetched_server_schemas
# ---------------------------------------------------------------------------

class TestEnsureFetchedServerSchemas:
    def _run(self, existing, mcp_schemas, fetched, disabled=None):
        from src.agent_loop import _ensure_fetched_server_schemas
        return _ensure_fetched_server_schemas(existing, mcp_schemas, fetched, disabled)

    def test_injects_all_tools_for_fetched_server(self):
        schemas = [_schema("hub", "tool_a"), _schema("hub", "tool_b")]
        result = self._run([], schemas, {"hub"})
        names = {s["function"]["name"] for s in result}
        assert "mcp__hub__tool_a" in names
        assert "mcp__hub__tool_b" in names

    def test_noop_when_fetched_set_empty(self):
        schemas = [_schema("hub", "tool_a")]
        result = self._run([], schemas, set())
        assert result == []

    def test_deduplicates_existing_schemas(self):
        schema = _schema("hub", "tool_a")
        result = self._run([schema], [schema], {"hub"})
        assert len(result) == 1

    def test_does_not_inject_other_servers(self):
        hub_schema = _schema("hub", "tool_a")
        fp_schema = _schema("fileprep", "preprocess")
        result = self._run([], [hub_schema, fp_schema], {"hub"})
        names = {s["function"]["name"] for s in result}
        assert "mcp__hub__tool_a" in names
        assert "mcp__fileprep__preprocess" not in names

    def test_respects_disabled_tools(self):
        schema = _schema("hub", "tool_a")
        result = self._run([], [schema], {"hub"}, disabled={"mcp__hub__tool_a"})
        assert result == []

    def test_multiple_fetched_servers(self):
        hub_schema = _schema("hub", "tool_a")
        fp_schema = _schema("fileprep", "preprocess")
        result = self._run([], [hub_schema, fp_schema], {"hub", "fileprep"})
        names = {s["function"]["name"] for s in result}
        assert "mcp__hub__tool_a" in names
        assert "mcp__fileprep__preprocess" in names


# ---------------------------------------------------------------------------
# _handle_get_tool_schema
# ---------------------------------------------------------------------------

class TestHandleGetToolSchema:
    def _mgr(self, schemas):
        return _FakeMcpManager({}, schemas)

    def _run(self, server_name, schemas):
        from src.tool_execution import _handle_get_tool_schema
        return _handle_get_tool_schema(server_name, self._mgr(schemas))

    def test_happy_path_returns_schemas(self):
        schemas = [_schema("hub", "tool_a"), _schema("hub", "tool_b")]
        result = self._run("hub", schemas)
        assert result["exit_code"] == 0
        parsed = json.loads(result["content"])
        names = [s["function"]["name"] for s in parsed]
        assert "mcp__hub__tool_a" in names
        assert "mcp__hub__tool_b" in names

    def test_happy_path_excludes_other_servers(self):
        schemas = [_schema("hub", "tool_a"), _schema("fileprep", "preprocess")]
        result = self._run("hub", schemas)
        parsed = json.loads(result["content"])
        names = [s["function"]["name"] for s in parsed]
        assert all(n.startswith("mcp__hub__") for n in names)

    def test_unknown_server_is_loud_error(self):
        schemas = [_schema("hub", "tool_a")]
        result = self._run("nonexistent_server", schemas)
        assert result["exit_code"] == 1
        assert result["error"].startswith("Error:")
        assert "nonexistent_server" in result["error"]

    def test_unknown_server_lists_valid_names(self):
        schemas = [_schema("hub", "a"), _schema("fileprep", "b")]
        result = self._run("ghost", schemas)
        assert "hub" in result["error"]
        assert "fileprep" in result["error"]

    def test_empty_server_name_is_loud_error(self):
        schemas = [_schema("hub", "tool_a")]
        result = self._run("", schemas)
        assert result["exit_code"] == 1
        assert "server_name is required" in result["error"]

    def test_no_schemas_connected_error(self):
        result = self._run("hub", [])
        assert result["exit_code"] == 1
        assert "Error:" in result["error"]
        assert "none connected" in result["error"]

    def test_returns_valid_json_content(self):
        schemas = [_schema("hub", "tool_a")]
        result = self._run("hub", schemas)
        assert result["exit_code"] == 0
        parsed = json.loads(result["content"])
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# hub_server.py — get_tool_schema Tool entry
# ---------------------------------------------------------------------------

class TestHubServerGetToolSchemaEntry:
    def test_get_tool_schema_in_list_tools(self):
        src = open("mcp_servers/hub_server.py").read()
        assert 'name="get_tool_schema"' in src or "name='get_tool_schema'" in src, (
            "get_tool_schema Tool entry not found in mcp_servers/hub_server.py"
        )

    def test_get_tool_schema_name_constant_matches_hub_server_name(self):
        from src.agent_loop import _GET_TOOL_SCHEMA_NAME
        assert _GET_TOOL_SCHEMA_NAME == "mcp__hub__get_tool_schema"

    def test_get_tool_schema_schema_has_server_name_param(self):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-c",
             "import ast; src = open('mcp_servers/hub_server.py').read(); "
             "print(src)"],
            capture_output=True, text=True
        )
        assert '"server_name"' in result.stdout


# ---------------------------------------------------------------------------
# Keyword fast-path regression
# ---------------------------------------------------------------------------

class TestKeywordFastPathRegression:
    def test_mcp_keywords_still_present(self):
        from src.agent_loop import _MCP_KEYWORDS
        for kw in ("upload", "uploaded", "attachment", "tc-", "email", "browse", "pdf"):
            assert kw in _MCP_KEYWORDS, f"'{kw}' missing from _MCP_KEYWORDS"

    def test_hub_retrieve_full_name_constant_unchanged(self):
        from src.agent_loop import _HUB_RETRIEVE_FULL_SCHEMA_NAME
        assert _HUB_RETRIEVE_FULL_SCHEMA_NAME == "mcp__hub__hub_retrieve_full"
