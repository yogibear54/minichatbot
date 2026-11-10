"""FastAPI app: SSE chat with guard pre-flight + citation validation, widget assets, admin refresh."""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from core import corpus
from core.config import Config
from server.citations import (KnowledgeBase, extract_citations, rescue_citations,
                              validate_citations)
from server.llm import LLMError, OpenAICompatClient
from server.prompts import build_messages, build_system_prompt
from server.sessions import Store

WIDGET_DIR = Path(__file__).resolve().parent.parent / "widget"
SESSION_ID_RE = re.compile(r"^[a-f0-9]{8,64}$")

# Live-stream holdback: emit tokens with a small lag so the trailing CITATIONS
# block (and its fence) never reaches the widget (PLAN §3.2 protocol).
_HOLD_CHARS = 24
_CIT_INLINE_RE = re.compile(r"CITATIONS:\s*\[", re.IGNORECASE)

GUARD_POLICY = """You are a safety classifier for a website help chatbot. Classify the user
message using MLCommons hazard categories: S1 violent crimes, S2 non-violent crimes,
S3 sex-related crimes, S4 child sexual exploitation, S5 defamation, S6 specialized advice,
S7 privacy, S8 intellectual property, S9 indiscriminate weapons, S10 hate, S11 self-harm,
S12 sexual content, S13 elections, S14 code interpreter abuse.
Respond with exactly one word: 'safe' or the single category code (for example: S10)."""


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="site-chatbot", docs_url=None, redoc_url=None)

    manifest = corpus.load_manifest(cfg.data_dir)
    if manifest is None or not (cfg.data_dir / "corpus.md").exists():
        raise RuntimeError(
            f"No knowledge bundle at {cfg.data_dir} — run `python cli.py ingest` first.")
    if manifest["verdict"]["status"] == "FAIL":
        raise RuntimeError(
            "Bundle verdict is FAIL (" + manifest["verdict"]["reason"] + "). "
            "Trim the site / raise budgets / split the bundle, then re-ingest. "
            "Run `python cli.py validate` for the token table.")

    kb = KnowledgeBase(cfg.data_dir)
    kb.load(manifest)

    corpus_md = (cfg.data_dir / "corpus.md").read_text(encoding="utf-8")
    state = {"system_prompt": build_system_prompt(cfg.site.name, corpus_md), "manifest": manifest}

    chat_client = OpenAICompatClient(
        cfg.chat.base_url, cfg.api_key(cfg.chat.api_key_env), cfg.chat.extra_headers)
    guard_client = OpenAICompatClient(
        cfg.guard.base_url, cfg.api_key(cfg.guard.api_key_env), timeout=20.0)
    store = Store(cfg.data_dir.parent / "sessions.db", history_turns=cfg.budgets.history_turns)

    def refusal_text() -> str:
        return cfg.refusal_message.format(site=cfg.site.name).strip()

    def reload_knowledge() -> None:
        m = corpus.load_manifest(cfg.data_dir)
        if m:
            kb.load(m)
            state["manifest"] = m
            state["system_prompt"] = build_system_prompt(cfg.site.name,
                                                         (cfg.data_dir / "corpus.md").read_text(encoding="utf-8"))

    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    async def guard_check(message: str) -> str:
        """Returns 'safe', a category code (e.g. 'S9'), or 'error' (fail-open, logged).
        Llama Guard 4 replies 'safe' or 'unsafe\nS<n>' — extract the code anywhere in it."""
        try:
            reply = await guard_client.complete(cfg.guard.model, [
                {"role": "system", "content": GUARD_POLICY},
                {"role": "user", "content": message},
            ], max_tokens=16)
        except LLMError as e:
            print(f"[guard] unavailable, failing open: {e}")
            return "error"
        reply = reply.strip().upper()
        m = re.search(r"\bS\d+\b", reply)
        if m:
            return m.group(0)
        if "SAFE" in reply:
            return "safe"
        print(f"[guard] unrecognized reply, failing open: {reply[:40]}")
        return "error"

    # ------------------------------------------------------------------ routes
    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse("/chat")

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "verdict": state["manifest"]["verdict"]["status"]}

    @app.get("/api/info")
    async def info() -> dict:
        v = state["manifest"]["verdict"]
        return {"site_name": cfg.site.name, "verdict": v["status"],
                "pages": v["pages"], "corpus_tokens": v["corpus_tokens"],
                "suggestions": cfg.site.suggestions}

    @app.get("/chat")
    async def chat_page() -> Response:
        html = (WIDGET_DIR / "chat.html").read_text(encoding="utf-8")
        html = html.replace("{{SITE_NAME}}", cfg.site.name)
        return Response(html, media_type="text/html")

    @app.get("/widget.js")
    async def widget_js() -> Response:
        return Response((WIDGET_DIR / "widget.js").read_text(encoding="utf-8"),
                        media_type="application/javascript")

    @app.get("/chat.js")
    async def chat_js() -> Response:
        return Response((WIDGET_DIR / "chat.js").read_text(encoding="utf-8"),
                        media_type="application/javascript")

    @app.get("/chat.css")
    async def chat_css() -> Response:
        return Response((WIDGET_DIR / "chat.css").read_text(encoding="utf-8"),
                        media_type="text/css")

    @app.post("/api/chat")
    async def chat(request: Request) -> StreamingResponse:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "invalid JSON body")
        message = str(body.get("message", "")).strip()
        session_id = str(body.get("session_id") or "")
        page_url = str(body.get("page_url") or "").strip()
        page_title = str(body.get("page_title") or "").strip()[:120]
        if not message:
            raise HTTPException(400, "empty message")
        if len(message) > cfg.rate_limits.max_question_chars:
            raise HTTPException(400, f"message too long (>{cfg.rate_limits.max_question_chars} chars)")
        if not SESSION_ID_RE.match(session_id):
            session_id = store.ensure_session(None)
        else:
            session_id = store.ensure_session(session_id)

        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
        if not store.allow(f"chat:{ip}", cfg.rate_limits.requests_per_minute, cfg.rate_limits.burst):
            raise HTTPException(429, "rate limit exceeded, please slow down")

        async def gen():
            yield sse({"type": "meta", "session_id": session_id,
                       "model": cfg.chat.model, "site": cfg.site.name})

            # -- L1 input guard (Llama Guard pre-flight, locked decision) --------
            if cfg.guard.enabled:
                category = await guard_check(message)
                if category in {c.upper() for c in cfg.guard.blocked_categories}:
                    store.log_block(session_id, category, message)
                    yield sse({"type": "refusal", "message": refusal_text(), "reason": "policy"})
                    yield sse({"type": "done", "finish": "refusal"})
                    return
                # 'error' and unblocked categories fall through to the chat model

            # -- generation (cached static prefix -> history -> question + page ctx) -
            history = store.get_history(session_id)
            resolved_page = kb.resolve(page_url) if page_url else ""
            resolved_title = kb.pages[resolved_page]["title"] if resolved_page else ""
            messages = build_messages(state["system_prompt"], history, message,
                                      resolved_page, resolved_title)
            acc: list[str] = []
            full = ""
            emitted = 0  # chars of the display text already streamed
            try:
                async for delta in chat_client.stream_chat(
                        cfg.chat.model, messages, cfg.chat.max_output, cfg.chat.temperature):
                    acc.append(delta)
                    full = "".join(acc)
                    m = _CIT_INLINE_RE.search(full)
                    limit = m.start() if m else max(0, len(full) - _HOLD_CHARS)
                    if limit > emitted:
                        yield sse({"type": "token", "text": full[emitted:limit]})
                        emitted = limit
            except LLMError as e:
                if not acc:
                    yield sse({"type": "error", "message": f"model unavailable: {e}"})
                    yield sse({"type": "done", "finish": "error"})
                    return
                yield sse({"type": "error", "message": "stream interrupted"})

            full = "".join(acc)
            display, raw = extract_citations(full)
            if len(display) > emitted:
                yield sse({"type": "token", "text": display[emitted:]})
            citations = validate_citations(raw, kb) if raw else []
            if not citations and display.strip() and len(display) > 40:
                # model omitted/mangled the block (small-model risk, PLAN §9.6) ->
                # rescue: best term-overlap page, current page preferred when known
                citations = rescue_citations(message, kb, prefer_url=resolved_page or None)
            if citations:
                yield sse({"type": "citations", "citations": citations})
            yield sse({"type": "done", "finish": "answer"})
            if display.strip():
                store.append_turn(session_id, message, display.strip())

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/refresh")
    async def refresh(request: Request) -> JSONResponse:
        if not cfg.admin_token:
            raise HTTPException(403, "refresh disabled (set ADMIN_TOKEN to enable)")
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {cfg.admin_token}":
            raise HTTPException(401, "bad admin token")
        from core.pipeline import refresh_site

        summary = await asyncio.to_thread(refresh_site, cfg)
        new_manifest = corpus.load_manifest(cfg.data_dir)
        blocked = bool(new_manifest and new_manifest["verdict"]["status"] == "FAIL")
        if blocked:
            # keep serving the last good (in-memory) bundle — PLAN §3.6 atomic-swap spirit
            print("[refresh] new bundle FAIL — previous bundle kept in memory")
        else:
            reload_knowledge()
        return JSONResponse({**summary, "applied": not blocked})

    @app.on_event("shutdown")
    async def shutdown() -> None:
        store.close()

    return app
