"""Provider model-metadata lookup — keeps config limits in sync with reality (PLAN §5).

OpenRouter exposes per-model ground truth on the public models endpoint:
  GET {base_url}/models  ->  context_length, top_provider.max_completion_tokens
The config's context_window is treated as an offline FALLBACK; when the provider is
reachable, its value wins (a mismatched window silently breaks the FAIL-verdict gate:
mistral reports 256k, phi-4 only 16k — config guesses would wrongly FAIL/PASS sites).
"""
from __future__ import annotations

import httpx


def fetch_model_limits(base_url: str, model: str, api_key: str = "",
                       timeout: float = 10.0) -> dict | None:
    """Returns {"context_window": int, "max_completion": int|None} or None if the
    provider is unreachable or the model is unknown (fail-open: config values stay)."""
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = httpx.get(url, headers=headers, timeout=timeout)
        if r.status_code != 200:
            print("[modelinfo] status", r.status_code)
            return None
        data = r.json().get("data", [])
    except Exception as e:
        print("[modelinfo] exception:", type(e).__name__, str(e)[:120])
        return None
    entry = next((m for m in data if m.get("id") == model), None)
    if entry is None or not entry.get("context_length"):
        return None
    tp = entry.get("top_provider") or {}
    return {"context_window": int(entry["context_length"]),
            "max_completion": tp.get("max_completion_tokens")}


def apply_model_limits(cfg, info: dict | None) -> dict | None:
    """Merge fetched limits into the in-memory config; returns the info dict (or None).
    context_window: provider value WINS (it gates the verdict). max_output: only
    clamped when it exceeds the provider cap — it is a product knob, not a capability."""
    if info is None:
        return None
    if info.get("context_window") and info["context_window"] != cfg.chat.context_window:
        print(f"[model] {cfg.chat.model} reports context window "
              f"{info['context_window']:,} (config said {cfg.chat.context_window:,}) — "
              f"using the provider value")
        cfg.chat.context_window = info["context_window"]
    cap = info.get("max_completion")
    if cap and cfg.chat.max_output > cap:
        print(f"[model] max_output {cfg.chat.max_output} exceeds the provider cap "
              f"{cap} — clamping")
        cfg.chat.max_output = cap
    return info
