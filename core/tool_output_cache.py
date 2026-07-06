"""Agent Hub tool-output cache — write path.

Large tool outputs are parked here behind a short ref_id instead of being
re-injected into context; hub_retrieve_full (mcp_servers/hub_server.py) is
the read path. Kept dependency-free so it can be imported from
src/tool_execution.py without pulling in the rest of core/.
"""

import logging
import secrets
from datetime import timedelta

logger = logging.getLogger(__name__)

TTL_DAYS = 14
MAX_PAYLOAD_CHARS = 1_000_000
REF_PREFIX = "tc-"
_MAX_REF_RETRIES = 5


def _sweep(db, HubToolOutputCache, utcnow_naive) -> None:
    cutoff = utcnow_naive() - timedelta(days=TTL_DAYS)
    db.query(HubToolOutputCache).filter(HubToolOutputCache.created_at < cutoff).delete()


def store_tool_output(
    payload: str,
    tool_name: str | None,
    session_id: str | None,
    chars_before: int | None = None,
    chars_after: int | None = None,
) -> str:
    """Cache a tool output and return its ref_id (format: tc-{8 hex chars}).

    Runs the 14-day TTL sweep first, then applies the 1 MB payload cap
    (larger payloads are stored truncated with truncated=True).

    chars_before/chars_after (Token Compression Session 2, both optional):
    set by the compressor to record the size of the original content vs. the
    size of what actually replaced it in the tool result (row-crushed JSON
    or an excerpt+marker) — groundwork for Session 3's savings stats. Left
    null for plain (non-compression) cache writes.
    """
    from sqlalchemy.exc import IntegrityError
    from core.database import SessionLocal, HubToolOutputCache, utcnow_naive

    truncated = False
    size_chars = len(payload)
    if size_chars > MAX_PAYLOAD_CHARS:
        payload = payload[:MAX_PAYLOAD_CHARS]
        truncated = True

    db = SessionLocal()
    try:
        _sweep(db, HubToolOutputCache, utcnow_naive)
        for _ in range(_MAX_REF_RETRIES):
            ref_id = REF_PREFIX + secrets.token_hex(4)
            row = HubToolOutputCache(
                ref_id=ref_id,
                session_id=session_id,
                tool_name=tool_name,
                payload=payload,
                size_chars=size_chars,
                truncated=truncated,
                created_at=utcnow_naive(),
                chars_before=chars_before,
                chars_after=chars_after,
            )
            db.add(row)
            try:
                db.commit()
                return ref_id
            except IntegrityError:
                db.rollback()
                continue
        raise RuntimeError("store_tool_output: exhausted ref_id collision retries")
    finally:
        db.close()
