"""Citation extraction, validation and server-side grounding (PLAN §3.2, L3 output layer)."""
from __future__ import annotations

import difflib
import json
import re
from typing import Callable

from core.markdown_utils import (
    Heading,
    normalize_loose,
    normalize_ws,
    section_text_for_anchor,
    slugify,
)

_CITATIONS_RE = re.compile(r"CITATIONS\s*:\s*", re.IGNORECASE)   # protocol marker
_BARE_ARRAY_RE = re.compile(r"\[\s*\{\s*\"url\"\s*:")               # bare/fenced array w/o marker
_TRAILING_LABEL_RE = re.compile(r"(?:#{1,6}\s*)?[*_\s]*citations?[*_\s]*:?[*_\s]*$", re.IGNORECASE)


_SRC_MARKER_RE = re.compile(r"\s*\[\s*src\s*:\s*[^\]]*\]", re.IGNORECASE)


def sanitize_markers(text: str) -> str:
    """Remove any [src: url#anchor] citation markers that leaked into an answer."""
    cleaned = _SRC_MARKER_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).rstrip()


def extract_citations(text: str) -> tuple[str, list[dict] | None]:
    """Split a finished answer into (display_text, raw_citation_dicts|None).

    Recognizes the block in three shapes (whichever appears last):
      - marker form:   CITATIONS: [...]        (possibly fenced)
      - fenced array:  ```json [ {"url": ...}] ```
      - bare array:    [{"url": ...}]
    Parse errors yield (text_without_block, None) so broken JSON never reaches the
    widget; truncated (max_output) blocks land here too and the rescue chip covers
    navigation instead.
    """
    marker = array = None
    for m in _CITATIONS_RE.finditer(text):
        marker = m
    for m in _BARE_ARRAY_RE.finditer(text):
        array = m

    block_start = None
    if array:
        # the array is the real content — prefer it (a bolded "**Citations:**" label
        # also matches the marker regex and would otherwise land mid-markdown)
        block_start = array.start()
    elif marker:
        block_start = marker.start()
    if block_start is None:
        return text, None

    display = text[:block_start].rstrip()
    display = _TRAILING_LABEL_RE.sub("", display).rstrip()            # stray "Citations:" label
    display = re.sub(r"```[a-z]*\s*$", "", display).rstrip()          # trailing fence opener
    bracket = text.find("[", block_start)
    if bracket == -1:
        return display, None
    blob = text[bracket:].strip()
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
                "suggestions": entry.get("suggestions", []),
                "md_raw": md,
                "md_normalized": normalize_ws(md),
                "md_loose": normalize_loose(md),
                "anchors": {h["slug"] for h in entry.get("headings", [])},
                "headings": entry.get("headings", []),
            }

    def get(self, url: str) -> dict | None:
        return self.pages.get(url)

    def resolve(self, url: str) -> str | None:
        """Map a visitor's current URL (with fragment, trailing slash variants) to a
        manifest page URL; None if unknown."""
        from core.markdown_utils import normalize_ws
        from urllib.parse import urlsplit, urlunsplit

        def variants(u):
            p = urlsplit(u)
            base = urlunsplit((p.scheme, p.netloc, p.path or "/", "", ""))
            for cand in (base, base.rstrip("/"), base + "/"):
                yield cand

        for cand in variants(url):
            if cand in self.pages:
                return cand
        return None


def ground_citations(candidates: list[dict], kb: KnowledgeBase, limit: int = 3) -> list[dict]:
    """Repair + validate model citations server-side (the §3.2 grounding upgrade):
    1. resolve the URL against the manifest (allowlist)
    2. anchor: keep valid slugs; otherwise fuzzy-match the section name against headings
    3. quote: keep verbatim matches; otherwise DERIVE it from the stored section text
       (so highlighting works without the model having copied text perfectly)
    Falls back to page-level citations; every emitted target is guaranteed real."""
    grounded: list[dict] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        url = str(c.get("url", "")).strip()
        page = kb.get(url)
        if page is None:
            continue

        anchor_req = str(c.get("anchor", "")).strip().lstrip("#")
        section = str(c.get("section", "")).strip()
        anchor, heading_text = "", ""
        if anchor_req and anchor_req in page["anchors"]:
            anchor = anchor_req
            heading_text = next((str(h["text"]) for h in page["headings"]
                                 if str(h["slug"]) == anchor), "")
        elif section:
            anchor, heading_text = fuzzy_anchor(section, page["headings"])

        quote = normalize_ws(str(c.get("quote", "")))
        derived = False
        quote_ok = 8 <= len(quote) <= 400 and (
            quote in page["md_normalized"] or normalize_loose(quote) in page["md_loose"])
        if not quote_ok:
            dheads = [Heading(level=int(h["level"]), text=str(h["text"]), slug=str(h["slug"]))
                      for h in page["headings"]]
            dq = section_text_for_anchor(page["md_raw"], dheads, anchor)
            if len(dq) >= 8:
                quote, derived = dq, True
            else:
                quote = ""

        label = heading_text or str(c.get("label") or page["title"])
        grounded.append({
            "url": url + (f"#{anchor}" if anchor else ""),
            "anchor": anchor,
            "quote": quote,
            "label": str(label),
            **({"derived": True} if derived else {}),
        })
        if len(grounded) >= limit:
            break
    return grounded


def fuzzy_anchor(section: str, headings: list[dict]) -> tuple[str, str]:
    """Match a plain-text section name to the page's heading tree.
    Returns (anchor_slug, heading_text) or ("", ""). Tolerates slug-ish inputs and typos."""
    if not section:
        return "", ""
    s = normalize_ws(section).strip().lstrip("#").strip()
    if not s:
        return "", ""
    sslug = slugify(s)

    def by_text(name: str) -> tuple[str, str] | None:
        for h in headings:
            if normalize_ws(str(h["text"])).lower() == name.lower():
                return str(h["slug"]), str(h["text"])
        return None

    for h in headings:                                   # exact slug / exact text
        if str(h["slug"]) == sslug:
            return str(h["slug"]), str(h["text"])
    hit = by_text(s)
    if hit:
        return hit
    close = difflib.get_close_matches(sslug, [str(h["slug"]) for h in headings], n=1, cutoff=0.6)
    if close:
        for h in headings:
            if str(h["slug"]) == close[0]:
                return str(h["slug"]), str(h["text"])
    close = difflib.get_close_matches(s.lower(),
                                      [normalize_ws(str(h["text"])).lower() for h in headings],
                                      n=1, cutoff=0.6)
    if close:
        hit = by_text(close[0])
        if hit:
            return hit
    return "", ""


def validate_citations(candidates: list[dict], kb: KnowledgeBase, limit: int = 3) -> list[dict]:
    """Strict path (kept for reference/compat): keep only citations whose url, anchor,
    and verbatim quote all check out. Prefer ground_citations for serving."""
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
        quote_loose = normalize_loose(quote)
        if quote not in page["md_normalized"] and quote_loose not in page["md_loose"]:
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
def rescue_citations(question: str, kb: KnowledgeBase, limit: int = 1,
                     prefer_url: str | None = None) -> list[dict]:
    """Last resort when the model omits/invalidates the CITATIONS block (PLAN §9.6):
    cite the page(s) with the strongest capped-term-frequency density with the question.
    A page-aware question (prefer_url) ranks the current page first when it passes the
    floor. Page-level only (no anchor/quote fabrications)."""
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
        if url == prefer_url and freq < floor:
            freq = floor        # current page always qualifies when the model stayed silent
        if freq >= floor:
            score = freq / (1 + words / 500)
            if url == prefer_url:
                score += 10     # strong boost: questions on a page are usually about it
            scored.append((score, url, page))
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
