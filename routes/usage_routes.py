"""Claude Code subscription rate-limit usage meter — backend."""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Request

from src.auth_helpers import require_authenticated_request

logger = logging.getLogger(__name__)

router = APIRouter()

_cache: dict = {
    "five_hour_pct": None,
    "weekly_pct": None,
    "five_hour_resets_at": None,
    "weekly_resets_at": None,
    "updated_at": None,
    "stale": True,
    "auth_source": None,
    "auth_status": None,
}
_first_probe = True

# In-memory OAuth state; refreshed tokens stay here so we don't fight Claude Code
# over the shared credentials file.
_oauth_mem: dict = {
    "access_token": None,
    "refresh_token": None,
    "expires_at_ms": 0,
    "full_data": None,   # last full JSON read from the credentials file
}
_OAUTH_BUFFER_MS = 300_000   # refresh 5 min before expiry
_CC_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

_BACKEND_DEFAULTS: dict = {
    "cc_usage_backend_poll_s": 60,
    "cc_usage_probe_model": "claude-haiku-4-5-20251001",
    "cc_usage_stale_threshold_s": 180,
    "cc_usage_auth_source": "oauth",
    "cc_usage_cred_path": "/app/data/cc_credentials.json",
}


def _get_setting_int(key: str) -> int:
    try:
        from src.settings import get_setting
        return int(get_setting(key, _BACKEND_DEFAULTS[key]))
    except Exception:
        return int(_BACKEND_DEFAULTS[key])


def _get_setting_str(key: str) -> str:
    try:
        from src.settings import get_setting
        return str(get_setting(key, _BACKEND_DEFAULTS[key]))
    except Exception:
        return str(_BACKEND_DEFAULTS[key])


def _get_anthropic_key() -> Optional[str]:
    """Return API key for the first enabled Anthropic endpoint, or None."""
    try:
        from core.database import SessionLocal, ModelEndpoint
        from src.llm_core import _detect_provider
        from src.endpoint_resolver import resolve_endpoint_runtime
        db = SessionLocal()
        try:
            eps = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
            for ep in eps:
                if _detect_provider(ep.base_url or "") == "anthropic":
                    try:
                        _, api_key = resolve_endpoint_runtime(ep)
                        if api_key:
                            return api_key
                    except Exception:
                        pass
        finally:
            db.close()
    except Exception as e:
        logger.warning("[usage] Failed to get Anthropic key: %s", e)
    return None


def _read_cred_file(path: str) -> Optional[dict]:
    """Read Claude Code credentials file. Returns the claudeAiOauth sub-dict or None."""
    try:
        with open(path) as f:
            raw = json.load(f)
        auth = raw.get("claudeAiOauth", {})
        access_token = auth.get("accessToken") or auth.get("access_token")
        if not access_token:
            return None
        return {
            "access_token": access_token,
            "refresh_token": auth.get("refreshToken") or auth.get("refresh_token"),
            "expires_at_ms": int(auth.get("expiresAt") or auth.get("expires_at") or 0),
            "_raw": raw,
        }
    except Exception as e:
        logger.debug("[usage] Cannot read cred file %s: %s", path, e)
    return None


def _save_cred_file(path: str, raw: dict, new_access: str, new_refresh: str,
                    new_expires_at_ms: int) -> None:
    """Write refreshed tokens back into the credentials file, preserving other fields."""
    try:
        auth = raw.get("claudeAiOauth", {}).copy()
        auth["accessToken"] = new_access
        if new_refresh:
            auth["refreshToken"] = new_refresh
        if new_expires_at_ms:
            auth["expiresAt"] = new_expires_at_ms
        raw = dict(raw)
        raw["claudeAiOauth"] = auth
        with open(path, "w") as f:
            json.dump(raw, f, indent=2)
        logger.debug("[usage] Saved refreshed credentials to %s", path)
    except Exception as e:
        logger.debug("[usage] Failed to save cred file %s: %s", path, e)


async def _refresh_token(refresh_token: str) -> Optional[str]:
    """Exchange refresh token for a new access token. Updates _oauth_mem."""
    global _oauth_mem
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://platform.claude.com/v1/oauth/token",
                headers={"Content-Type": "application/json"},
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": _CC_OAUTH_CLIENT_ID,
                },
            )
            if r.status_code != 200:
                logger.warning("[usage] Token refresh failed %d: %s", r.status_code, r.text[:200])
                return None
            body = r.json()
            new_token = body.get("access_token")
            new_refresh = body.get("refresh_token") or refresh_token
            expires_in = body.get("expires_in", 28800)
            new_expires_ms = int(
                (datetime.now(timezone.utc).timestamp() + int(expires_in)) * 1000
            )
            if new_token:
                _oauth_mem["access_token"] = new_token
                _oauth_mem["refresh_token"] = new_refresh
                _oauth_mem["expires_at_ms"] = new_expires_ms
                # Write back to credentials file so next restart re-reads a valid token
                raw = _oauth_mem.get("full_data")
                if raw:
                    path = _get_setting_str("cc_usage_cred_path")
                    _save_cred_file(path, raw, new_token, new_refresh, new_expires_ms)
                logger.info("[usage] OAuth token refreshed")
            return new_token
    except Exception as e:
        logger.warning("[usage] Token refresh error: %s", e)
    return None


async def _get_access_token() -> Optional[str]:
    """Return a valid access token, loading/refreshing as needed."""
    global _oauth_mem
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    path = _get_setting_str("cc_usage_cred_path")

    # Always try the file for a potentially fresher token from Claude Code
    file_creds = await asyncio.to_thread(_read_cred_file, path)
    if file_creds:
        _oauth_mem["full_data"] = file_creds.get("_raw")
        if file_creds["refresh_token"]:
            _oauth_mem["refresh_token"] = file_creds["refresh_token"]
        if file_creds["expires_at_ms"] > now_ms + _OAUTH_BUFFER_MS:
            # File has a valid token — use it directly
            _oauth_mem["access_token"] = file_creds["access_token"]
            _oauth_mem["expires_at_ms"] = file_creds["expires_at_ms"]
            return file_creds["access_token"]

    # Fall back to our in-memory token if it's still valid
    if _oauth_mem.get("access_token") and _oauth_mem.get("expires_at_ms", 0) > now_ms + _OAUTH_BUFFER_MS:
        return _oauth_mem["access_token"]

    # Try to refresh
    refresh_token = _oauth_mem.get("refresh_token") or (
        file_creds.get("refresh_token") if file_creds else None
    )
    if refresh_token:
        return await _refresh_token(refresh_token)

    # Last resort: use potentially expired token from file (might still work)
    if file_creds:
        return file_creds["access_token"]

    return None


def _parse_float(val: Optional[str]) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _check_stale() -> None:
    updated_at = _cache.get("updated_at")
    if not updated_at or _cache.get("stale"):
        return
    try:
        age_s = (datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)).total_seconds()
        if age_s > _get_setting_int("cc_usage_stale_threshold_s"):
            _cache["stale"] = True
    except Exception:
        pass


async def _probe_oauth() -> bool:
    """Probe via GET /api/oauth/usage. Returns True on success."""
    access_token = await _get_access_token()
    if not access_token:
        _cache.update({"auth_status": "not_found", "stale": True})
        return False

    async def _call(token: str) -> Optional[dict]:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.anthropic.com/api/oauth/usage",
                headers={"Authorization": f"Bearer {token}"},
            )
            return r

    try:
        r = await _call(access_token)
        if r.status_code == 401:
            refresh_token = _oauth_mem.get("refresh_token")
            if refresh_token:
                new_token = await _refresh_token(refresh_token)
                if new_token:
                    r = await _call(new_token)
                else:
                    _cache.update({"auth_status": "expired", "stale": True})
                    return False
            else:
                _cache.update({"auth_status": "expired", "stale": True})
                return False

        if r.status_code != 200:
            logger.warning("[usage] OAuth usage endpoint %d: %s", r.status_code, r.text[:200])
            _cache.update({"auth_status": "error", "stale": True})
            return False

        data = r.json()
        five_h = data.get("five_hour") or {}
        seven_d = data.get("seven_day") or {}
        five_h_pct = five_h.get("utilization")
        weekly_pct = seven_d.get("utilization")

        # OAuth endpoint returns 0–100; cache uses 0–1 fractions
        if five_h_pct is not None:
            five_h_pct = float(five_h_pct) / 100.0
        if weekly_pct is not None:
            weekly_pct = float(weekly_pct) / 100.0

        _cache.update({
            "five_hour_pct": five_h_pct,
            "weekly_pct": weekly_pct,
            "five_hour_resets_at": five_h.get("resets_at"),
            "weekly_resets_at": seven_d.get("resets_at"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "stale": False,
            "auth_source": "oauth",
            "auth_status": "ok",
        })
        logger.debug("[usage] OAuth: 5h=%.1f%% week=%.1f%%",
                     (five_h_pct or 0) * 100, (weekly_pct or 0) * 100)
        return True
    except Exception as e:
        logger.warning("[usage] OAuth probe failed: %s", e)
        _cache.update({"auth_status": "error", "stale": True})
        return False


async def _probe_apikey() -> bool:
    """Probe via POST /v1/messages with API key. Returns True on success."""
    global _first_probe
    probe_model = _get_setting_str("cc_usage_probe_model")
    api_key = await asyncio.to_thread(_get_anthropic_key)
    if not api_key:
        logger.debug("[usage] No Anthropic key found — skipping API key probe")
        _cache.update({"auth_status": "not_found", "stale": True})
        return False

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": probe_model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            hdrs = dict(r.headers)
            if _first_probe:
                rl = {k: v for k, v in hdrs.items() if "ratelimit" in k.lower()}
                logger.info("[usage] Rate-limit headers on first probe: %s", rl)
                _first_probe = False

            five_h_pct = _parse_float(hdrs.get("anthropic-ratelimit-unified-5h-utilization"))
            weekly_pct = _parse_float(hdrs.get("anthropic-ratelimit-unified-7d-utilization"))

            _cache.update({
                "five_hour_pct": five_h_pct,
                "weekly_pct": weekly_pct,
                "five_hour_resets_at": hdrs.get("anthropic-ratelimit-unified-5h-reset"),
                "weekly_resets_at": hdrs.get("anthropic-ratelimit-unified-7d-reset"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "stale": False,
                "auth_source": "apikey",
                "auth_status": "ok",
            })
            logger.debug("[usage] API key probe: 5h=%s week=%s", five_h_pct, weekly_pct)
            return True
    except Exception as e:
        logger.warning("[usage] API key probe failed: %s", e)
        _cache.update({"auth_status": "error", "stale": True})
        return False


async def _probe_once() -> None:
    auth_source = _get_setting_str("cc_usage_auth_source")
    if auth_source == "oauth":
        if not await _probe_oauth():
            logger.debug("[usage] OAuth failed, trying API key fallback")
            await _probe_apikey()
    else:
        await _probe_apikey()


async def _bg_poller() -> None:
    while True:
        try:
            await _probe_once()
        except Exception as e:
            logger.warning("[usage] bg_poller error: %s", e)
        poll_s = max(30, min(600, _get_setting_int("cc_usage_backend_poll_s")))
        await asyncio.sleep(poll_s)


def start_usage_poller() -> asyncio.Task:
    return asyncio.create_task(_bg_poller())


@router.get("/api/usage/claude-code")
async def get_claude_code_usage(request: Request):
    require_authenticated_request(request)
    _check_stale()
    return _cache


@router.get("/api/usage/claude-code/config")
async def get_claude_code_config(request: Request):
    require_authenticated_request(request)
    return {
        "backend_poll_s": _get_setting_int("cc_usage_backend_poll_s"),
        "probe_model": _get_setting_str("cc_usage_probe_model"),
        "stale_threshold_s": _get_setting_int("cc_usage_stale_threshold_s"),
        "auth_source": _get_setting_str("cc_usage_auth_source"),
        "cred_path": _get_setting_str("cc_usage_cred_path"),
    }


@router.patch("/api/usage/claude-code/config")
async def patch_claude_code_config(request: Request):
    require_authenticated_request(request)
    body = await request.json()
    try:
        from src.settings import load_settings, save_settings
        settings = load_settings()
        allowed: dict = {
            "backend_poll_s":   ("cc_usage_backend_poll_s",   int, 30,   600),
            "probe_model":      ("cc_usage_probe_model",       str, None, None),
            "stale_threshold_s":("cc_usage_stale_threshold_s", int, 30,  3600),
            "auth_source":      ("cc_usage_auth_source",       str, None, None),
            "cred_path":        ("cc_usage_cred_path",         str, None, None),
        }
        for field, (key, cast, lo, hi) in allowed.items():
            if field not in body:
                continue
            try:
                val = cast(body[field])
                if field == "auth_source":
                    val = val if val in ("oauth", "apikey") else "oauth"
                if lo is not None:
                    val = max(lo, val)
                if hi is not None:
                    val = min(hi, val)
                settings[key] = val
            except (ValueError, TypeError):
                pass
        save_settings(settings)
    except Exception as e:
        logger.warning("[usage] Failed to save config: %s", e)
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def setup_usage_routes() -> APIRouter:
    return router
