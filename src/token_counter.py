"""Approximate token counting for stats/reporting only.

Uses tiktoken cl100k_base (GPT-4-class tokenizer) as a reasonable
approximation across model families. This is intentionally NOT used
for context-trimming decisions (estimate_tokens() in model_context.py
handles those with a 0.3×chars heuristic and stays unchanged).

Falls back to len(text)//4 if tiktoken is unavailable (e.g. during
container build before the package is installed, or in restricted envs).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Lazily loaded encoding — avoids import-time cost and allows graceful
# degradation if tiktoken is absent.
_enc = None
_enc_error_logged = False


def _get_encoding():
    global _enc, _enc_error_logged
    if _enc is not None:
        return _enc
    try:
        import tiktoken
        _enc = tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        if not _enc_error_logged:
            logger.warning("tiktoken unavailable, falling back to chars//4 approximation: %s", exc)
            _enc_error_logged = True
    return _enc


def count_tokens_approx(text: str) -> int:
    """Return an approximate token count for *text*.

    Uses tiktoken cl100k_base when available; falls back to len(text)//4.
    Both are approximations — suitable for stats display, not billing.
    """
    enc = _get_encoding()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # Fallback: ~4 chars per token (standard rough approximation)
    return max(1, len(text) // 4)


# Ratio used when tiktoken can't be applied (we have char counts but not text).
# Derived from the same cl100k_base empirical average.
_CHARS_PER_TOKEN = 4.0


def chars_to_tokens_approx(char_count: int) -> int:
    """Convert a character count to an approximate token count.

    Used when the original text is unavailable (only the char count was stored).
    """
    return max(1, round(char_count / _CHARS_PER_TOKEN))
