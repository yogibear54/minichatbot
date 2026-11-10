"""Ingest/refresh orchestration: discovery -> fetch -> extract -> bundle (PLAN §3.1, §3.6)."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from . import corpus, ingest
from .config import Config
from .modelinfo import apply_model_limits, fetch_model_limits

log = logging.getLogger(__name__)



def _first_party_markdown_map(client: httpx.Client, site_url: str,
                              urls: list[str]) -> dict[str, str]:
    """RESEARCH (first-party markdown): llms.txt advertises per-page Markdown versions.
    Highest-fidelity source when available — no boilerplate classifier needed.
    Returns {canonical_page_url: md_url} restricted to the discovered pages."""
    base = urlsplit(site_url)
    origin = f"{base.scheme}://{base.netloc}"
    try:
        r = client.get(origin + "/llms.txt")
    except httpx.HTTPError:
        return {}
    if r.status_code != 200 or len(r.text.strip()) < 10:
        return {}
    mapping: dict[str, str] = {}
    for link in re.findall(r"\(([^)]+?\.md)\)", r.text):
        absu = urljoin(origin + "/", link)
        p = urlsplit(absu)
        if not p.path.endswith(".md"):
            continue
        page_url = urlunsplit((p.scheme, p.netloc, p.path[:-3], "", ""))
        for u in urls:
            if u.split("#")[0] == page_url:
                mapping[u] = absu
    return mapping


def _fetch_first_party(client: httpx.Client, url: str, md_url: str):
    """Fetch a first-party markdown page; returns (FetchedPage|None, status)."""
    try:
        r = client.get(md_url)
    except httpx.HTTPError:
        return None, "error"
    if r.status_code != 200:
        return None, "error"
    md_text = r.text.strip()
    if len(md_text) < 50:
        return None, "skipped"
    first_h1 = next((line[2:].strip() for line in md_text.splitlines()
                     if line.startswith("# ")), url)
    page = ingest.FetchedPage(url=url, title=first_h1, markdown=md_text + "\n")
    page.etag = r.headers.get("etag")
    page.last_modified = r.headers.get("last-modified")
    return page, "ok"



def full_ingest(cfg: Config, max_pages: int | None = None) -> dict:
    """Fresh ingest of the configured site; overwrites the bundle."""
    data_dir: Path = cfg.data_dir
    (data_dir / "pages").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)

    manifest = corpus.init_manifest(cfg)
    limit = max_pages or cfg.site.max_pages

    # provider ground truth wins for context_window; config value = offline fallback
    info = apply_model_limits(cfg, fetch_model_limits(cfg.chat.base_url, cfg.chat.model,
                                                      cfg.api_key(cfg.chat.api_key_env)))

    renderer = None
    if cfg.site.render_js:
        renderer = ingest.JsRenderer()
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            found, method = ingest.discover_urls(
                client, cfg.site.start_url, limit, cfg.site.include_subdomains,
                cfg.site.crawl_fallback, cfg.site.exclude_paths,
                (urlsplit(cfg.site.start_url).netloc if cfg.site.map_sitemap_host else None)
            )
            manifest["discovery"] = method
            if not found:
                raise RuntimeError("No pages discovered — check start_url / sitemap / crawl_fallback")

            first_party = _first_party_markdown_map(client, cfg.site.start_url, list(found))
            if first_party:
                manifest["first_party_markdown"] = sorted(first_party)
                log.info("llms.txt: %d page(s) available as first-party markdown", len(first_party))

            pages: list[dict] = []
            skipped: list[dict] = []
            for i, (url, lastmod) in enumerate(sorted(found.items()), 1):
                if url in first_party:
                    page, status = _fetch_first_party(client, url, first_party[url])
                    status = "ok" if status == "ok" else "skipped"
                else:
                    page, status = ingest.fetch_and_extract(client, url, renderer=renderer)
                if status == "ok" and page is not None:
                    entry = corpus.new_page_entry(page)
                    entry["lastmod"] = lastmod
                    corpus.write_page_file(data_dir / "pages", entry, page.markdown)
                    pages.append(entry)
                    log.info("(%d/%d) ok  %s", i, len(found), url)
                else:
                    reason = {"error": "fetch error", "skipped": "not extractable HTML or too short (JS-only page?)"}.get(status, status)
                    skipped.append({"url": url, "reason": reason})
                    log.info("(%d/%d) skip %s (%s)", i, len(found), url, reason)
                if len(pages) >= limit:
                    break
    finally:
        if renderer:
            renderer.close()

    manifest["pages"] = pages
    manifest["skipped"] = skipped
    if info:
        manifest["model"]["max_completion"] = info.get("max_completion")
        manifest["model"]["limits_source"] = "openrouter-api"
    else:
        manifest["model"]["limits_source"] = "config-fallback"
    corpus.build_corpus(data_dir, manifest, cfg)
    return manifest


def refresh_site(cfg: Config) -> dict:
    """Incremental update (PLAN §3.6): sitemap lastmod hint -> conditional GET -> hash diff."""
    data_dir: Path = cfg.data_dir
    manifest = corpus.load_manifest(data_dir)
    if manifest is None:
        return {"action": "full", "manifest": full_ingest(cfg)}

    apply_model_limits(cfg, fetch_model_limits(cfg.chat.base_url, cfg.chat.model,
                                               cfg.api_key(cfg.chat.api_key_env)))
    limit = cfg.site.max_pages
    by_url = {p["url"]: p for p in manifest["pages"]}
    changed: list[str] = []
    unchanged = 0
    renderer = None
    if cfg.site.render_js:
        renderer = ingest.JsRenderer()
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            found, _method = ingest.discover_urls(
                client, cfg.site.start_url, limit, cfg.site.include_subdomains,
                cfg.site.crawl_fallback, cfg.site.exclude_paths,
                (urlsplit(cfg.site.start_url).netloc if cfg.site.map_sitemap_host else None)
            )
            first_party = _first_party_markdown_map(
                client, cfg.site.start_url, list(found)) if found else {}
            new_urls = [u for u in found if u not in by_url]
            for url in new_urls:
                if url in first_party:
                    page, status = _fetch_first_party(client, url, first_party[url])
                else:
                    page, status = ingest.fetch_and_extract(client, url, renderer=renderer)
                if status == "ok" and page is not None:
                    entry = corpus.new_page_entry(page)
                    entry["lastmod"] = found.get(url)
                    corpus.write_page_file(data_dir / "pages", entry, page.markdown)
                    manifest["pages"].append(entry)
                    changed.append(f"+ {url}")
            for url, existing in by_url.items():
                page, status = ingest.fetch_and_extract(
                    client, url, etag=existing.get("etag"), last_modified=existing.get("last_modified"),
                    skip_unchanged=bool((existing.get("etag") or existing.get("last_modified"))
                                        and renderer is None),
                    renderer=renderer,
                )
                if status == "unchanged":
                    unchanged += 1
                    continue
                if status == "ok" and page is not None and page.markdown.strip() != "":
                    if corpus.sha256(page.markdown) != existing["hash"]:
                        entry = corpus.new_page_entry(page)
                        entry["lastmod"] = found.get(url) or existing.get("lastmod")
                        corpus.write_page_file(data_dir / "pages", entry, page.markdown)
                        manifest["pages"][manifest["pages"].index(existing)] = entry
                        changed.append(f"~ {url}")
                    else:
                        unchanged += 1
        # Removals only trusted from sitemap discovery (crawl fallback can miss pages).
        if found and manifest.get("discovery") == "sitemap":
            removed = [p for p in manifest["pages"] if p["url"] not in found]
            for p in removed:
                manifest["pages"].remove(p)
                (data_dir / "pages" / p["file"]).unlink(missing_ok=True)
                changed.append(f"- {p['url']}")
    finally:
        if renderer:
            renderer.close()

    corpus.build_corpus(data_dir, manifest, cfg)

    summary = {
        "action": "refresh",
        "changed": changed,
        "unchanged": unchanged,
        "checked": len(found),
        "verdict": manifest["verdict"]["status"],
        "corpus_tokens": manifest["verdict"]["corpus_tokens"],
    }
    log_line = ", ".join(changed) if changed else "no changes"
    with open(data_dir / "logs" / "sync.log", "a", encoding="utf-8") as f:
        f.write(f"{corpus.now_iso()}  {log_line}  verdict={summary['verdict']}\n")
    return summary
