"""Token counting (approximation, per PLAN: o200k + margin) and verdict policy."""
from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path

# Reserve for system instructions, answer output, and chat history (see PLAN §5).
_INSTRUCTION_RESERVE = 1200
_HISTORY_RESERVE = 3000


@lru_cache(maxsize=1)
def _encoding():
    cache_dir = Path(__file__).resolve().parent.parent / "data" / ".token_cache"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Read-only install (e.g. root-owned /app in Docker when running as a
        # non-root user) — cache the BPE download in the system temp dir instead.
        cache_dir = Path(tempfile.gettempdir()) / "site-chatbot-token-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(cache_dir))
    import tiktoken

    return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    """o200k_base count. For llama-family models this approximates within ~±10%;
    the budget margins below compensate. Recorded in the manifest as the tokenizer."""
    return len(_encoding().encode(text, disallowed_special=()))


def approximate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def hard_cap_tokens(context_window: int, max_output: int, safety_margin_pct: int) -> int:
    """Largest corpus that still fits: window minus margin minus instruction/output/history reserves."""
    usable = context_window * (100 - safety_margin_pct) / 100.0
    return int(usable - _INSTRUCTION_RESERVE - max_output - _HISTORY_RESERVE)


def verdict_for(
    total_tokens: int,
    context_window: int,
    max_output: int,
    pass_tokens: int = 40000,
    warn_tokens: int = 100000,
    safety_margin_pct: int = 10,
) -> tuple[str, str]:
    """Returns (verdict, reason). Verdicts: PASS / WARN / FAIL (PLAN §5)."""
    cap = hard_cap_tokens(context_window, max_output, safety_margin_pct)
    limit = min(warn_tokens, cap)
    if total_tokens > limit:
        why = f"corpus {total_tokens:,} tokens exceeds the {limit:,}-token limit"
        if cap < warn_tokens and total_tokens > cap:
            why = f"corpus {total_tokens:,} tokens exceeds the model's fitted capacity (~{cap:,})"
        return "FAIL", why
    if total_tokens > pass_tokens:
        return (
            "WARN",
            f"corpus {total_tokens:,} tokens fits, but expect slower cold prefill and some "
            f"mid-corpus quality loss on small models (lost-in-the-middle); consider trimming the site",
        )
    return "PASS", f"corpus {total_tokens:,} tokens — comfortable for full-context mode"
