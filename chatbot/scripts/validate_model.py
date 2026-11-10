#!/usr/bin/env python
"""Model acceptance harness — validate any OpenRouter model against THIS chatbot's
contract before shipping it in config.yaml.

Checks per question battery:
  - response arrives (no upstream errors)
  - answer text non-empty (and clean of citation JSON in trailer mode)
  - citations grounded: >=1 valid citation with a real manifest URL (+ highlight quote
    or derived quote) on at least THRESHOLD_EXACT fraction of questions
  - TTFT recorded

Usage:
  export OPENROUTER_API_KEY=sk-or-...
  .venv/bin/python scripts/validate_model.py --models meta-llama/llama-3.1-8b-instruct
  .venv/bin/python scripts/validate_model.py --models google/gemma-3-12b-it mistralai/mistral-small-3.2-24b-instruct --protocol structured

Exit code 0 = every model passed, 1 = at least one failed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import load_config
from server.citations import KnowledgeBase, extract_citations, ground_citations
from server.llm import LLMError, OpenAICompatClient
from server.prompts import CITATION_SCHEMA, build_messages, build_system_prompt
from server.streamparse import StreamedAnswerParser

# Question battery: (question, page_url, page_title, must_be_about)
BATTERY = [
    ("How do I contact you and how fast do you reply?", "", "", None),
    ("What services do you offer?", "", "", "service"),
    ("Summarize this article.", "http://localhost:8081/blogs/developers-to-follow",
     "Developers to Follow", "developer"),
    ("What is on this page?", "http://localhost:8081/about", "About", None),
    ("How do I install and use this library?", "", "", None),
]

# Acceptance thresholds (deliberately conservative for small models)
THRESHOLD_EXACT = 0.6      # >= 60% of questions must carry >=1 grounded citation
THRESHOLD_TTFT_MS = 6000   # median first-token latency budget


async def run_question(client, cfg, protocol, sysmsg, model, question, page_url, page_title):
    messages = build_messages(sysmsg, [], question, page_url, page_title)
    ttft = None
    t0 = time.perf_counter()
    if protocol == "structured":
        parser = StreamedAnswerParser()
        raw = ""
        async for delta in client.stream_chat(model, messages, cfg.chat.max_output,
                                              cfg.chat.temperature,
                                              response_format=CITATION_SCHEMA):
            if ttft is None:
                ttft = (time.perf_counter() - t0) * 1000
            raw += delta
            parser.feed(delta)
        display = parser.final_display()
        try:
            obj = json.loads(raw)
            candidates = obj.get("citations") or []
            if not display:
                display = obj.get("answer", "")
        except json.JSONDecodeError:
            return {"ok": False, "fail": "invalid JSON under schema", "ttft": ttft,
                    "display": "", "citations": []}
    else:
        acc = []
        async for delta in client.stream_chat(model, messages, cfg.chat.max_output,
                                              cfg.chat.temperature):
            if ttft is None:
                ttft = (time.perf_counter() - t0) * 1000
            acc.append(delta)
        full = "".join(acc)
        display, candidates = extract_citations(full)
        candidates = candidates or []
        if "CITATIONS" in display:
            return {"ok": False, "fail": "CITATIONS block leaked into display", "ttft": ttft,
                    "display": display, "citations": []}
    if not display.strip():
        return {"ok": False, "fail": "empty answer", "ttft": ttft, "display": "", "citations": []}
    return {"ok": True, "fail": None, "ttft": ttft, "display": display, "citations": candidates}


async def evaluate(client, cfg, kb, protocol, sysmsg_trailer, sysmsg_structured, model):
    sysmsg = sysmsg_structured if protocol == "structured" else sysmsg_trailer
    results = []
    for q, u, t, about in BATTERY:
        r = await run_question(client, cfg, protocol, sysmsg, model, q, u, t)
        r["citations"] = ground_citations(r["citations"], kb)
        r["grounded"] = 1 if r["citations"] else 0
        if about and r["citations"]:
            r["relevant"] = any(about in c["url"].lower() for c in r["citations"])
        else:
            r["relevant"] = None
        results.append(r)
    return results


async def main():
    ap = argparse.ArgumentParser(description="validate OpenRouter models for this chatbot")
    ap.add_argument("--config", default="lotus/config.yaml")
    ap.add_argument("--models", required=True, help="comma-separated model ids")
    ap.add_argument("--protocol", choices=["trailer", "structured"], default="trailer")
    args = ap.parse_args()

    cfg = load_config(args.config)
    kb = KnowledgeBase(cfg.data_dir)
    kb.load(json.loads((cfg.data_dir / "manifest.json").read_text(encoding="utf-8")))
    corpus_md = (cfg.data_dir / "corpus.md").read_text(encoding="utf-8")
    sys_t = build_system_prompt(cfg.site.name, corpus_md, protocol="trailer")
    sys_s = build_system_prompt(cfg.site.name, corpus_md, protocol="structured")
    client = OpenAICompatClient(cfg.chat.base_url, os.environ[cfg.chat.api_key_env],
                                cfg.chat.extra_headers)

    all_ok = True
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        print(f"\n=== {model}  (protocol={args.protocol}) ===")
        rows = await evaluate(client, cfg, kb, args.protocol, sys_t, sys_s, model)
        exact = sum(r["grounded"] for r in rows)
        n = len(rows)
        ttfts = [r["ttft"] for r in rows if r["ttft"]]
        med_ttft = statistics.median(ttfts) if ttfts else float("nan")
        fails = [r["fail"] for r in rows if r["fail"]]
        for i, r in enumerate(rows, 1):
            cite = r["citations"][0]["url"] if r["citations"] else "-"
            rel = r["relevant"]
            print(f"  Q{i}: {'OK ' if r['ok'] else 'ERR'} ttft={r['ttft'] and round(r['ttft'])}ms "
                  f"grounded={r['grounded']} cite={cite[:60]}"
                  + (f" relevant={'?' if rel is None else bool(rel)}" if r["citations"] else "")
                  + (f"  [{r['fail']}]" if r["fail"] else ""))
        passed = exact / n >= THRESHOLD_EXACT and not fails and med_ttft < THRESHOLD_TTFT_MS
        verdict = "PASS" if passed else "FAIL"
        all_ok &= passed
        print(f"  grounded {exact}/{n} (threshold {THRESHOLD_EXACT:.0%}), "
              f"median TTFT {med_ttft:.0f}ms (budget {THRESHOLD_TTFT_MS}ms), "
              f"hard failures: {len(fails)} -> {verdict}")
    print("\nOVERALL:", "PASS" if all_ok else "FAIL")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
