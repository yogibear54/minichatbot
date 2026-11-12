"""Site ingestion: discovery (sitemap/robots + crawl fallback), fetch, extraction to markdown."""
from __future__ import annotations

import logging
import re

from core.markdown_utils import normalize_paragraph_wrapping, normalize_ws
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from lxml import html as lhtml

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; SiteChatbot/1.0; +https://github.com/site-chatbot)"
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                   "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid"}
MIN_MARKDOWN_CHARS = 200  # below this, a page is boilerplate-only or JS-rendered -> skipped

# Navigational/generated junk that wastes corpus tokens on many sites (docs generators).
DEFAULT_EXCLUDES = ["/search", "genindex", "modindex", "_modules"]


def excluded(url: str, extra: list[str] | None = None) -> bool:
    path = urlsplit(url).path.lower()
    return any(pat.lower() in path for pat in (DEFAULT_EXCLUDES + (extra or [])))


@dataclass
class FetchedPage:
    url: str            # final canonical URL after redirects
    title: str
    markdown: str
    etag: str | None = None
    last_modified: str | None = None


def canonical_url(url: str) -> str:
    """Strip fragments + tracking params; normalize scheme/host case. Path kept as-is."""
    p = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if k.lower() not in TRACKING_PARAMS]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", urlencode(q), ""))


def dedupe_key(url: str) -> str:
    """Trailing-slash-insensitive identity used to avoid double-fetching /a and /a/."""
    p = urlsplit(canonical_url(url))
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme, p.netloc, path, p.query, ""))


def same_site(url: str, site_url: str, include_subdomains: bool = False) -> bool:
    u, s = urlsplit(url).netloc.lower(), urlsplit(site_url).netloc.lower()
    if u.startswith("www."):
        u = u[4:]
    if s.startswith("www."):
        s = s[4:]
    if u == s:
        return True
    return include_subdomains and u.endswith("." + s)


def _parse_sitemap_xml(xml_text: str) -> tuple[list[tuple[str, str | None]], bool]:
    """Returns ([(loc, lastmod)], is_index)."""
    import xml.etree.ElementTree as ET

    locs: list[tuple[str, str | None]] = []
    is_index = False
    try:
        root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return locs, False
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag == "sitemapindex":
            is_index = True
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag == "loc" and (el.text or "").strip():
            locs.append((el.text.strip(), None))
    # lastmod lives in <url><loc/><lastmod/></url> — re-scan for pairs
    pairs: list[tuple[str, str | None]] = []
    for url_el in root.iter():
        if url_el.tag.rsplit("}", 1)[-1].lower() in {"url", "sitemap"}:
            loc = mod = None
            for child in url_el.iter():
                ctag = child.tag.rsplit("}", 1)[-1].lower()
                if ctag == "loc" and child.text:
                    loc = child.text.strip()
                elif ctag == "lastmod" and child.text:
                    mod = child.text.strip()
            if loc:
                pairs.append((loc, mod))
    return (pairs or locs), is_index


def discover_urls(client: httpx.Client, site_url: str, max_pages: int,
                  include_subdomains: bool = False, crawl_fallback: bool = True,
                  exclude: list[str] | None = None, map_host: str | None = None
                  ) -> tuple[dict[str, str | None], str]:
    """Returns ({canonical_url: lastmod|None}, method). Sitemap-first, robots hint, crawl fallback."""
    found: dict[str, str | None] = {}          # canonical_url -> lastmod
    key_map: dict[str, str] = {}               # dedupe_key -> canonical_url
    method = "crawl"
    base = urlsplit(site_url)
    origin = f"{base.scheme}://{base.netloc}"

    def add_page(cu: str, lastmod: str | None = None) -> bool:
        if map_host:
            # rewrite sitemap URLs onto the start_url origin (scheme+host), keep the path —
            # for local/staging crawls whose sitemap lists production URLs
            p = urlsplit(cu)
            base = urlsplit(site_url)
            cu = urlunsplit((base.scheme, base.netloc, p.path, p.query, ""))
        if not same_site(cu, site_url, include_subdomains) or ".xml" in urlsplit(cu).path:
            return False
        if excluded(cu, exclude):
            return False
        k = dedupe_key(cu)
        if k not in key_map:
            key_map[k] = cu
            found[cu] = lastmod
            return True
        if lastmod:
            found[key_map[k]] = lastmod
        return False

    def add_sitemap(url: str, depth: int = 0) -> None:
        if depth > 2 or len(found) >= max_pages:
            return
        nonlocal method
        try:
            r = client.get(url)
            if r.status_code != 200:
                return
            entries, is_index = _parse_sitemap_xml(r.text)
        except httpx.HTTPError:
            return
        if entries:
            method = "sitemap"
        if is_index:
            for loc, _ in entries[:20]:
                add_sitemap(loc, depth + 1)
            return
        for loc, lastmod in entries:
            if add_page(canonical_url(loc), lastmod) and len(found) >= max_pages:
                return

    # 1) conventional sitemap locations, 2) robots.txt hints
    for candidate in ("/sitemap.xml", "/sitemap_index.xml"):
        add_sitemap(origin + candidate)
    try:
        r = client.get(origin + "/robots.txt")
        if r.status_code == 200:
            for line in r.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    add_sitemap(line.split(":", 1)[1].strip())
    except httpx.HTTPError:
        pass

    # Sitemap-first, but when crawl_fallback is enabled ALWAYS also crawl nav links:
    # sitemaps are often incomplete (forgotten pages, new sections) — the homepage nav
    # catches the rest (e.g. lotus-creations.com's sitemap omits /services).
    if crawl_fallback:
        for cu, lastmod in _crawl(client, site_url, max(0, max_pages - len(found)),
                                  include_subdomains).items():
            add_page(cu, lastmod)
    if not found:
        return {}, "none"
    return found, method


def _crawl(client: httpx.Client, site_url: str, max_pages: int,
           include_subdomains: bool) -> dict[str, str | None]:
    """BFS crawl of same-site links from the start URL (sitemap-less sites)."""
    from urllib.robotparser import RobotFileParser

    base = urlsplit(site_url)
    origin = f"{base.scheme}://{base.netloc}"
    rp = RobotFileParser()
    try:
        rp.parse(client.get(origin + "/robots.txt").text.splitlines())
    except httpx.HTTPError:
        rp.allow_all = True

    out: dict[str, str | None] = {}
    queue = [canonical_url(site_url)]
    seen: set[str] = set()
    depth_of = {dedupe_key(site_url): 0}
    max_depth = 2
    while queue and len(out) < max_pages:
        url = queue.pop(0)
        k = dedupe_key(url)
        if k in seen or depth_of.get(k, 0) > max_depth:
            continue
        seen.add(k)
        if not rp.can_fetch(USER_AGENT, url):
            continue
        try:
            r = client.get(url)
        except httpx.HTTPError:
            continue
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            continue
        final = canonical_url(str(r.url))
        out[dedupe_key(final)] = final
        if depth_of.get(k, 0) >= max_depth:
            continue
        try:
            doc = lhtml.fromstring(r.text)
        except lhtml.ParserError:
            continue
        for a in doc.iter("a"):
            href = a.get("href")
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            nxt = canonical_url(urljoin(final, href))
            if not nxt.startswith("http") or not same_site(nxt, site_url, include_subdomains):
                continue
            nk = dedupe_key(nxt)
            if nk not in seen:
                depth_of[nk] = depth_of.get(k, 0) + 1
                queue.append(nxt)
    return out


def _extract_title(doc: lhtml.HtmlElement, fallback: str) -> str:
    for xp in (".//meta[@property='og:title']/@content", ".//title/text()", ".//h1/text()"):
        vals = doc.xpath(xp)
        if vals and str(vals[0]).strip():
            return str(vals[0]).strip()
    return fallback


def _dom_text_len(page_html: str) -> int:
    """Length of all body text — the reference an extractor is judged against."""
    try:
        doc = lhtml.fromstring(page_html)
        return len(" ".join(t.strip() for t in doc.xpath("//body//text()") if t.strip()))
    except lhtml.ParserError:
        return 0


_CHROME_XPATH = (".//script|.//style|.//noscript|.//nav|.//header|.//footer|"
                 ".//form|.//button|.//*[@aria-hidden='true']|.//*[@hidden]")  # NB: aside kept — sidebars often hold real content


def _fallback_markdown(page_html: str) -> str | None:
    """Last-resort conversion of the (visible) DOM to markdown — used when trafilatura's
    main-content heuristic drops large parts of a page (e.g. multi-section layouts where
    no single <article> dominates). Captures everything; precision traded for recall."""
    try:
        from markdownify import markdownify

        doc = lhtml.fromstring(page_html)
        body = doc.find("body")
        root = body if body is not None else doc
        for el in root.xpath(_CHROME_XPATH):
            el.getparent().remove(el)
        md = markdownify(lhtml.tostring(root), heading_style="ATX", bullets="-")
        md = _html_tables_to_markdown(md)
        md = re.sub(r"\n{3,}", "\n\n", md).strip()
        return md or None
    except Exception as e:
        log.warning("fallback markdown failed: %s", e)
        return None


def _html_tables_to_markdown(md: str) -> str:
    """markdownify keeps <table> markup; LLMs read markdown tables far better.
    Simple tables convert; colspan/rowspan/nested tables stay as HTML."""
    for tbl in re.findall(r"<table[\s\S]*?</table>", md, flags=re.IGNORECASE):
        converted = _convert_table(tbl)
        if converted:
            md = md.replace(tbl, converted)
    return md


def _convert_table(tbl_html: str) -> str | None:
    try:
        table = lhtml.fromstring(tbl_html)
        if table.xpath(".//table") or table.xpath(".//*[@colspan or @rowspan]"):
            return None                     # nested/merged cells -> keep raw HTML
        rows = []
        for tr in table.xpath(".//tr"):
            cells = [normalize_ws(" ".join(cell.itertext())).replace("|", "\\|") or " "
                     for cell in tr.xpath("./th|./td")]
            if cells:
                rows.append(cells)
        if not rows:
            return None
        width = max(len(r) for r in rows)
        rows = [r + [" "] * (width - len(r)) for r in rows]
        lines = ["| " + " | ".join(rows[0]) + " |",
                 "|" + "---|" * width]
        for r in rows[1:]:
            lines.append("| " + " | ".join(r) + " |")
        return "\n".join(lines)
    except Exception as e:
        log.warning("table conversion failed: %s", e)
        return None


GATE_KEEP_RATIO = 0.6   # extraction must keep >=60% of visible DOM text, else fallback


def _choose_best(page_html: str, md: str | None, dom_len: int) -> str | None:
    """Quality gate: if extraction kept <60% of the visible DOM text, convert the whole
    cleaned DOM instead and keep whichever is longer. (A low ratio usually means
    trafilatura truncated trailing card/list sections on section-based pages.)"""
    best = (md or "").strip()
    if dom_len > 400 and len(best) < GATE_KEEP_RATIO * dom_len:
        fb = _fallback_markdown(page_html)
        if fb and len(fb) > len(best):
            log.info("extraction gate: trafilatura kept %d/%d DOM chars -> using DOM fallback (%d)",
                     len(best), dom_len, len(fb))
            return fb
    return best or None


def extract_markdown(page_html: str, url: str, fallback_title: str,
                     dom_len: int | None = None) -> FetchedPage | None:
    """Main-content extraction -> markdown; with a DOM-text quality gate and a
    whole-DOM fallback for pages trafilatura under-extracts."""
    from trafilatura import extract

    base_kwargs = dict(url=url, output_format="markdown", include_links=True,
                       include_tables=True, include_comments=False,
                       include_images=False, with_metadata=False)
    # recall-first: run both precision modes and MERGE. Measured trade-off on this repo:
    # favor_recall keeps the heading hierarchy but drops markdown tables; the default
    # mode keeps tables but flattens all headings. Neither alone is acceptable
    # (RESEARCH: heading hierarchy is the #1 LLM quality check; tables are unrecoverable).
    # Strategy: hierarchy wins the primary slot; missing tables are grafted from the other.
    md = extract(page_html, favor_recall=True, **base_kwargs) or ""
    md_alt = extract(page_html, favor_recall=False, **base_kwargs) or ""

    def _headings(m: str) -> int:
        return m.count("\n#") + (1 if m.lstrip().startswith("#") else 0)

    def _tables(m: str) -> int:
        return m.count("|---")

    def _score(m: str) -> tuple[int, int, int]:
        return (_headings(m), _tables(m), len(m))

    if _score(md_alt) > _score(md):
        md, md_alt = md_alt, md

    # graft tables that only exist in the other candidate (header-line dedupe)
    alt_tables = [b.strip() for b in re.split(r"\n\s*\n", md_alt)
                  if b.lstrip().startswith("|") and "|---" in b
                  and b.strip().splitlines()[0] not in md]
    if alt_tables:
        md = md.rstrip() + "\n\n" + "\n\n".join(alt_tables)
    if dom_len is None:
        dom_len = _dom_text_len(page_html)
    best = _choose_best(page_html, md, dom_len)
    if not best or len(best.strip()) < MIN_MARKDOWN_CHARS:
        return None
    best = normalize_paragraph_wrapping(best)
    try:
        doc = lhtml.fromstring(page_html)
        title = _extract_title(doc, fallback_title)
    except lhtml.ParserError:
        title = fallback_title
    return FetchedPage(url=url, title=title, markdown=best.strip() + "\n")


def fetch_and_extract(client: httpx.Client, url: str,
                      etag: str | None = None, last_modified: str | None = None,
                      skip_unchanged: bool = False,
                      renderer: "JsRenderer | None" = None) -> tuple[FetchedPage | None, str]:
    """Fetch one URL; returns (page|None, status). status: ok|unchanged|skipped|error.
    With a JsRenderer, the page is rendered in headless Chromium first (JS-only sites,
    PLAN §9.1) and conditional-GET skipping is bypassed (raw HTML never changes even
    when injected content does — the hash diff remains the source of truth)."""
    if renderer is not None:
        html = renderer.visible_html(url)
        if html is None:
            return None, "error"
        page = extract_markdown(html, canonical_url(url), url, dom_len=_dom_text_len(html))
        if page is None:
            return None, "skipped"
        return page, "ok"
    headers = {"User-Agent": USER_AGENT}
    if skip_unchanged and etag:
        headers["If-None-Match"] = etag
    if skip_unchanged and last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        r = client.get(url, headers=headers, follow_redirects=True)
    except httpx.HTTPError as e:
        log.warning("fetch failed %s: %s", url, e)
        return None, "error"
    if r.status_code == 304:
        return None, "unchanged"
    if r.status_code != 200:
        return None, "error"
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype:
        return None, "skipped"
    page = extract_markdown(r.text, canonical_url(str(r.url)), url)
    if page is None:
        return None, "skipped"
    page.etag = r.headers.get("etag")
    page.last_modified = r.headers.get("last-modified")
    return page, "ok"


class JsRenderer:
    """Headless-Chromium renderer for JS-only sites (the Crawl4AI approach from
    RESEARCH §2). Start once per run; reuse across pages; close() at the end."""

    def __init__(self, timeout_ms: int = 20000):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        # containers run as root and share a small /dev/shm — Chromium's sandbox and
        # shared-memory device break in that environment (standard Playwright-in-Docker
        # pattern), so both flags are applied when root or explicitly requested.
        launch_args = []
        import os as _os
        if _os.name == "posix" and (_os.geteuid() == 0 or _os.environ.get("CHROMIUM_NO_SANDBOX")):
            launch_args += ["--no-sandbox", "--disable-dev-shm-usage"]
        self.browser = self._pw.chromium.launch(headless=True, args=launch_args)
        self.page = self.browser.new_page(user_agent=USER_AGENT)
        self.timeout_ms = timeout_ms

    def html(self, url: str) -> str | None:
        import time

        try:
            try:
                self.page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
            except Exception:
                pass  # networkidle timeout -> take whatever has rendered
            # page.content() can race with JS-driven navigation — retry briefly
            for attempt in range(4):
                try:
                    return self.page.content()
                except Exception:
                    time.sleep(0.3 * (attempt + 1))
            return None
        except Exception as e:
            log.warning("render failed %s: %s", url, e)
            return None

    def visible_html(self, url: str) -> str | None:
        """Rendered page reduced to its VISIBLE body content: chrome (nav/header/footer/
        forms/buttons/dialogs) and computed-invisible elements (display:none, opacity:0 —
        GSAP pre-animation states, closed modals) are removed. <aside> is kept: sidebars
        often carry real page content (contact cards, related info). Extraction then sees
        exactly what a visitor sees, and the DOM-text quality gate in extract_markdown
        gets an accurate reference."""
        try:
            try:
                self.page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
            except Exception:
                pass
            # Reveal scroll-triggered content (GSAP ScrollTrigger / IntersectionObserver):
            # walk down the page so pre-animation opacity:0 states flip to visible
            # BEFORE the strip pass — otherwise below-the-fold cards (e.g. contact
            # info) are dropped as invisible and never reach the corpus.
            try:
                self.page.evaluate("""async () => {
                  const step = window.innerHeight * 0.7;
                  for (let i = 0, y = 0;
                       i < 40 && y < document.body.scrollHeight;
                       i++, y += step) {
                    window.scrollTo(0, y);
                    await new Promise(r => setTimeout(r, 60));
                  }
                  window.scrollTo(0, 0);
                  await new Promise(r => setTimeout(r, 150));
                }""")
            except Exception:
                pass
            for _ in range(4):
                try:
                    self.page.evaluate("""() => {
                      const doomed = [];
                      document.body.querySelectorAll('script,style,noscript,nav,header,footer,form,button,[aria-hidden="true"],[role="dialog"],dialog,.modal,[hidden]').forEach(e => doomed.push(e));
                      document.body.querySelectorAll('*').forEach(e => {
                        const s = getComputedStyle(e);
                        if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) doomed.push(e);
                      });
                      doomed.forEach(e => e.remove());
                    }""")
                    break
                except Exception:
                    time.sleep(0.3 * (attempt + 1))
            return self.page.content()
        except Exception as e:
            log.warning("visible render failed %s: %s", url, e)
            return None

    def close(self) -> None:
        try:
            self.browser.close()
            self._pw.stop()
        except Exception:
            pass


def progress_printer(label: str) -> Callable[[str], None]:
    def p(msg: str) -> None:
        print(f"[{label}] {msg}")
    return p
