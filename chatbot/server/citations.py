"""Citation extraction + validation against the manifest (PLAN §3.2, L3 output layer)."""
from __future__ import annotations

import json
import re
from typing import Callable

from core.markdown_utils import normalize_ws

_CITATIONS_RE = re.compile(r"CITATIONS:\s*(\[)", re.IGNORECASE)


def extract_citations(text: str) -> tuple[str, list[dict] | None]:
    """Split a finished answer into (display_text, raw_citation_dicts|None).

    The CITATIONS block is everything from the marker (and any surrounding code
    fence) to the end of the message. Returns raw dicts unparsed; parse errors
    yield (text_without_block, None) so broken JSON never reaches the widget.
    """
    m = None
    for m in _CITATIONS_RE.finditer(text):
        pass
    if m is None:
        return text, None
    start = m.start()
    display = text[:start].rstrip()
    # strip a trailing code fence opener like ``` or ```json right before the marker
    display = re.sub(r"```[a-z]*\s*$", "", display).rstrip()
    blob = text[m.end() - 1:]  # starts at '['
    blob = blob.strip()
    if blob.endswith("```"):
        blob = blob[:-3].strip()
    try:
        arr = json.loads(blob)
        if not isinstance(arr, list):
            return display, None
    except json.JSONDecodeError:
        return display, None
    return display, [c for c in arr if isinstance(c, dict)]


class KnowledgeBase:
    """In-memory page cache for citation validation; reloadable after refresh."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.pages: dict[str, dict] = {}  # canonical url -> {title, md_normalized, anchors}

    def load(self, manifest: dict) -> None:
        self.pages.clear()
        pages_dir = self.data_dir / "pages"
        for entry in manifest["pages"]:
            f = pages_dir / entry["file"]
            try:
                md = f.read_text(encoding="utf-8")
            except OSError:
                continue
            self.pages[entry["url"]] = {
                "title": entry["title"],
                "md_normalized": normalize_ws(md),
                "anchors": {h["slug"] for h in entry.get("headings", [])},
            }

    def get(self, url: str) -> dict | None:
        return self.pages.get(url)


def validate_citations(candidates: list[dict], kb: KnowledgeBase, limit: int = 3) -> list[dict]:
    """Keep only citations whose url, anchor, and verbatim quote all check out."""
    valid: list[dict] = []
    for c in candidates:
        url = str(c.get("url", "")).strip()
        anchor = str(c.get("anchor", "")).strip().lstrip("#")
        quote = normalize_ws(str(c.get("quote", "")))
        page = kb.get(url)
        if page is None or not quote:
            continue
        if anchor and anchor not in page["anchors"]:
            anchor = ""  # drift -> degrade to page-level citation
        if len(quote) < 8 or len(quote) > 400:
            continue
        if quote not in page["md_normalized"]:
            continue
        label = c.get("label") or page["title"]
        valid.append({"url": url + (f"#{anchor}" if anchor else ""),
                      "anchor": anchor, "quote": quote, "label": str(label)})
        if len(valid) >= limit:
            break
    return valid


_STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "to", "of", "in", "on", "for",
              "and", "or", "how", "do", "does", "did", "i", "my", "me", "it", "this", "that",
              "with", "what", "where", "when", "can", "you", "your", "we", "us", "be", "from",
              "at", "as", "by", "if", "not", "no", "yes", "about"}


def rescue_citations(question: str, kb: KnowledgeBase, limit: int = 1) -> list[dict]:
    """Last resort when the model omits/invalidates the CITATIONS block (PLAN §9.6):
    cite the page(s) with the strongest term overlap with the question. Page-level
    only (no anchor/quote fabrications) and only above a conservative overlap floor."""
    terms = {w for w in re.findall(r"[a-z0-9]{3,}", question.lower()) if w not in _STOPWORDS}
    if not terms:
        return []
    floor = 1 if len(terms) == 1 else 2
    scored = []
    for url, page in kb.pages.items():
        hay = page["md_normalized"].lower()
        words = max(1, hay.count(" ") + 1)
        # capped term frequency, density-normalized: a focused page that repeats the
        # question's words beats a big page that mentions them once (PLAN §3.2 rescue)
        freq = sum(min(hay.count(t), 5) for t in terms)
        if freq >= floor:
            scored.append((freq / (1 + words / 500), url, page))
    scored.sort(reverse=True, key=lambda s: (round(s[0], 6), s[2]["title"]))
    return [{"url": url, "anchor": "", "quote": "", "label": page["title"],
             "rescued": True}
            for _ov, url, page in scored[:limit]]


def refusal_finish(display_text: str, had_valid_citations: bool,
                   is_refusal_hint: Callable[[str], bool] | None = None) -> str:
    """answer | refusal — refusal = no citations and refusal-detector says so."""
    if had_valid_citations:
        return "answer"
    if is_refusal_hint and is_refusal_hint(display_text):
        return "refusal"
    return "answer"
