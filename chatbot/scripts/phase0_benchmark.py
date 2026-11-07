#!/usr/bin/env python
"""Phase 0 provider spike (PLAN §10): TTFT at various prompt sizes, cached vs uncached.

Run AFTER `ingest` config exists and with the provider key exported:
    OPENROUTER_API_KEY=sk-... python scripts/phase0_benchmark.py --sizes 1000,10000,60000,100000

Measures time-to-first-token for synthetic prompts of N tokens. "cached" runs send the
identical prompt twice and report the second call (provider prompt-cache hit).
Decides: default chat model + whether prompt caching engages through OpenRouter.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from core.config import load_config  # noqa: E402
from core.tokens import _encoding  # noqa: E402

FILLER = ("The quick brown fox jumps over the lazy dog near the river bank every morning. ")


def make_text(n_tokens: int) -> str:
    enc = _encoding()
    filler_ids = enc.encode(FILLER)
    ids = (filler_ids * (n_tokens // len(filler_ids) + 1))[:n_tokens]
    return enc.decode(ids)


async def timed_call(cfg, messages: list[dict]) -> tuple[float, float, int]:
    """One streaming call; returns (ttft_ms, total_ms, output_chars)."""
    url = cfg.chat.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.api_key(cfg.chat.api_key_env)}",
               "Content-Type": "application/json"}
    headers.update({k: v for k, v in cfg.chat.extra_headers.items() if v})
    payload = {"model": cfg.chat.model, "messages": messages, "max_tokens": 32,
               "temperature": 0, "stream": True}
    ttft = total = None
    chars = 0
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as r:
            if r.status_code != 200:
                body = (await r.aread()).decode(errors="replace")[:200]
                raise SystemExit(f"upstream {r.status_code}: {body}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
                if delta:
                    if ttft is None:
                        ttft = (time.perf_counter() - t0) * 1000
                    chars += len(delta)
    total = (time.perf_counter() - t0) * 1000
    return ttft or total, total, chars


async def measure(cfg, n: int, repeats: int) -> dict:
    body = make_text(n)
    rows = {"uncached": [], "cached": [], "out_chars": 0}
    for _ in range(repeats):
        suffix = make_text(24)  # unique suffix -> guaranteed cache miss
        msgs = [{"role": "system", "content": body + suffix},
                {"role": "user", "content": "Reply with the single word: OK"}]
        ttft, _total, chars = await timed_call(cfg, msgs)
        rows["uncached"].append(ttft)
        rows["out_chars"] = chars
    msgs = [{"role": "system", "content": body},
            {"role": "user", "content": "Reply with the single word: OK"}]
    await timed_call(cfg, msgs)  # warm the provider cache
    for _ in range(repeats):
        ttft, _total, chars = await timed_call(cfg, msgs)
        rows["cached"].append(ttft)
        rows["out_chars"] = chars
    return rows


def med(xs):
    return statistics.median(xs) if xs else float("nan")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--sizes", default="1000,10000,60000,100000")
    ap.add_argument("--repeats", type=int, default=2)
    args = ap.parse_args()
    cfg = load_config(args.config)
    print(f"model={cfg.chat.model}  provider={cfg.chat.base_url}  target TTFT < 700ms\n")
    print(f"{'prompt tokens':>14} {'uncached TTFT':>15} {'cached TTFT':>13}  verdict")
    results = {}
    for n in (int(s) for s in args.sizes.split(",")):
        try:
            rows = await measure(cfg, n, args.repeats)
        except SystemExit as e:
            print(str(e))
            return
        unc, cch = med(rows["uncached"]), med(rows["cached"])
        ok = "✅" if cch < 700 else "⚠️ "
        results[n] = cch
        print(f"{n:>14,} {unc:>13.0f}ms {cch:>11.0f}ms  {ok}")
    good = [n for n, ms in results.items() if ms < 700]
    print(f"\nCorpus sizes with cached TTFT < 700ms: {good or 'none — revisit provider/model'}")


if __name__ == "__main__":
    asyncio.run(main())
