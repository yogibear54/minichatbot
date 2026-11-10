#!/usr/bin/env python
"""A/B test OpenRouter models on the CITATIONS protocol with the real site bundle.

Usage: OPENROUTER_API_KEY=... .venv/bin/python scripts/model_citation_test.py
"""
from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import load_config
from server.citations import KnowledgeBase, extract_citations, validate_citations
from server.llm import OpenAICompatClient
from server.prompts import build_messages, build_system_prompt

QUESTIONS = [
    ("How do I contact you and how fast do you reply?", "", ""),
    ("Summarize this article.", "http://localhost:8081/blogs/developers-to-follow", "Developers to Follow"),
    ("What services do you offer?", "", ""),
]
MODELS = [
    "meta-llama/llama-3.1-8b-instruct",
    "openai/gpt-oss-20b",
    "qwen/qwen3-14b",
    "google/gemma-3-12b-it",
    "mistralai/mistral-small-3.2-24b-instruct",
    "meta-llama/llama-3.3-70b-instruct",
    "nvidia/nemotron-3.5-super-vl:free",
]


async def run_one(client, cfg, model, sysmsg, kb, question, page_url, page_title):
    messages = build_messages(sysmsg, [], question, page_url, page_title)
    acc, ttft, t0 = [], None, time.perf_counter()
    try:
        async for delta in client.stream_chat(model, messages, 400, 0.2):
            if ttft is None:
                ttft = (time.perf_counter() - t0) * 1000
            acc.append(delta)
    except Exception as e:
        return {"error": str(e)[:80], "ttft": None, "exact": 0, "block": False}
    full = "".join(acc)
    display, raw = extract_citations(full)
    citations = validate_citations(raw, kb) if raw else []
    return {
        "error": None, "ttft": ttft, "chars": len(full),
        "exact": 1 if citations else 0,
        "block": raw is not None,
        "top": (citations[0]["url"].replace("http://localhost:8081", "") if citations else None),
    }


async def main():
    cfg = load_config("lotus/config.yaml")
    kb = KnowledgeBase(cfg.data_dir)
    kb.load(corpus_manifest := __import__("json").loads(
        (cfg.data_dir / "manifest.json").read_text(encoding="utf-8")))
    sysmsg = build_system_prompt(cfg.site.name, (cfg.data_dir / "corpus.md").read_text(encoding="utf-8"))
    client = OpenAICompatClient(cfg.chat.base_url, os.environ[cfg.chat.api_key_env], cfg.chat.extra_headers)

    print(f"{'model':45} {'exact/3':>7} {'block%':>6} {'med TTFT':>9}")
    results = {}
    for model in MODELS:
        rows = []
        for q, u, t in QUESTIONS:
            rows.append(await run_one(client, cfg, model, sysmsg, kb, q, u, t))
        exact = sum(r["exact"] for r in rows)
        block = sum(1 for r in rows if r["block"])
        errs = sum(1 for r in rows if r["error"])
        ttfts = [r["ttft"] for r in rows if r["ttft"]]
        med = statistics.median(ttfts) if ttfts else float("nan")
        results[model] = (exact, rows)
        err_note = f" ({errs} errors)" if errs else ""
        print(f"{model:45} {exact}/3{err_note:>8} {block * 100 // 3:>5}% {med:>8.0f}ms")

    print("\ndetails (exact-citation URLs per model):")
    for model, (exact, rows) in results.items():
        tops = [r.get("top") or ("block-no-valid" if r.get("block") else "no-block") for r in rows]
        print(f"  {model}: {tops}")


if __name__ == "__main__":
    asyncio.run(main())
