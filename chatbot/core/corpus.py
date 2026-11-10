"""Knowledge bundle: page files, manifest.json, corpus.md (annotated), atomic writes."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .ingest import FetchedPage
from .markdown_utils import annotate_page, extract_headings, normalize_ws
from .tokens import count_tokens, verdict_for

import math

TOKENIZER_LABEL = "o200k_base (approx for llama-family, ~±10%; safety margins applied)"


def page_filename(url: str) -> str:
    """Stable filename for a page: host + path slug."""
    from urllib.parse import urlsplit

    p = urlsplit(url)
    slug = (p.path or "/").strip("/").replace("/", "-").lower() or "home"
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-") or "home"
    return f"{slug}.md"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_page_entry(page: FetchedPage) -> dict:
    headings = extract_headings(page.markdown)
    return {
        "url": page.url,
        "title": page.title,
        "file": page_filename(page.url),
        "tokens": count_tokens(page.markdown),
        "hash": sha256(page.markdown),
        "etag": page.etag,
        "last_modified": page.last_modified,
        "lastmod": None,
        "fetched_at": now_iso(),
        "headings": [{"level": h.level, "text": h.text, "slug": h.slug} for h in headings],
    }


def compute_verdict(manifest: dict, cfg: Config) -> dict:
    total = manifest.get("verdict", {}).get("corpus_tokens", 0)
    status, reason = verdict_for(
        total, cfg.chat.context_window, cfg.chat.max_output,
        cfg.budgets.pass_tokens, cfg.budgets.warn_tokens, cfg.budgets.safety_margin_pct,
    )
    manifest["verdict"].update({
        "status": status,
        "reason": reason,
        "total_tokens": total,
        "pages": len(manifest["pages"]),
        "skipped": len(manifest["skipped"]),
    })
    return manifest


def write_page_file(pages_dir: Path, entry: dict, markdown: str) -> None:
    _atomic_write(pages_dir / entry["file"], markdown)


def load_manifest(data_dir: Path) -> dict | None:
    p = data_dir / "manifest.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def save_manifest(data_dir: Path, manifest: dict) -> None:
    _atomic_write(data_dir / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))


def _section_key(chunk: str) -> str:
    """Normalized identity of a corpus chunk (markers/headings stripped, lowercased)."""
    lines = [l for l in chunk.splitlines() if not l.startswith("[src:")]
    t = " ".join(lines)
    t = re.sub(r"#+", " ", t)
    return normalize_ws(t).lower()


def _strip_boilerplate(frags: list[tuple[dict, str]],
                       threshold: float = 0.6, min_pages: int = 3):
    """RESEARCH (dedup): text sections repeated on >=60% of pages (min 3) are corpus
    boilerplate — CTAs, footer strips, repeated contact blurbs. They cost tokens and
    skew retrieval toward whatever page repeats them most. Returns (cleaned frags, removed)."""
    import collections

    n = len(frags)
    counts: collections.Counter = collections.Counter()
    frag_keys: list[set] = []
    for _, frag in frags:
        keys = set()
        for chunk in re.split(r"\n\s*\n", frag):
            k = _section_key(chunk)
            if len(k) >= 20:                      # ignore tiny chunks (bare headings)
                keys.add(k)
        frag_keys.append(keys)
        counts.update(keys)
    need = max(min_pages, math.ceil(threshold * n))
    boiler = {k for k, c in counts.items() if c >= need}
    if not boiler:
        return frags, 0
    out: list[tuple[dict, str]] = []
    removed = 0
    for (entry, frag), keys in zip(frags, frag_keys):
        kept = []
        for chunk in re.split(r"\n\s*\n", frag):
            if _section_key(chunk) in boiler:
                removed += 1
                continue
            kept.append(chunk)
        out.append((entry, "\n\n".join(kept)))
    return out, removed


def build_corpus(data_dir: Path, manifest: dict, cfg: Config) -> None:
    """Assemble corpus.md (annotated bundle sent to the model), recount tokens, refresh verdict."""
    pages_dir = data_dir / "pages"
    frags: list[tuple[dict, str]] = []
    for entry in sorted(manifest["pages"], key=lambda p: p["url"]):
        md = (pages_dir / entry["file"]).read_text(encoding="utf-8")
        frags.append((entry, annotate_page(entry["url"], entry["title"], md)))

    removed = 0
    if cfg.site.dedup_boilerplate and len(frags) >= 4:
        frags, removed = _strip_boilerplate(frags, threshold=cfg.site.boilerplate_threshold)
    manifest["boilerplate_sections_removed"] = removed

    parts: list[str] = [
        f"SITE CONTENT for {manifest['site_name']} ({manifest['site_url']}).",
        "Each section is prefixed with [src: url#anchor] — cite these as the location.",
        "",
    ]
    for _entry, frag in frags:
        parts.append(frag)
        parts.append("")
    corpus = "\n".join(parts)
    _atomic_write(data_dir / "corpus.md", corpus)
    manifest.setdefault("verdict", {})
    manifest["verdict"]["corpus_tokens"] = count_tokens(corpus)
    manifest["updated_at"] = now_iso()
    manifest = compute_verdict(manifest, cfg)
    save_manifest(data_dir, manifest)


def init_manifest(cfg: Config) -> dict:
    return {
        "site_url": cfg.site.start_url,
        "site_name": cfg.site.name,
        "generated_at": now_iso(),
        "updated_at": now_iso(),
        "tokenizer": TOKENIZER_LABEL,
        "model": {"id": cfg.chat.model, "context_window": cfg.chat.context_window},
        "budgets": {
            "pass_tokens": cfg.budgets.pass_tokens,
            "warn_tokens": cfg.budgets.warn_tokens,
            "safety_margin_pct": cfg.budgets.safety_margin_pct,
        },
        "discovery": None,
        "verdict": {"corpus_tokens": 0},
        "pages": [],
        "skipped": [],
    }
