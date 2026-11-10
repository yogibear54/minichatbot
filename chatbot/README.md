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
cp config.example.yaml config.yaml    # first site (or per-site folders — see next section)
.venv/bin/pip install -r requirements-dev.txt   # for tests
```

Secrets (environment variables, never in config):
- `OPENROUTER_API_KEY` — chat model (default `meta-llama/llama-3.1-8b-instruct`)
  **and** the input guard (`meta-llama/llama-guard-4-12b`) — one key runs both.
- `ADMIN_TOKEN` — enables `POST /api/refresh`

Get an OpenRouter key at https://openrouter.ai/settings/keys (sign up, create key).
If you prefer a different guard host (e.g. Groq, key from https://console.groq.com/keys),
point `guard.base_url` / `guard.api_key_env` at it — any OpenAI-compatible endpoint works.

## New site setup — step by step

One installation = one site (its own config + data dir, PLAN §12). For each new site,
create a subfolder so bundles never mix — `lotus/` in this repo is a working example.

**1. Create the site config** — e.g. `mysite/config.yaml`. Only the `site` block is
required; chat/guard/budgets inherit the code defaults (OpenRouter + Llama Guard 4).

```yaml
site:
  name: "My Site"
  start_url: "http://localhost:8081/"
  max_pages: 40
  render_js: false           # flip to true for JS-rendered sites (see step 3)
  map_sitemap_host: false    # flip to true for local/staging crawls (see step 3)
  suggestions:               # tappable chips shown in the widget
    - "What services do you offer?"
chat:
  model: "meta-llama/llama-3.1-8b-instruct"   # A/B showed 8B beats gpt-oss-20b at citations
```

**2. Export the key** — `export OPENROUTER_API_KEY=sk-or-...` (runs chat AND guard).

**3. Ingest + validate** — `.venv/bin/python cli.py ingest --config mysite/config.yaml`

Read the verdict table; it tells you what to fix:
- Many pages "skipped — not extractable HTML or too short (JS-only page?)" → the site
  renders content client-side. Set `render_js: true` and re-run (first run downloads
  headless Chromium, ~120 MB: `.venv/bin/playwright install chromium`).
- Pages missing + sitemap lists the *production* domain while you crawl localhost/staging →
  set `map_sitemap_host: true` (rewrites sitemap URLs onto `start_url`'s host).
- Verdict **WARN** → serves, but trim pages if you can (small models lose accuracy mid-corpus).
- Verdict **FAIL** → raise/trim `max_pages`, add `exclude_paths`, or split the site;
  the server refuses to serve a FAIL bundle.
- Exit code: 0 = PASS/WARN, 2 = FAIL (CI-friendly).

**4. Serve** — `.venv/bin/python cli.py serve --config mysite/config.yaml --port 8010`

**5. Embed the widget** — one line in the site's shared footer/template, before `</body>`
(one edit covers every page):

```html
<script src="http://localhost:8010/widget.js" data-endpoint="http://localhost:8010" defer></script>
```

**6. Verify end to end** — open the site, click the launcher, ask a question, click
**Show me** on a citation. Scripted check: `.venv/bin/python scripts/e2e_widget.py "your question"`
(edit `SITE`/`BOT` constants at the top; drives launcher → chat → citation → highlight).

**7. Daily loop** — keep `serve` running; after content edits run `refresh`; `validate`
when in doubt. `refresh` only re-crawls changed pages (ETag/lastmod → markdown hash diff)
and never swaps a FAIL bundle into the running server.

Troubleshooting:
- `model unavailable: upstream 401` → the API key isn't in the server process's environment.
- Guard silent → it fails open when its host is unreachable; look for `[guard]` log lines.
- Chat panel blank on the site → bot server not running on the port in the script tag.
- Production deploy: replace `localhost` URLs in `start_url` + the embed snippet, and drop
  `map_sitemap_host` (the production sitemap host then matches `start_url`).

## Run

```bash
.venv/bin/python cli.py ingest --config <site>/config.yaml    # crawl + validate + build bundle
.venv/bin/python cli.py validate --config <site>/config.yaml  # re-check token verdict
.venv/bin/python cli.py refresh --config <site>/config.yaml   # incremental sync (hash diff)
.venv/bin/python cli.py serve --config <site>/config.yaml --port 8010
```

Embed on any page of the site:

```html
<script src="http://localhost:8000/widget.js" data-endpoint="http://localhost:8000" defer></script>
```

Optional color attributes on that tag restyle the chat panel — see [Theming](#theming).

Standalone chat UI: `http://localhost:8000/chat`.

## Theming

All theming is done with color attributes on the embed script tag — no CSS files to
override, no config keys. The widget reads them from the tag, forwards them to the chat
iframe in the postMessage handshake, and they are applied as CSS custom properties after
allowlist validation (`widget/theme.js`): each value must be a plain color — hex,
`rgb()`/`rgba()`/`hsl()`/`hsla()`, or a CSS color name — anything else is dropped and the
corresponding default stays.

```html
<script src="http://localhost:8000/widget.js" data-endpoint="http://localhost:8000"
        data-accent="#7c3aed" data-accent-weak="#f3e8ff"
        data-bg="#141414" data-fg="#f5f5f5"
        data-muted="#a3a3a3" data-border="#333"
        data-user-bg="#7c3aed" data-user-fg="#ffffff" defer></script>
```

| Attribute | Default | What it colors |
|---|---|---|
| `data-accent` | `#2563eb` | send button, avatar, source-chip links, hover highlights — pick one dark enough for white icons/text |
| `data-accent-weak` | `#eff6ff` | tinted backgrounds: source chips, "answering with context of" banner |
| `data-bg` | `#ffffff` | panel background — set it dark for dark mode |
| `data-fg` | `#1a1a1a` | text; bot-bubble and inline-code tints derive from it automatically |
| `data-muted` | `#6b7280` | secondary text: header subtitle, notes, typing cursor |
| `data-border` | `#e5e7eb` | divider lines, input/chip borders |
| `data-user-bg` | `#2563eb` | the visitor's chat bubbles |
| `data-user-fg` | `#ffffff` | the visitor's bubble text |

Notes:

- Attributes are optional and independent. A single `data-accent` re-brands the stock
  light panel; `data-bg` + `data-fg` + `data-muted` + `data-border` give a dark panel.
- Bot bubbles, chips and code tints derive from `bg`/`fg`, so dark themes come out
  coherent without extra attributes (very old browsers without `color-mix` fall back to
  the stock light surfaces).
- Refusal (amber) and error (red) bubbles keep fixed status colors that stay readable on
  any panel.
- The floating launcher button keeps its neutral dark style (not themed).
- The standalone `/chat` page always uses the defaults — there is no parent script tag to
  read attributes from.

## Citation protocols (config `chat.protocol`)

- **`trailer`** (default): prose answer + trailing `CITATIONS:` block — works with any instruct model.
- **`structured`**: `response_format: json_schema` enforces `{answer, citations[]}` — requires a
  model with real enforcement (metadata: `GET /api/v1/models?supported_parameters=structured_outputs`;
  enforcement quality still varies per upstream — always verify, see below).
  The answer streams normally; the citations payload is parsed server-side and never shown raw.

Both protocols pass through the same **grounding pipeline**: URL allowlist → fuzzy section-name →
anchor match against the stored heading tree → verbatim quote kept, or a quote **derived from the
stored section text** (marked `"derived": true`). Result: citation chips + highlighting work even
when the model paraphrases or omits quotes; if the model cites nothing at all, a current-page-aware
rescue chip (`≈`) still points somewhere real.

**Validating a new model** (before putting it in config):

```bash
OPENROUTER_API_KEY=... .venv/bin/python scripts/validate_model.py \
    --config lotus/config.yaml --models <model-id> --protocol structured   # or trailer
```

Verdict thresholds: ≥60% grounded citations, no hard failures, median TTFT < 6s. Measured on the
lotus bundle: trailer+llama-3.1-8b 5/5 grounded (PASS); structured+mistral-small-24b 3/5 (PASS);
structured+gpt-oss-20b 0/5 with invalid JSON (FAIL — avoid).

## Token validation (PLAN §5)

The ingest verdict gates serving:
- **PASS** ≤ 40k corpus tokens — comfortable (also the small-model sweet spot)
- **WARN** ≤ 100k — fits; expect slower cold prefill + lost-in-the-middle risk
- **FAIL** above that (or above the model's fitted capacity) — markdown-only mode refused;
  print remedies (trim site / split subsite / per-language bundles / future retrieval mode)

**Model limits auto-sync**: at ingest/serve the bot queries the provider's models
endpoint (`context_length`, `top_provider.max_completion_tokens`) — the provider value
wins over `context_window` (config value = offline fallback), and `max_output` is clamped
if it exceeds the model's completion cap. A mismatch prints a warning and is recorded in
`manifest.json` (`model.limits_source`).

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
