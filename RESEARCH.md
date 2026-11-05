# Research: Fast Site-Scoped Chatbot with Deep-Link Navigation

Research for an embeddable chatbot that answers questions from any website's content,
navigates users to the source (URL + location), highlights the answer on-page, and stays
in sync with site updates. Optimized for **speed** with small models via API providers
(OpenRouter, Together, Groq, Cerebras, etc.).

---

## TL;DR — Recommended approach

| Decision | Recommendation |
|---|---|
| Knowledge storage | **Size-gated, not RAG-first**: clean markdown is the source of truth. Corpus ≤ ~40–60k tokens → full-context mode (default; covers most sites). Larger → page-level retrieval (top 3–5 whole pages). Chunk-level RAG only for very large docs sites (see §3.1) |
| Scraping tool | **Firecrawl** (best clean markdown, handles JS/SPA) or **Crawl4AI** (free, self-hosted, Python) — see §2. Spider is fastest/cheapest if a cloud crawler is acceptable |
| Chunking | Split markdown at **headings**; store `heading breadcrumb + GitHub-style anchor + verbatim quote` per chunk (this is what makes deep-linking work) |
| Retrieval | Hybrid search: **BM25 (FTS5) + dense embeddings + RRF fusion**; top-k = 3–5, reranking optional |
| LLM provider | Direct to **Groq** or **Cerebras** for lowest TTFT; **OpenRouter** as the abstraction/fallback layer (`:nitro` routing). Together AI for cheap steady throughput |
| Models | `llama-3.1-8b-instant` (Groq, ~560–840 tok/s), `gpt-oss-20b` (Groq ~1000 tok/s, better reasoning), `gpt-oss-120b` (Cerebras ~3000 tok/s) for hard questions |
| Latency tactics | Stream (SSE), prompt caching, ≤ 2–4k tokens of retrieved context, output cap ~300 tokens, semantic cache for repeat questions |
| Deep links | Citation = `{url, anchor, quote}` → navigate with **Scroll-To-Text Fragment** (`#:~:text=`) or JS-highlight via **CSS Custom Highlight API** for same-page answers |
| Sync | Scheduled incremental crawl: sitemap `lastmod` → conditional GET (ETag/Last-Modified) → SHA-256 content hash → re-chunk/re-embed only changed pages |
| Study/fork | `raksbisht/sitechat`, `9bishal/grounded-rag-bot`, `rootsongjc/rag-chatbot` (see §8) |

---

## 1. System architecture (target)

```
                 ┌───────────────────── INGESTION (offline / cron) ─────────────────────┐
 site ──crawl──▶ │ crawler (Firecrawl/Crawl4AI) → clean markdown per page               │
                 │ → SHA-256 hash & diff vs stored → only changed pages                  │
                 │ → chunk by headings (breadcrumb + anchor + quote metadata)            │
                 │ → embed chunks → upsert vector index (SQLite-vec/Qdrant/LanceDB)      │
                 │ → BM25 index (SQLite FTS5) + doc catalog (url, title, hash, mtime)    │
                 └───────────────────────────────────────────────────────────────────────┘

                 ┌──────────────────── SERVING (online, per question) ───────────────────┐
 user ▶ widget ─▶│ API: embed query → hybrid retrieve top-k → build small prompt         │
                 │ → LLM (Groq/Cerebras/OpenRouter) stream SSE tokens                    │
                 │ → answer + structured citations [{url, anchor, quote}]                │
 widget ◀────────│ widget renders answer; click citation → navigate + highlight          │
                 └───────────────────────────────────────────────────────────────────────┘
```

---

## 2. Scraping website → markdown

### Tool landscape (1,000-URL benchmark, Spider Research, Feb 2026 snapshot)

| | **Firecrawl** | **Crawl4AI** | **Spider** | Jina Reader |
|---|---|---|---|---|
| License | AGPL-3.0 (OSS, self-hostable) + cloud | Apache-2.0, self-host only | MIT (Rust) + cloud | API (r.jina.ai) |
| Language | TypeScript | Python (asyncio, Playwright) | Rust | — |
| Throughput (corpus avg) | ~16 pages/s | ~12 pages/s | ~74 pages/s | n/a |
| Success rate overall | 95.3% (88.4% protected) | 89.7% (72% protected) | 99.9% | n/a |
| Markdown RAG quality (recall@5) | 89.0%, noise 6.8% | slightly below Firecrawl | 91.5%, noise 4.2% | decent |
| Handles JS-heavy SPAs | yes (cloud auto-renders) | yes (Playwright) | yes (smart mode skips browser when possible) | yes |
| Cost | from $16/mo; ~$0.83–3.20 /1K pages | free (you run infra) | ~$0.08–0.15 /1K pages | free tier |
| API surface | `/scrape`, `/crawl`, `/map`, `/extract` | Python API, extraction strategies | crawl/search endpoints | GET url → md |
| Incremental crawling | checksum/ETag/lastmod concepts documented; crawl jobs + webhooks | DIY (you control the loop) | cheap full recrawl viable | DIY |

*Caveat: the benchmark was designed by Spider (self-interested), but methodology is public and rerunnable. Directionally consistent with community experience: Firecrawl = best markdown cleanliness/zero-config; Crawl4AI = best free/self-hosted control; Spider = speed/cost.*

### Recommendation
- **If you want speed-to-build:** Firecrawl cloud — `map` for discovery, `crawl` for full capture, clean markdown out of the box, proxy/browser handled for you.
- **If you want zero vendor cost / full control:** Crawl4AI self-hosted — free, Apache-2.0, markdown + chunking helpers built in; add residential proxies only if you must crawl protected sites (for "chatbot on your own website" use-case you usually don't).
- **Whichever you pick, keep markdown as the canonical artifact** (one `.md` per page + metadata sidecar). Everything downstream (hashing, chunking, embedding, deep-link metadata) operates on this. This also gives you a free fallback: render markdown to the LLM directly for small sites.

Also worth knowing: `trafilatura`/`readability`-style extraction is enough for static docs sites if you want a dependency-free path.

---

## 3. RAG vs. stuffing markdown into context

Evidence gathered:

- **"Lost in the middle"**: accuracy drops 10–20+ points when relevant info sits mid-context; grows worse with size (Stanford paper, cited by Redis).
- **Latency**: TTFT grows with context length (attention ~O(n²)); one test: RAG ~1s end-to-end vs 30–60s long-context on same workload. Retrieval itself adds only ~50–200ms.
- **Quality**: identical answers ~60% of the time across 12 QA datasets; long-context wins on full-document reasoning, RAG wins on precise factual retrieval **with source attribution** — which is exactly this project's requirement (citations + deep links).
- **Updates**: RAG handles frequent content updates gracefully (re-embed one page); long-context requires re-feeding everything.
- Counterpoint: one small-model study ("The Token Tax of Epistemic Accuracy", arXiv:2606.20898) found long-context prompting reached 73.1% vs 65.4% semantic RAG with small models when the corpus fits — i.e., **for tiny sites, full markdown can be more accurate**.

**Implication for this project:** make the threshold automatic:
- `total_tokens(site markdown) < ~40–60k` → *full-context mode*: inject the whole clean markdown (still keep per-page anchors for citations). Fewer moving parts, great for small models.
- larger sites → *page-level retrieval first*: top 3–5 **whole pages** in context (no chunk plumbing, trivially page-level citations) — then chunk-level hybrid RAG only for very large docs sites.
- Hybrid escape hatch: if retrieval confidence is low, retry with a page-level summary index, or let the model call a `fetch_page(url)` tool (agentic fallback, +1 round-trip).

### 3.1 Full-context feasibility check: is 128k context enough?

RAG is a size threshold, not a requirement. Verified math and facts:

| Quantity | Value |
|---|---|
| Typical page as **clean markdown** | ~1,000–2,000 tokens (docs pages up to 3–5k) |
| Same page as **raw HTML** | ~5,000–20,000 tokens (5–20× bloat — never feed HTML to the model) |
| 128k context budget | ~90–100k usable for corpus (leave room for history + output + safety) |
| → Full-context capacity | **~50–100 typical pages** (comfortable ceiling ~40–60k tokens due to lost-in-the-middle on 8B-class models) |

Fits: small business sites, portfolios, landing pages, product marketing, small docs. Doesn't fit: 300+ page docs sites, wikis, e-commerce catalogs.

**Gains from skipping RAG when it fits:**
- Accuracy: small models often beat chunk-RAG on long context (73.1% vs 65.4%, arXiv 2606.20898); model sees structure/cross-references chunking destroys.
- Better citations, free: annotate each section in the bundle with a marker like `[src: /docs/export#csv-export]`; model cites locations directly, no chunk bookkeeping.
- Simpler updates: re-crawl → regenerate bundle. No vectors, no index diff.

**The one real catch — per-request latency:**
- Uncached 60–100k-token prompts mean seconds of prefill (Cerebras p95: ~80–150ms TTFT at <1k tokens → 300–600ms at 10k+; keeps growing from there). This conflicts with the "fast" requirement.
- **Prompt caching makes full-context viable**: order the prompt `static corpus prefix → short history → question`; Groq caching is automatic, free, and cached tokens don't count toward rate limits (https://console.groq.com/docs/prompt-caching).
- Caveats: cache hits not guaranteed (eviction), corpus edits invalidate the cache until re-warmed, and input token cost still scales with corpus size (at cached-discount rates).

**Storage vs rendering roles:** markdown = source of truth for the model; the live HTML DOM is only the highlighting substrate at answer time (Text-Fragment / CSS Highlight API search the rendered page).

---

## 4. Chunking for answer quality AND deep links

This is the single highest-leverage design decision because chunks double as citation targets.

**Strategy: structure-aware, heading-boundary chunking** (community consensus: "the single highest-leverage change most teams can make"):
1. Split markdown at headings (H1–H4); merge sections smaller than ~200 tokens; split sections larger than ~800–1k tokens at paragraph boundaries.
2. Each chunk stores:
   - `url` (canonical page URL)
   - `heading_path`: e.g. `["Install", "Requirements", "Windows"]`
   - `anchor`: GitHub-style slug of the nearest heading (`#windows`) — this is the precise scroll target
   - `quote`: 1–3 verbatim sentences (the chunk's "gist") — used for Scroll-To-Text-Fragment and on-page highlight
   - `title`, `chunk_index`, `content_hash`
3. Prepend the breadcrumb to the chunk text sent to the LLM ("Docs > Install > Requirements > Windows") — small models ground much better with explicit provenance, and it steers citation output.

---

## 5. Retrieval stack (fast + accurate)

- **Hybrid search**: BM25 (SQLite FTS5 — zero infra, millisecond queries) + dense vectors (SQLite-vec / LanceDB / Qdrant), fused with **Reciprocal Rank Fusion**. Hybrid+RRF is the standard remedy for vocabulary-miss failures; reference projects (grounded-rag-bot) use Chroma + BM25 + RRF + rerank.
- **Embedding models**:
  - **BGE-M3** — dense + sparse in one model, multilingual, 8k ctx; the strongest open hybrid option. Needs GPU/CPU budget (~568M params).
  - **EmbeddingGemma (300M) / bge-small / all-MiniLM-L6-v2** — CPU-friendly, fast enough to embed query in ~5–20ms; fine for single-site corpora.
  - If CPU-only deployment → MiniLM/EmbeddingGemma. If a small GPU is available → BGE-M3.
- **Reranking** (optional): bge-reranker-v2-m3 or an LLM rerank step; adds ~100–300ms. Skip at MVP; enable if retrieval precision becomes the complaint.
- **Semantic response cache**: embed the question; if a near-identical question was answered, return cached answer instantly (Redis/SQLite). Huge for FAQ-heavy sites; also cheap "instant" first impression.

---

## 6. LLM providers and model choice (the "fast" requirement)

Measured/reported numbers (2025–2026 sources):

| Provider | Notable models | Speed | Notes |
|---|---|---|---|
| **Groq** (LPU) | `llama-3.1-8b-instant` ~560–840 tok/s; `gpt-oss-20b` ~1000 tok/s; `gpt-oss-120b` ~476 tok/s | **Lowest & most consistent TTFT** — best chat UX | OpenAI-compatible API; prompt caching supported |
| **Cerebras** (wafer-scale) | `gpt-oss-120b` ~3000 tok/s; Llama Scout ~2000 tok/s | Fastest raw throughput; excellent TTFT on small prompts | OpenAI-compatible |
| **SambaNova** (RDU) | ~435 tok/s on some models | fast | fewer model options |
| **Together AI** | Turbo variants of Llama/Qwen/DeepSeek | good latency (one test: median TTFT 693ms vs OpenRouter 980ms on same model; lower throughput 67 vs 101 tok/s) | cheap, dedicated endpoints available |
| **OpenRouter** | everything, one API | adds a routing hop; `:nitro` variant routes to fastest provider; per-provider pinning possible | best fallback/reliability layer (100% vs 91.9% reliability in one head-to-head), price/speed negotiation across providers |

**Key insight:** the same open-weight model runs 10–20× faster on Groq/Cerebras/SambaNova than generic GPU hosts; inference stack matters more than model choice for latency. Gateways (OpenRouter) add a small hop but buy you model portability and automatic failover.

### Suggested model strategy
- **Primary:** `llama-3.1-8b-instant` on Groq (or 8B-class on Cerebras) — sub-second TTFT, cheap, plenty for grounded Q&A with citations when the context is tight and well-formatted.
- **Escalation path:** confidence too low / question needs synthesis → retry on `gpt-oss-120b` (Groq or Cerebras). Route via OpenRouter to make this a config change, not a code change.
- **Local dev / air-gapped:** Ollama with `llama3.1:8b` or `qwen3:8b` — same OpenAI-compatible interface; use it in dev, expect 10–20× worse latency than Groq on typical hardware.
- **Prompt format for citations** (small models need enforcement): system prompt requires final output as JSON: `{ "answer": "...", "citations": [{"url","anchor","quote"}] }` — or answer-then-citations streaming with the citation block emitted last (keeps perceived latency low). Tool-calling/function-calling mode is the most reliable enforcer where supported.

### Latency budget (target: first token < 600ms)

| Step | Budget |
|---|---|
| Query embed | 5–30ms |
| Hybrid retrieve + fuse | 20–100ms |
| Prompt build (cached prefix) | ~0ms |
| LLM TTFT (Groq 8B, ~3k tok prompt) | 150–400ms |
| Stream 150–300 answer tokens @ 500+ tok/s | 0.3–0.6s to finish (streamed = perceived fast) |

**Checklist:**
- Stream via SSE; render tokens immediately; emit citations at the end.
- **Prompt caching:** static prefix (system prompt + instructions) cached → up to ~80% TTFT reduction; put dynamic content (retrieved chunks, history) *after* the cached prefix.
- Cap retrieved context at 2–4k tokens; cap output at ~300 tokens ("answer in ≤120 words + citations").
- Prefer short chat history (last 2–4 turns or summary) — history length dominates TTFT on multi-turn.
- Semantic cache FAQ answers.
- Same-region deployment as the inference provider; keep the API server near the site's users.

---

## 7. Deep links + highlighting ("guide the eye")

### Citation schema (produced by the LLM, validated server-side)
```json
{
  "answer": "Yes — you can export reports as CSV.",
  "citations": [
    { "url": "https://site.com/docs/export",
      "anchor": "#csv-export",
      "quote": "Reports can be exported as CSV from the Reports tab." }
  ]
}
```

### Navigation + highlight mechanisms

| Mechanism | What it does | Support | Use for |
|---|---|---|---|
| **Scroll-To-Text Fragment** `https://site.com/page#:~:text=prefix-,start,end,-suffix` | Browser scrolls to and natively highlights the exact text; style via `::target-text` | Chrome 80+, Edge 83+, Firefox 126/131+, Safari 16.1+ — broad | **Primary**: cross-page navigation with zero site cooperation needed |
| **CSS Custom Highlight API** | JS creates `Range` objects over arbitrary text; styled with CSS (`::highlight(name)`) — no DOM mutation, multiple non-contiguous ranges | Chrome/Edge 105+, Safari 17.2+, Firefox 2025+ (recent) | **Same-page highlight** after finding the quote in the live DOM |
| Heading anchor `#anchor` | Precise, stable scroll target (from chunk metadata) | universal | Preferred target when present; quote used as fallback/refinement |
| Fallback text scan | Walk text nodes, wrap matches in `<mark>` | universal | Browsers without the Highlight API |

### Interaction flow
1. User clicks citation → widget asks parent page (via `postMessage` — embed script doubles as the highlight agent) to navigate or scroll.
2. Same page as user is on → JS search for `quote` → CSS Custom Highlight + scroll into view + attention pulse animation.
3. Different page → navigate with `#:~:text=` STTF URL (native highlight), embedding script also tries JS-highlight refinement (STTF highlight is transient; `::target-text` styles it).
4. Quote not found (site content drifted) → degrade gracefully: open `url#anchor`; if that fails, just the URL.
5. If the chatbot is embedded in an iframe (typical), the parent embed script must perform navigation/highlighting — cross-origin iframes can't touch the parent DOM directly; this is why your embed script should include a small "highlight receiver" (it's your site or your customer's site, so the script is already installed).

**Design rule:** never cite scroll *percentages*/pixel offsets — layouts shift. Cite semantic anchors (heading ID) + verbatim quote; coordinates as a last resort only.

### Grounding guardrail
Follow the "refuse when not in the corpus" pattern (ragchat): if best retrieval score < threshold, answer "I couldn't find that on this site" + offer closest pages. Prevents hallucinated links — far more damaging for a navigation bot than a refusal.

---

## 8. Keeping the index in sync (incremental updates)

Change-detection cascade (cheap checks first):

1. **Sitemap `lastmod`**: fetch `sitemap.xml` (handle `sitemapindex`), compare per-URL `lastmod` against stored mtime → candidate list. (Caveat: operators often submit inaccurate `lastmod`; treat as a hint, not truth.)
2. **Conditional GET**: for candidates (and periodically for all), send `If-None-Match` (ETag) / `If-Modified-Since` → 304 = skip.
3. **Content hash**: SHA-256 of *extracted markdown* (not raw HTML — templates change, meaning doesn't). Changed hash → re-chunk + re-embed only that page; delete vectors/chunks of removed URLs; follow redirects to update canonical URLs.
4. Keep a `pages` table: `url, title, content_hash, etag, last_crawled, last_modified, status` — diff source of truth.

Scheduling: cron (e.g. hourly/daily based on site churn) or a webhook from the site's CMS/CI on deploy ("content changed" → enqueue incremental crawl). Firecrawl/crawl4ai both slot into this; with Crawl4AI you own the whole loop (simple with the table above).

Vector-store update mechanics: upsert by `chunk_id = hash(url + chunk_index)`; soft-delete → prune on next run. Full rebuild of a mid-size site (≤ 5k pages) is often < 10 min anyway — keep rebuild as the repair path.

---

## 9. Prior art to study / fork

| Project | What it gives you |
|---|---|
| **raksbisht/sitechat** | Closest match: self-hosted RAG chatbot platform, crawl website → RAG, single `<script>` embeddable widget, dashboard, provider swap via env var (Ollama/OpenAI/Anthropic/Azure), Q&A training pairs, 440+ tests. No deep-linking feature → your differentiator. |
| **9bishal/grounded-rag-bot** | Hybrid retrieval (Chroma + BM25 + RRF), reranking, **clickable page-level citations**, SSE streaming, Groq models, refusal guard. Great retrieval/citation reference. |
| **rootsongjc/rag-chatbot** | Embeddable RAG widget on Cloudflare Workers, content from a markdown repo. Blueprint for edge deployment (very low latency). |
| **fbedeschi/ragchat** | Minimal grounded QA with refusal-when-not-in-docs pattern. |
| Commercial references | Chatbase, SiteGPT, Kapa AI (kapa highlights "deploy in <1hr, deep integrations"); competitive gap: none of the simple OSS ones do fine-grained on-page highlight navigation. |

---

## 10. Suggested build order (MVP → v1)

1. **Ingest**: Crawl4AI (or Firecrawl) → markdown per page + metadata sidecar; store hashes. *Verify: 1 site end-to-end, spot-check markdown quality.*
2. **Index**: heading chunker (breadcrumb/anchor/quote) → SQLite FTS5 + sqlite-vec → hybrid retrieve with RRF. *Verify: recall spot-checks on 20 questions.*
3. **Serve**: FastAPI/Node API → Groq `llama-3.1-8b-instant`, SSE streaming, strict citation JSON, refusal threshold, prompt-cached prefix. *Verify: TTFT < 600ms, citations resolve.*
4. **Widget**: `<script>` embed + iframe chat UI; citation click → postMessage → STTF navigation / CSS Highlight API. *Verify: cross-page jump + highlight in Chrome/Firefox/Safari.*
5. **Sync**: scheduled incremental crawl (lastmod → ETag → hash), upsert-only-changed. *Verify: edit a page, see updated answer without manual reindex.*
6. **Harden**: OpenRouter fallback routing, semantic cache, full-context mode for small sites, eval set for hallucinated-citation rate.

---

## 11. Risks & open questions

- **JS-only SPAs**: if the target site renders content client-side, the crawler must execute JS (Playwright in Crawl4AI; Firecrawl cloud handles it). Confirm per-site early.
- **STTF quirks**: exact-match text (whitespace sensitivity); highlight is transient/scroll-only on some browsers; needs JS fallback — plan for the embed-script highlight receiver.
- **Chunking edge cases**: tables/lists split badly; keep tables atomic; test on the actual site's content.
- **`lastmod` unreliability**: hash-diff remains the source of truth.
- **Rate limits / cost**: Groq free tier is generous for dev; production spend is tiny (8B-class models ≈ $0.05–0.60/M tokens depending on provider).
- **Multi-tenancy later**: "any websites" → per-site index namespace + per-site widget keys (sitechat's data model is a good template).
- **Agentic fallback cost**: `fetch_page` tool adds a round-trip; keep behind low-confidence triggers only.

---

## 12. Sources

**Scraping**
- Spider Research — Firecrawl vs Crawl4AI vs Spider 1,000-URL benchmark: https://spider.cloud/blog/firecrawl-vs-crawl4ai-vs-spider-honest-benchmark/
- Apify — Crawl4AI vs Firecrawl: https://blog.apify.com/crawl4ai-vs-firecrawl/
- Crawl4AI: https://crawl4ai.org/ · https://github.com/unclecode/crawl4ai
- Firecrawl crawl API: https://docs.firecrawl.dev/features/crawl · incremental crawling glossary: https://www.firecrawl.dev/glossary/web-crawling-apis/incremental-crawling

**RAG vs long-context**
- Redis — RAG vs Large Context Window trade-offs: https://redis.io/blog/rag-vs-large-context-window-ai-apps/
- Qdrant — "Is RAG dead?": https://qdrant.tech/articles/rag-is-dead/
- Lost in the Middle (Stanford): https://cs.stanford.edu/~nfliu/papers/lost-in-the-middle.arxiv2023.pdf
- arXiv 2606.20898 (small-model long-context vs RAG), arXiv 2501.01880 (12-dataset RAG/LC comparison)

**Providers / speed**
- GMI Cloud — Groq vs Cerebras vs SambaNova: https://www.gmicloud.ai/en/blog/fastest-llm-platform-compare
- Inworld — Fastest LLM inference APIs (TTFT guide): https://inworld.ai/resources/fastest-llm-inference-api
- eesel — Groq pricing/speeds (Llama 3.1 8B 560–840 TPS; GPT-OSS-20B ~1000 TPS): https://www.eesel.ai/blog/groq-pricing
- OpenRouter vs Together head-to-head: https://quasa.io/stories/openrouter-or-together-ai-speed-wins-do-not-predict-reliability
- OpenAI latency optimization guide (prompt prefix ordering): https://developers.openai.com/api/docs/guides/latency-optimization
- Prompt caching 201: https://developers.openai.com/cookbook/examples/prompt_caching_201

**Deep links / highlighting**
- MDN — Text fragments (`#:~:text=`): https://developer.mozilla.org/en-US/docs/Web/URI/Reference/Fragment/Text_fragments
- MDN — CSS Custom Highlight API: https://developer.mozilla.org/en-US/docs/Web/API/CSS_Custom_Highlight_API
- Browser support (STTF): https://www.testmuai.com/learning-hub/scroll-to-text-fragment-browser-support/
- freeCodeCamp — programmatic highlighting with the Highlight API: https://www.freecodecamp.org/news/how-to-programmatically-highlight-text-with-the-css-custom-highlight-api/

**Chunking / citations / sync**
- DEV — Structure-aware RAG chunking: https://dev.to/faisal_nadeem_752520c3e03/rag-chunking-strategy-how-to-preserve-context-instead-of-destroying-it-49ph
- Heading-path markdown splitting (r/Rag): https://www.reddit.com/r/Rag/comments/1f0q2b7/rethinking_markdown_splitting_for_rag_context/
- Verifiable citations in RAG pipelines: https://rubel.dev/blog/implementing-llm-grounding-verifiable-citations-in-rag-pipelines
- Incremental crawling (Bright Data glossary; ETag/lastmod/hash): https://brightdata.com/glossary/incremental-crawling

**Embeddings**
- BGE-M3: https://huggingface.co/BAAI/bge-m3
- Open-source embedding model comparisons: https://supermemory.ai/blog/best-open-source-embedding-models-benchmarked-and-ranked/ · https://www.tigerdata.com/blog/finding-the-best-open-source-embedding-model-for-rag

**Prior art**
- sitechat: https://github.com/raksbisht/sitechat
- grounded-rag-bot: https://github.com/9bishal/grounded-rag-bot
- Cloudflare Workers RAG chatbot: https://github.com/rootsongjc/rag-chatbot
- ragchat: https://github.com/fbedeschi/ragchat
