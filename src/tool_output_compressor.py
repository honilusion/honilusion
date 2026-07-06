"""Token Compression Session 2 — the compressor wired into format_tool_result.

Local (non-API) models only: large tool results are either row-crushed (a
JSON array of uniform-ish dict rows loses its middle) or excerpted (oversized
free text loses its tail), with the full original parked via
core.tool_output_cache.store_tool_output() and a hub_retrieve_full marker left
in its place. API models always pass through unmodified — see
compress_tool_result.

Deliberately has no import-time dependency on src.tool_execution (that module
imports this one) — the one thing this module needs from there
(_FORMATTER_HANDLED_KEYS) is imported lazily inside compress_tool_result.
"""

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

ROW_CRUSH_TRIGGER_CHARS = 2000
ROW_CRUSH_KEEP_FIRST = 5
ROW_CRUSH_KEEP_LAST = 3
ROW_CRUSH_SAMPLE_MID = 3
UNIFORMITY_THRESHOLD = 0.8

PROSE_TRIGGER_CHARS = 6000
PROSE_EXCERPT_CHARS = 1000

# Same length as any real ref_id ("tc-" + 8 hex chars) — used to precompute
# chars_after byte-accurately before the real ref_id exists (see
# compress_tool_result: store_tool_output() must be called with chars_after
# already known, but it's also the thing that generates the real ref_id).
_PLACEHOLDER_REF = "tc-00000000"

# Priority order mirrors format_tool_result's own if/elif chain — only the
# first of these actually present is the text that gets rendered to the
# model, so it's the only one worth prose-compressing.
_PROSE_TEXT_KEYS_IN_PRIORITY = ("stdout", "output", "content", "response", "results")


def detect_array_of_objects(data: Any, uniformity_threshold: float = UNIFORMITY_THRESHOLD):
    """Find a JSON array of dict-shaped, reasonably uniform objects in `data`.

    Accepts either a bare list (validated directly) or a dict (the first
    qualifying list found nested one level under any key — this is where
    real tool results carry them, e.g. {"response": ..., "documents": [...]}).
    Returns the list if it qualifies, else None.

    Uniformity heuristic: every row must be a dict; take the mode (most
    common) frozenset of keys across all rows and require it cover
    >=uniformity_threshold of rows. Simple and cheap rather than a partial
    top-N-keys overlap scheme — good enough to tell "list of similar
    records" from "grab-bag of unrelated dicts", which is all row-crush
    needs to decide.
    """
    if isinstance(data, dict):
        for value in data.values():
            found = detect_array_of_objects(value, uniformity_threshold)
            if found is not None:
                return found
        return None

    if not isinstance(data, list) or not data:
        return None
    if not all(isinstance(row, dict) for row in data):
        return None

    key_sets = [frozenset(row.keys()) for row in data]
    counts: dict = {}
    for ks in key_sets:
        counts[ks] = counts.get(ks, 0) + 1
    mode_count = max(counts.values())
    if (mode_count / len(data)) < uniformity_threshold:
        return None
    return data


def row_crush(rows: list, keep_first: int = ROW_CRUSH_KEEP_FIRST,
              keep_last: int = ROW_CRUSH_KEEP_LAST, sample_mid: int = ROW_CRUSH_SAMPLE_MID):
    """Keep the first `keep_first` rows, the last `keep_last` rows, and an
    evenly-spaced sample of `sample_mid` rows from the middle. Returns
    (kept_rows, omitted_count) — kept_rows in original relative order
    (first-block, then mid-sample, then last-block); the caller splices in
    a marker at index `keep_first`.

    Pure function; never crashes or omits a negative count when the array is
    shorter than keep_first + keep_last.
    """
    n = len(rows)
    if n <= keep_first + keep_last:
        return list(rows), 0

    first_block = rows[:keep_first]
    last_block = rows[n - keep_last:] if keep_last > 0 else []
    middle = rows[keep_first: n - keep_last]

    sample_n = min(sample_mid, len(middle))
    if sample_n <= 0 or not middle:
        mid_sample = []
    elif sample_n >= len(middle):
        mid_sample = list(middle)
    else:
        # Evenly-spaced indices across the middle region, e.g. sample_n=3
        # over 100 rows picks roughly the 25th/50th/75th-percentile rows.
        step = len(middle) / sample_n
        mid_sample = [middle[int(i * step)] for i in range(sample_n)]

    kept_rows = first_block + mid_sample + last_block
    omitted_count = n - len(kept_rows)
    return kept_rows, omitted_count


def is_prose_oversized(text: str, threshold: int = PROSE_TRIGGER_CHARS) -> bool:
    return isinstance(text, str) and len(text) > threshold


def _row_crush_marker(omitted_count: int, ref_id: str) -> str:
    return f"…{omitted_count} similar rows omitted — full output: call hub_retrieve_full with ref_id='{ref_id}'"


def _prose_marker(total_chars: int, ref_id: str) -> str:
    return f"[showing first {PROSE_EXCERPT_CHARS} chars of {total_chars} total — full text: call hub_retrieve_full with ref_id='{ref_id}']"


def compress_tool_result(result, is_api_model: bool, tool_name: str, session_id: Optional[str] = None):
    """Orchestrator. API models always pass through unmodified. Local models:
    row-crush a qualifying nested array if found, and/or excerpt an oversized
    primary text field — both independently, since they target different
    keys and a result can have either, both, or neither.

    Returns the original `result` object unchanged (no copy) when nothing
    qualifies, or a shallow-copied dict with only the qualifying key(s)
    replaced.
    """
    if is_api_model or not isinstance(result, dict):
        return result

    from core.tool_output_cache import store_tool_output
    from src.tool_execution import _FORMATTER_HANDLED_KEYS

    out = result

    def _ensure_copy():
        nonlocal out
        if out is result:
            out = dict(result)

    # 1. Row-crush: a nested array of uniform dict rows under a non-handled key.
    for key, value in result.items():
        if key in _FORMATTER_HANDLED_KEYS:
            continue
        candidate = detect_array_of_objects(value)
        if candidate is None:
            continue
        dumped = json.dumps(candidate, default=str, ensure_ascii=False)
        if len(dumped) <= ROW_CRUSH_TRIGGER_CHARS:
            continue
        kept_rows, omitted_count = row_crush(candidate)
        if omitted_count <= 0:
            continue
        insert_at = min(ROW_CRUSH_KEEP_FIRST, len(kept_rows))
        # chars_after must reflect the FINAL spliced content (marker included),
        # but the real ref_id only exists after store_tool_output() runs.
        # _PLACEHOLDER_REF has the exact same length as any real ref_id
        # (REF_PREFIX + 8 hex chars), so this precompute is byte-accurate.
        preview = kept_rows[:insert_at] + [_row_crush_marker(omitted_count, _PLACEHOLDER_REF)] + kept_rows[insert_at:]
        chars_after = len(json.dumps(preview, default=str, ensure_ascii=False))
        ref_id = store_tool_output(
            dumped, tool_name, session_id,
            chars_before=len(dumped), chars_after=chars_after,
        )
        marker = _row_crush_marker(omitted_count, ref_id)
        _ensure_copy()
        out[key] = kept_rows[:insert_at] + [marker] + kept_rows[insert_at:]

    # 2. Prose: the first big-text key present (format_tool_result's own
    # rendering priority), if its string value is oversized.
    for key in _PROSE_TEXT_KEYS_IN_PRIORITY:
        if key not in out:
            continue
        text = out[key]
        if not isinstance(text, str) or not is_prose_oversized(text):
            continue
        excerpt = text[:PROSE_EXCERPT_CHARS]
        chars_after = len(_prose_marker(len(text), _PLACEHOLDER_REF)) + 1 + len(excerpt)
        ref_id = store_tool_output(
            text, tool_name, session_id,
            chars_before=len(text), chars_after=chars_after,
        )
        _ensure_copy()
        out[key] = f"{_prose_marker(len(text), ref_id)}\n{excerpt}"
        break  # only one text key is ever actually rendered by format_tool_result

    return out
