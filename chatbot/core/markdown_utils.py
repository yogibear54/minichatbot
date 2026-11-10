"""Markdown utilities: heading slugs, whitespace normalization, corpus annotation."""
from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_ANCHOR_RE = re.compile(r"\[#+\]\([^)]*\)")                 # trafilatura's [#](url)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")              # [text](url) -> text


def clean_heading_text(text: str) -> str:
    """Strip trailing anchor links / markdown links from heading text."""
    text = _MD_ANCHOR_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str) -> str:
    """GitHub-style anchor slug: lowercase, strip punctuation, spaces -> dashes."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)      # drop punctuation (keep word chars, spaces, dashes)
    s = re.sub(r"[\s_]+", "-", s)       # spaces/underscores -> dash
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def normalize_ws(text: str) -> str:
    """Collapse all whitespace runs to single spaces (quote matching)."""
    return re.sub(r"\s+", " ", text).strip()


def normalize_loose(text: str) -> str:
    """Whitespace + markdown-symbol stripped (bold/italics/headings/bullets removed)
    — second-chance match for quotes spanning inline formatting."""
    text = re.sub(r"[*_`#>\[\]]+", "", text)
    return normalize_ws(text)

@dataclass
class Heading:
    level: int
    text: str
    slug: str


def extract_headings(markdown: str) -> list[Heading]:
    """ATX headings (# .. ######) with cleaned text and per-page deduped slugs."""
    seen: dict[str, int] = {}
    out: list[Heading] = []
    for line in markdown.splitlines():
        m = _HEADING_RE.match(line)
        if not m:
            continue
        text = clean_heading_text(m.group(2))
        if not text:
            continue
        slug = slugify(text) or "section"
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        if n:
            slug = f"{slug}-{n}"
        out.append(Heading(level=len(m.group(1)), text=text, slug=slug))
    return out


def normalize_paragraph_wrapping(md: str) -> str:
    """Collapse soft line breaks inside paragraphs to spaces (GFM: single newlines in a
    paragraph render as spaces anyway). HTML-to-markdown converters leak source
    indentation as intra-paragraph newlines ("pick\n      CSV format.") which wastes
    tokens and breaks verbatim quote matching. Lists, tables, code fences, blockquotes
    and headings are left untouched."""
    blocks = re.split(r"\n\s*\n", md)
    out = []
    in_fence = False
    for block in blocks:
        if in_fence:
            out.append(block)
            if "```" in block:
                in_fence = False
            continue
        if "```" in block:
            in_fence = True
            out.append(block)
            continue
        first = block.lstrip()
        if (first.startswith(("#", ">", "|", "-", "*", "+"))
                or re.match(r"\d+[.)]\s", first)
                or "\n" not in block.strip()):
            out.append(block)               # structural blocks keep their line breaks
            continue
        out.append(normalize_ws(block))
    return "\n\n".join(out)


def section_text_for_anchor(md: str, headings: list[Heading], anchor: str,
                            max_words: int = 25) -> str:
    """First ~max_words words (loose-normalized) of the section under the heading with
    this slug — used to derive highlight quotes server-side (PLAN §3.2 grounding)."""
    if not anchor:
        return ""
    idx = next((i for i, h in enumerate(headings) if h.slug == anchor), None)
    if idx is None:
        return ""
    target = headings[idx]

    lines = md.splitlines()
    hs = list(extract_headings(md))
    hi = 0
    heading_line_idx = None
    for li, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            if hi < len(hs) and hs[hi].slug == anchor and clean_heading_text(m.group(2)).strip() == target.text:
                heading_line_idx = li
                break
            hi += 1
    if heading_line_idx is None:
        return ""

    content: list[str] = []
    for line in lines[heading_line_idx + 1:]:
        m2 = _HEADING_RE.match(line)
        if m2 and len(m2.group(1)) <= target.level:
            break
        content.append(line)
    text = normalize_loose(normalize_ws(" ".join(content)))
    words = text.split()
    if not words:
        return ""
    # prefer a complete first sentence (6+ words), else the first max_words words
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        s_words = sentence.split()
        if len(s_words) >= 6:
            return " ".join(s_words[:max_words])
    return " ".join(words[:max_words])


def annotate_page(url: str, title: str, markdown: str) -> str:
    """Build the corpus fragment for one page: a [src:] marker before each heading
    so the model can cite `url#slug` locations it can see inline."""
    lines = markdown.splitlines()
    headings = extract_headings(markdown)
    out: list[str] = []
    starts_with_h1 = bool(lines) and _HEADING_RE.match(lines[0]) and lines[0].startswith("# ")
    if not starts_with_h1:
        out += [f"[src: {url}]", f"# {title}", ""]
    # heading-level normalization (RESEARCH: structural hierarchy errors make LLMs
    # misunderstand document organization): page starts at level 1 and each heading is
    # clamped to at most previous+1, so jumps like h2 -> h4 become h2 -> h3. Slugs and
    # anchors are NOT renumbered — they stay identical to manifest/stored headings.
    hi = 0
    prev_level = 0 if starts_with_h1 else 1        # injected title already occupies level 1
    for line in lines:
        m = _HEADING_RE.match(line)
        if m and hi < len(headings):
            h = headings[hi]
            hi += 1
            level = min(h.level, prev_level + 1)
            prev_level = level
            out.append(f"[src: {url}#{h.slug}]")
            line = f"{'#' * level} {h.text}"       # cleaned heading (anchors/links stripped)
        out.append(line)
    return "\n".join(out).strip() + "\n"
