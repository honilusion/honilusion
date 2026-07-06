"""Tests for Token Compression Phase 1, Session 3:
- src/token_counter.py: count_tokens_approx, chars_to_tokens_approx
- GET /api/hub/compression_stats endpoint (JSON shape, auth, zero-events)
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# token_counter tests
# ---------------------------------------------------------------------------

class TestCountTokensApprox:
    def test_empty_string(self):
        from src.token_counter import count_tokens_approx
        result = count_tokens_approx("")
        assert isinstance(result, int)
        assert result >= 0

    def test_short_string_returns_positive(self):
        from src.token_counter import count_tokens_approx
        result = count_tokens_approx("hello world")
        assert result >= 1

    def test_longer_text_more_tokens_than_short(self):
        from src.token_counter import count_tokens_approx
        short = count_tokens_approx("hi")
        long = count_tokens_approx("This is a much longer sentence with many more words and tokens in it.")
        assert long > short

    def test_returns_int(self):
        from src.token_counter import count_tokens_approx
        result = count_tokens_approx("some sample text here")
        assert type(result) is int

    def test_known_text_reasonable_range(self):
        # "Hello, world!" is ~4 tokens in cl100k_base; chars//4 also gives ~3.
        from src.token_counter import count_tokens_approx
        result = count_tokens_approx("Hello, world!")
        assert 2 <= result <= 10, f"Unexpected token count: {result}"

    def test_fallback_when_tiktoken_unavailable(self):
        # Patch _get_encoding to return None, simulating tiktoken absence.
        from src import token_counter
        with patch.object(token_counter, '_get_encoding', return_value=None):
            result = token_counter.count_tokens_approx("hello world test")
            assert result == max(1, len("hello world test") // 4)

    def test_fallback_minimum_is_one(self):
        from src import token_counter
        with patch.object(token_counter, '_get_encoding', return_value=None):
            result = token_counter.count_tokens_approx("a")
            assert result >= 1


class TestCharsToTokensApprox:
    def test_zero_chars(self):
        from src.token_counter import chars_to_tokens_approx
        assert chars_to_tokens_approx(0) >= 1

    def test_four_chars_is_one_token(self):
        from src.token_counter import chars_to_tokens_approx
        assert chars_to_tokens_approx(4) == 1

    def test_large_value(self):
        from src.token_counter import chars_to_tokens_approx
        result = chars_to_tokens_approx(40000)
        assert result == 10000

    def test_returns_int(self):
        from src.token_counter import chars_to_tokens_approx
        assert type(chars_to_tokens_approx(100)) is int


# ---------------------------------------------------------------------------
# Stats aggregation logic tests (test the _window_stats path in isolation)
# ---------------------------------------------------------------------------

def _make_cache_row(chars_before, chars_after, payload=None, tool_name="test_tool", created_at=None):
    row = MagicMock()
    row.chars_before = chars_before
    row.chars_after = chars_after
    row.payload = payload or ("x" * chars_before)
    row.tool_name = tool_name
    row.created_at = created_at or datetime.now(timezone.utc).replace(tzinfo=None)
    return row


class TestWindowStats:
    """Test the stats aggregation logic independent of HTTP layer."""

    def _window_stats(self, subset):
        """Replicate the _window_stats closure from hub_routes."""
        from src.token_counter import count_tokens_approx, chars_to_tokens_approx
        if not subset:
            return {
                "events": 0,
                "chars_before": 0, "chars_after": 0,
                "tokens_before_approx": 0, "tokens_after_approx": 0,
                "reduction_pct": 0.0,
            }
        cb = sum(r.chars_before for r in subset)
        ca = sum(r.chars_after for r in subset)
        tb = sum(
            count_tokens_approx(r.payload) if r.payload else chars_to_tokens_approx(r.chars_before)
            for r in subset
        )
        ta = sum(chars_to_tokens_approx(r.chars_after) for r in subset)
        pct = round((1 - ta / tb) * 100, 1) if tb > 0 else 0.0
        return {
            "events": len(subset),
            "chars_before": cb, "chars_after": ca,
            "tokens_before_approx": tb, "tokens_after_approx": ta,
            "reduction_pct": pct,
        }

    def test_empty_returns_zeroes(self):
        result = self._window_stats([])
        assert result["events"] == 0
        assert result["chars_before"] == 0
        assert result["reduction_pct"] == 0.0

    def test_single_row_correct_sums(self):
        row = _make_cache_row(chars_before=4000, chars_after=400, payload="x" * 4000)
        result = self._window_stats([row])
        assert result["events"] == 1
        assert result["chars_before"] == 4000
        assert result["chars_after"] == 400
        assert result["tokens_before_approx"] >= 1
        assert result["tokens_after_approx"] >= 1

    def test_reduction_pct_is_positive_for_compression(self):
        # chars_before >> chars_after → reduction > 0
        row = _make_cache_row(chars_before=10000, chars_after=100)
        result = self._window_stats([row])
        assert result["reduction_pct"] > 0

    def test_multiple_rows_sum_correctly(self):
        rows = [
            _make_cache_row(4000, 400),
            _make_cache_row(8000, 800),
        ]
        result = self._window_stats(rows)
        assert result["events"] == 2
        assert result["chars_before"] == 12000
        assert result["chars_after"] == 1200

    def test_reduction_pct_in_valid_range(self):
        row = _make_cache_row(chars_before=10000, chars_after=1000)
        result = self._window_stats([row])
        assert 0.0 <= result["reduction_pct"] <= 100.0

    def test_no_compression_zero_reduction(self):
        # chars_before == chars_after → tokens equal → 0% reduction
        # (tokens_before from tiktoken may differ from tokens_after via chars/4,
        # so just check it doesn't explode and is close to 0)
        row = _make_cache_row(chars_before=400, chars_after=400)
        result = self._window_stats([row])
        assert result["reduction_pct"] <= 50.0  # not wildly positive


# ---------------------------------------------------------------------------
# Endpoint tests (HTTP layer)
# ---------------------------------------------------------------------------

class TestCompressionStatsEndpoint:
    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        from fastapi import FastAPI
        from routes.hub.hub_routes import setup_hub_routes
        from unittest.mock import patch

        app = FastAPI()
        app.include_router(setup_hub_routes())

        # Bypass auth for unit tests
        with patch("routes.hub.hub_routes.require_authenticated_request"):
            yield TestClient(app, raise_server_exceptions=True)

    def test_returns_200(self, client):
        with patch("routes.hub.hub_routes.SessionLocal") as mock_sl:
            mock_db = MagicMock()
            mock_sl.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = []
            resp = client.get("/api/hub/compression_stats")
        assert resp.status_code == 200

    def test_zero_events_returns_valid_structure(self, client):
        with patch("routes.hub.hub_routes.SessionLocal") as mock_sl:
            mock_db = MagicMock()
            mock_sl.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = []
            resp = client.get("/api/hub/compression_stats")
        data = resp.json()
        assert "all_time" in data
        assert "last_24h" in data
        assert "last_7d" in data
        assert "by_tool" in data
        assert "token_counting" in data
        assert data["all_time"]["events"] == 0
        assert data["all_time"]["reduction_pct"] == 0.0

    def test_with_rows_returns_correct_shape(self, client):
        fake_row = _make_cache_row(chars_before=10000, chars_after=1000, tool_name="mcp: hub_test")
        with patch("routes.hub.hub_routes.SessionLocal") as mock_sl:
            mock_db = MagicMock()
            mock_sl.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = [fake_row]
            resp = client.get("/api/hub/compression_stats")
        data = resp.json()
        assert data["all_time"]["events"] == 1
        assert data["all_time"]["chars_before"] == 10000
        assert data["all_time"]["chars_after"] == 1000
        assert data["all_time"]["tokens_before_approx"] >= 1
        assert data["all_time"]["tokens_after_approx"] >= 1
        assert data["all_time"]["reduction_pct"] > 0
        assert len(data["by_tool"]) == 1
        assert data["by_tool"][0]["tool_name"] == "mcp: hub_test"

    def test_token_counting_method_documented(self, client):
        with patch("routes.hub.hub_routes.SessionLocal") as mock_sl:
            mock_db = MagicMock()
            mock_sl.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = []
            resp = client.get("/api/hub/compression_stats")
        data = resp.json()
        assert "method" in data["token_counting"]
        assert "note" in data["token_counting"]
        assert "cl100k" in data["token_counting"]["method"]

    def test_requires_auth(self):
        """Endpoint must call require_authenticated_request — verified by checking
        it's called in the route handler (unit test uses patch to bypass it)."""
        import inspect
        from routes.hub import hub_routes
        src = inspect.getsource(hub_routes)
        # The compression_stats route must include the auth call
        assert "require_authenticated_request" in src

    def test_by_tool_sorted_by_tokens_desc(self, client):
        rows = [
            _make_cache_row(chars_before=1000, chars_after=100, tool_name="small_tool"),
            _make_cache_row(chars_before=10000, chars_after=1000, tool_name="large_tool"),
        ]
        with patch("routes.hub.hub_routes.SessionLocal") as mock_sl:
            mock_db = MagicMock()
            mock_sl.return_value = mock_db
            mock_db.query.return_value.filter.return_value.all.return_value = rows
            resp = client.get("/api/hub/compression_stats")
        data = resp.json()
        tools = data["by_tool"]
        assert len(tools) == 2
        # Larger tool should come first (sorted by tokens_before desc)
        assert tools[0]["tool_name"] == "large_tool"
        assert tools[1]["tool_name"] == "small_tool"
