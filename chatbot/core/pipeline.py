"""Ingest/refresh orchestration: discovery -> fetch -> extract -> bundle (PLAN §3.1, §3.6)."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from . import corpus, ingest
from .config import Config

log = logging.getLogger(__name__)


def full_ingest(cfg: Config, max_pages: int | None = None) -> dict:
    """Fresh ingest of the configured site; overwrites the bundle."""
    data_dir: Path = cfg.data_dir
    (data_dir / "pages").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)

    manifest = corpus.init_manifest(cfg)
    limit = max_pages or cfg.site.max_pages

    with httpx.Client(timeout=30, follow_redirects=True) as client:
        found, method = ingest.discover_urls(
            client, cfg.site.start_url, limit, cfg.site.include_subdomains,
            cfg.site.crawl_fallback, cfg.site.exclude_paths
        )
        manifest["discovery"] = method
        if not found:
            raise RuntimeError("No pages discovered — check start_url / sitemap / crawl_fallback")

        pages: list[dict] = []
        skipped: list[dict] = []
        for i, (url, lastmod) in enumerate(sorted(found.items()), 1):
            page, status = ingest.fetch_and_extract(client, url)
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

    manifest["pages"] = pages
    manifest["skipped"] = skipped
    corpus.build_corpus(data_dir, manifest, cfg)
    return manifest


def refresh_site(cfg: Config) -> dict:
    """Incremental update (PLAN §3.6): sitemap lastmod hint -> conditional GET -> hash diff."""
    data_dir: Path = cfg.data_dir
    manifest = corpus.load_manifest(data_dir)
    if manifest is None:
        return {"action": "full", "manifest": full_ingest(cfg)}

    limit = cfg.site.max_pages
    by_url = {p["url"]: p for p in manifest["pages"]}
    changed: list[str] = []
    unchanged = 0
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        found, _method = ingest.discover_urls(
            client, cfg.site.start_url, limit, cfg.site.include_subdomains,
            cfg.site.crawl_fallback, cfg.site.exclude_paths
        )
        new_urls = [u for u in found if u not in by_url]
        for url in new_urls:
            page, status = ingest.fetch_and_extract(client, url)
            if status == "ok" and page is not None:
                entry = corpus.new_page_entry(page)
                entry["lastmod"] = found.get(url)
                corpus.write_page_file(data_dir / "pages", entry, page.markdown)
                manifest["pages"].append(entry)
                changed.append(f"+ {url}")
        for url, existing in by_url.items():
            page, status = ingest.fetch_and_extract(
                client, url, etag=existing.get("etag"), last_modified=existing.get("last_modified"),
                skip_unchanged=bool(existing.get("etag") or existing.get("last_modified")),
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
