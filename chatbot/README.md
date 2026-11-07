# site-chatbot

A markdown-only, full-context chatbot for any (server-rendered) website. Per PLAN.md:
clean markdown is the single source of truth, the site is **validated against the model's
context window** before serving, answers stream fast, every answer ships **validated
citation links** (page › section), and a "Show me" action scrolls to + highlights the
quoted text on the live page. Inappropriate questions are blocked by a Llama Guard
pre-flight classifier.

Stack: **Python/FastAPI server + vanilla JS widget** (per the locked decisions in PLAN §12).

```
┌ setup (offline) ─────────────────────────────────────────────┐
│ cli.py ingest  → sitemap/crawl → clean markdown per page     │
│                → heading slugs + [src: url#anchor] markers   │
│                → SHA-256 hashes + token counts → manifest    │
│                → corpus.md bundle → verdict PASS/WARN/FAIL   │
└──────────────────────────────────────────────────────────────┘
┌ serving (per question) ──────────────────────────────────────┐
│ guard (Llama Guard) → stream tokens (SSE) → parse + validate │
│ CITATIONS block vs manifest → rescue citation if omitted     │
│ widget: source chips = real links + "Show me" (postMessage)  │
│   same page: CSS Highlight API scroll+pulse (<mark> fallback)│
│   other page: url#anchor#:~:text=… (native scroll+highlight) │
└──────────────────────────────────────────────────────────────┘
```

## Setup

```bash
cd chatbot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml    # edit site.name / start_url
.venv/bin/pip install -r requirements-dev.txt   # for tests
```

Secrets (environment variables, never in config):
- `OPENROUTER_API_KEY` — chat model (default `meta-llama/llama-3.1-8b-instruct`)
  **and** the input guard (`meta-llama/llama-guard-4-12b`) — one key runs both.
- `ADMIN_TOKEN` — enables `POST /api/refresh`

Get an OpenRouter key at https://openrouter.ai/settings/keys (sign up, create key).
If you prefer a different guard host (e.g. Groq, key from https://console.groq.com/keys),
point `guard.base_url` / `guard.api_key_env` at it — any OpenAI-compatible endpoint works.

## Run

```bash
.venv/bin/python cli.py ingest                # crawl + validate + build bundle (exit 2 on FAIL)
.venv/bin/python cli.py validate              # re-check token verdict
.venv/bin/python cli.py refresh               # incremental sync (ETag/lastmod → hash diff)
.venv/bin/python cli.py serve --port 8000
```

Embed on any page of the site:

```html
<script src="http://localhost:8000/widget.js" data-endpoint="http://localhost:8000" defer></script>
```

Standalone chat UI: `http://localhost:8000/chat`.

## Token validation (PLAN §5)

The ingest verdict gates serving:
- **PASS** ≤ 40k corpus tokens — comfortable (also the small-model sweet spot)
- **WARN** ≤ 100k — fits; expect slower cold prefill + lost-in-the-middle risk
- **FAIL** above that (or above the model's fitted capacity) — markdown-only mode refused;
  print remedies (trim site / split subsite / per-language bundles / future retrieval mode)

Tokens counted with `o200k_base` (approx for llama-family, ±10%) — margins compensate.
Phase 0 (`scripts/phase0_benchmark.py`) measures real TTFT cached/uncached at 1k–100k
prompt sizes to calibrate these thresholds and pick the chat model.

## Guardrails (PLAN §3.3)

| Layer | Mechanism |
|---|---|
| L0 | system-prompt scope rules + corpus treated as untrusted data |
| L1 | Llama Guard pre-flight (`blocked_categories` config; fails open if provider down) |
| L2 | hidden-text stripping at extraction; citation allowlist (only manifest URLs) |
| L3 | CITATIONS parsed server-side, stripped from the stream (24-char holdback), every quote verified verbatim against page markdown; rescue citation (≈ page-level) if the model omits the block |
| L4 | per-IP token-bucket rate limits, question length caps, block log in SQLite |

## Response protocol (SSE)

`meta → token* → citations? → done` (or `refusal` / `error`).
Citations: `{url, anchor, quote, label[, rescued]}` — `url` is always a manifest URL.

## Tests

```bash
.venv/bin/python -m pytest tests -q     # 16 tests: slugs/annotate, verdicts, citations,
                                        # rescue, sessions/rate-limit, fixture ingest, live SSE e2e
```

## Known limitations (v1, by design)

- Server-rendered sites only — JS-only SPAs are detected at ingest and skipped with a warning.
- The 8B chat model sometimes omits the CITATIONS block despite prompting; the server
  rescue (≈ best-overlap page) keeps navigation working. Escalating to a stronger model
  (`openai/gpt-oss-20b`) is a one-line config change — compare with Phase 0 + evals.
- Guard defaults to Llama Guard 4 on OpenRouter (same key as chat). Category codes
  (`safe` / `unsafe\nS<n>`) are parsed anywhere in the reply; unparseable replies fail
  open with a log line.
- Multilingual sites: ingest everything into one bundle for now; per-language bundles are
  the planned remedy when a site needs it (PLAN §9.2).
