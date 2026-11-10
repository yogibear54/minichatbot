# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this repo is

**site-chatbot** — an embeddable, markdown-only, **full-context (no RAG)** chatbot for a
single website. A crawl becomes one clean-markdown corpus; the corpus is token-validated
against the model's context window before serving (PASS/WARN/FAIL verdict gates serving);
answers stream over SSE with server-validated citations; a vanilla-JS widget renders chat
and drives "Show me" (navigate + scroll + highlight) via postMessage.

- `PLAN.md` is the **spec of record** — component specs, budgets, locked decisions.
  Code comments cite it (e.g. `PLAN §3.2`). Change the plan first if a change contradicts it.
- `RESEARCH.md` is the rationale (tool/model/latency research) — reference, not spec.
- `chatbot/README.md` is the user-facing ops doc (setup, per-site config, troubleshooting) —
  keep it accurate when you change CLI flags, config keys, or endpoints.

## Layout

Everything implemented lives in `chatbot/` (run commands from that directory — imports are
bare `core`/`server` paths):

```
chatbot/
  cli.py            CLI: ingest | refresh | validate | serve (exit 2 on FAIL verdict, CI-friendly)
  config.example.yaml   config template; real config.yaml + data/ are gitignored
  core/
    config.py       dataclass configs loaded from YAML; per-site folders (one install = one site)
    ingest.py       discovery (sitemap/robots + crawl fallback), fetch, extraction, boilerplate dedup
    pipeline.py     full_ingest / refresh orchestration; dynamic suggestion generation
    corpus.py       bundle assembly: page files, manifest.json, annotated corpus.md, atomic writes
    tokens.py       tiktoken o200k_base counting + PASS/WARN/FAIL verdict policy
    modelinfo.py    syncs context window / max completion from provider /models endpoint (fail-open)
    markdown_utils.py  GitHub-style slugs, whitespace normalization, corpus annotation
  server/
    main.py         FastAPI app: SSE /chat (holdback so the CITATIONS block never reaches the
                    widget), Llama Guard pre-flight, widget assets, /api/refresh (admin token)
    llm.py          OpenAI-compatible client (streaming + one-shot), provider-agnostic
    citations.py    citation extraction, validation vs manifest, grounding (fuzzy anchor match,
                    derived quotes), rescue chip
    prompts.py      system prompt (cache-friendly static prefix first) + CITATION_SCHEMA
    sessions.py     SQLite: sessions, turns, token-bucket rate limits, guard block log
    streamparse.py  incremental JSON "answer" field parser for the structured protocol
  widget/           dependency-free vanilla JS: widget.js (launcher + parent-side guide agent),
                    chat.js (iframe UI), md.js (escaping markdown subset renderer), chat.html/css
  scripts/          phase0_benchmark (TTFT cached/uncached), validate_model (acceptance harness),
                    structured_probe, model_citation_test, e2e_widget (Playwright)
  tests/            pytest suite; helpers.py has a local fixture HTTP server + config factory
```

## Commands

```bash
cd chatbot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest tests -q                        # full suite, no network needed
node tests/md_js.node.js                                   # widget/md.js directly (also run via pytest; skipped if node absent)

.venv/bin/python cli.py ingest --config <site>/config.yaml # crawl + validate + bundle
.venv/bin/python cli.py serve --config <site>/config.yaml --port 8010
```

There is no linter/formatter configured — match existing style by hand.

## Conventions

- Python: `from __future__ import annotations` in every module, type hints, stdlib +
  the deps already in `requirements.txt`. Don't add dependencies for small problems —
  the dependency budget is deliberately tiny (no frameworks beyond FastAPI, no JS packages).
- Comments reference the spec (`PLAN §…`). When you add or change behavior that the plan
  describes, update the comment reference and, if needed, `PLAN.md` itself.
- Config is YAML → dataclasses (`core/config.py`); secrets come **only** from environment
  variables referenced by `api_key_env` / `token_env`. Never put secrets in config files.
- Widget JS is intentionally old-school (IIFE, `var`, `"use strict"`, no build step, no
  imports). Keep it that way — the embed script must run anywhere with zero tooling.
- File writes of the knowledge bundle go through `core/corpus.py`'s atomic-write helpers;
  don't write bundle files ad hoc.

## Invariants — do not weaken

The guardrail layers (PLAN §3.3) are load-bearing:

1. **Corpus is untrusted data.** System prompt says so; never concatenate site content
   into instructions.
2. **Citations are allowlisted.** Only manifest URLs/anchors may reach the widget; quotes
   are verified verbatim against stored page markdown (or marked `"derived": true`).
   Never pass model-provided URLs/HTML through unvalidated.
3. **The CITATIONS block never reaches the user.** Streaming holdback (`main.py`,
   `_HOLD_CHARS`) + server-side parsing keep payloads out of the visible answer.
4. **`widget/md.js` escapes all HTML** before rendering model output. Any md.js change
   must keep/extend the injection-safety assertions in `tests/md_js.node.js`.
5. Guard fails **open** by design (log line, no hard block) when its provider is
   unreachable — preserve that behavior; rate limits and question caps stay on.

## Gotchas

- Run pytest/CLI from `chatbot/` — imports rely on cwd/sys.path (tests and scripts do
  their own `sys.path.insert`). Some test files insert paths themselves; don't "fix" that.
- Site data (`config.yaml`, `data/`, `sessions.db`, per-site folders like `lotus/`) is
  gitignored — a fresh clone has none of it. Tests build their own fixture bundles.
- Tests must not hit the network: `tests/helpers.py` serves a local fixture site and the
  LLM is stubbed. Keep new tests offline; put live-API checks in `scripts/` instead.
- `test_md_js.py` needs `node` on PATH (skipped otherwise). `scripts/e2e_widget.py` needs
  a running server + Playwright chromium — manual/e2e only, not part of pytest.
- Model limits: config `context_window` is an offline fallback; the provider's value wins
  when reachable (`core/modelinfo.py`). Don't bypass that sync or the FAIL gate breaks.
- One installation serves one site; site bundles live in separate folders so they never mix.

## Definition of done

- `pytest tests -q` green (add tests for behavior changes; citation/grounding changes need
  coverage in `test_citations.py` / `test_server.py`).
- Changes to prompts, citation protocol, or model-facing behavior: verify with
  `scripts/validate_model.py` against a real bundle when an API key is available
  (thresholds: ≥60% grounded citations, no hard failures, median TTFT < 6s).
- If you changed user-facing setup/ops (CLI, config keys, endpoints, embed snippet),
  update `chatbot/README.md`.
