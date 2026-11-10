# minichatbot — site-chatbot

An embeddable, **markdown-only, full-context** chatbot for any (server-rendered) website.
No RAG, no vector DB: a crawl of the site becomes one clean-markdown corpus, validated
against the model's context window before serving. Answers stream fast (SSE), every answer
ships **validated citation links** (page › section › verbatim quote), and a **"Show me"**
action scrolls to and highlights the quoted text on the live page. Unsafe/off-scope
questions are blocked by a Llama Guard pre-flight classifier.

- **Spec:** [`PLAN.md`](PLAN.md) — architecture, component specs, budgets, decisions (the code cites it, e.g. `PLAN §3.2`)
- **Rationale:** [`RESEARCH.md`](RESEARCH.md) — tool/model/latency research behind the decisions
- **Implementation docs:** [`chatbot/README.md`](chatbot/README.md) — detailed setup, per-site config, troubleshooting

## How it works

```
┌ setup (offline CLI) ─────────────────────────────────────────┐
│ ingest: sitemap/crawl → clean markdown per page              │
│   → heading slugs + [src: url#anchor] markers                │
│   → SHA-256 hashes + token counts → manifest.json            │
│   → corpus.md bundle → verdict PASS / WARN / FAIL            │
└──────────────────────────────────────────────────────────────┘
┌ serving (per question, FastAPI) ─────────────────────────────┐
│ Llama Guard pre-flight → stream tokens (SSE)                 │
│   → parse + validate trailing CITATIONS block vs manifest    │
│   → server-side grounding (anchors, quotes, rescue chip)     │
│ widget: source chips = real links + "Show me" (postMessage)  │
│   same page: CSS Highlight API scroll+pulse (<mark> fallback)│
│   other page: url#anchor#:~:text=… (native highlight)        │
└──────────────────────────────────────────────────────────────┘
```

Key design points:

- **Size-gated, not RAG-first.** PASS ≤ 40k corpus tokens (comfortable), WARN ≤ 100k,
  FAIL above — a FAIL bundle is never served. Token counts use `o200k_base` with safety
  margins; model limits auto-sync from the provider's models endpoint.
- **Grounded output.** The model ends answers with a `CITATIONS:` JSON block (default
  `trailer` protocol) or emits schema-enforced JSON (`structured` protocol). The server
  strips the payload from the stream, validates every URL/anchor/quote against the
  manifest, derives quotes from stored section text when the model paraphrases, and
  injects a rescue chip if the model cites nothing.
- **One installation = one site.** Per-site config + data folder (e.g. `mysite/config.yaml`).
- **Dependency-free widget.** Vanilla JS (`widget.js` embed + iframe chat), style-isolated,
  accessible, honors `prefers-reduced-motion`.

## Repository layout

```
PLAN.md, RESEARCH.md      spec + research (read these first; code comments cite PLAN §)
chatbot/                  the implementation (details in chatbot/README.md)
  cli.py                  ingest | refresh | validate | serve
  core/                   ingest pipeline: discovery, extraction, corpus bundle, tokens, config
  server/                 FastAPI app: SSE chat, guard, citations, sessions, LLM client
  widget/                 embed script, iframe chat UI, safe markdown renderer
  scripts/                phase-0 benchmark, model validation harness, e2e widget driver
  tests/                  pytest suite (+ one Node test for widget/md.js)
```

## Quickstart

```bash
cd chatbot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.yaml mysite/config.yaml        # edit site.name / site.start_url
export OPENROUTER_API_KEY=sk-or-...              # runs chat model AND guard

.venv/bin/python cli.py ingest --config mysite/config.yaml     # crawl + validate + bundle
.venv/bin/python cli.py serve --config mysite/config.yaml --port 8010
```

Embed on the site (one line before `</body>`):

```html
<script src="http://localhost:8010/widget.js" data-endpoint="http://localhost:8010" defer></script>
```

Full walkthrough (JS-rendered sites, staging crawls, verdicts, troubleshooting):
[`chatbot/README.md`](chatbot/README.md).

## Tests

```bash
cd chatbot
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q    # 16 tests; no network needed (stub LLM + fixture site)
```

## Status

v1 built and verified end-to-end against a real docs site through OpenRouter.
Outstanding (PLAN Phase 7): widget theming, eval set, deploy recipe.
