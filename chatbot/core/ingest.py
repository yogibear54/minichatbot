"""Site ingestion: discovery (sitemap/robots + crawl fallback), fetch, extraction to markdown."""
from __future__ import annotations

import logging
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


def extract_markdown(page_html: str, url: str, fallback_title: str) -> FetchedPage | None:
    """Main-content extraction -> markdown via trafilatura (JS-only pages return None)."""
    from trafilatura import extract

    md = extract(
        page_html, url=url, output_format="markdown", include_links=True,
        include_tables=True, include_comments=False, include_images=False,
        favor_recall=True, with_metadata=False,
    )
    if not md or len(md.strip()) < MIN_MARKDOWN_CHARS:
        return None
    try:
        doc = lhtml.fromstring(page_html)
        title = _extract_title(doc, fallback_title)
    except lhtml.ParserError:
        title = fallback_title
    return FetchedPage(url=url, title=title, markdown=md.strip() + "\n")


def fetch_and_extract(client: httpx.Client, url: str,
                      etag: str | None = None, last_modified: str | None = None,
                      skip_unchanged: bool = False,
                      renderer: "JsRenderer | None" = None) -> tuple[FetchedPage | None, str]:
    """Fetch one URL; returns (page|None, status). status: ok|unchanged|skipped|error.
    With a JsRenderer, the page is rendered in headless Chromium first (JS-only sites,
    PLAN §9.1) and conditional-GET skipping is bypassed (raw HTML never changes even
    when injected content does — the hash diff remains the source of truth)."""
    if renderer is not None:
        html = renderer.html(url)
        if html is None:
            return None, "error"
        page = extract_markdown(html, canonical_url(url), url)
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
        self.browser = self._pw.chromium.launch(headless=True)
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
