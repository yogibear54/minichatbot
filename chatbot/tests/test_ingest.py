from core.pipeline import full_ingest, refresh_site
from tests.helpers import PAGES, make_config
import tests.helpers as helpers


def test_full_ingest_fixture(tmp_path, fixture_site):
    cfg = make_config(tmp_path, fixture_site)
    manifest = full_ingest(cfg)
    urls = {p["url"] for p in manifest["pages"]}
    assert f"{fixture_site}/docs/export" in urls
    assert manifest["verdict"]["status"] == "PASS"
    assert manifest["verdict"]["corpus_tokens"] > 0
    assert manifest["discovery"] == "sitemap"
    corpus = (tmp_path / "data" / "site" / "corpus.md").read_text()
    assert f"[src: {fixture_site}/docs/export#csv-export]" in corpus
    # page files exist and slugs are recorded
    assert any(p["file"] == "docs-export.md" for p in manifest["pages"])


def test_refresh_adds_new_page_exactly_once(tmp_path, fixture_site):
    """Regression: refresh appended new pages to the manifest twice (duplicate
    corpus entries + double token counts)."""
    from core import corpus

    cfg = make_config(tmp_path, fixture_site)
    manifest = full_ingest(cfg)
    assert all(p["url"] != f"{fixture_site}/docs/api" for p in manifest["pages"])

    # the site publishes a new page (linked from the sitemap)
    old_sitemap = helpers.SITEMAP
    helpers.SITEMAP = old_sitemap.replace(
        "</urlset>",
        f"  <url><loc>{fixture_site}/docs/api</loc><lastmod>2026-02-01</lastmod></url>\n</urlset>")
    PAGES["/docs/api"] = ("<html><head><title>API Docs</title></head><body>"
                          "<h1>API Docs</h1><p>The REST API accepts JSON payloads at "
                          "https://api.example.com/v1 and returns a sync identifier for every "
                          "request. Webhook events are delivered within a few minutes when a "
                          "sync completes. Retries use exponential backoff for up to one hour. "
                          "All endpoints require a bearer token issued in the dashboard.</p>"
                          "</body></html>")
    try:
        summary = refresh_site(cfg)
    finally:
        helpers.SITEMAP = old_sitemap
        PAGES.pop("/docs/api", None)

    assert f"+ {fixture_site}/docs/api" in summary["changed"]
    m2 = corpus.load_manifest(cfg.data_dir)
    urls = [p["url"] for p in m2["pages"]]
    assert urls.count(f"{fixture_site}/docs/api") == 1
    body = (tmp_path / "data" / "site" / "corpus.md").read_text()
    assert body.count(f"[src: {fixture_site}/docs/api]") == 1


def test_first_party_md_dedup_and_normalization(tmp_path, fixture_site):
    """llms.txt first-party markdown + boilerplate dedup + heading normalization + tables."""
    cfg = make_config(tmp_path, fixture_site)
    manifest = full_ingest(cfg)

    # 1. first-party markdown preferred for /about (via llms.txt)
    assert manifest.get("first_party_markdown") == [f"{fixture_site}/about"]
    about_entry = next(p for p in manifest["pages"] if p["url"].endswith("/about"))
    assert about_entry["file"] == "about.md"
    about_md = (tmp_path / "data" / "site" / "pages" / "about.md").read_text()
    assert "About Us (first-party)" in about_md          # first-party content, not rendered HTML

    corpus = (tmp_path / "data" / "site" / "corpus.md").read_text()

    # 2. boilerplate CTA (repeated on every html page) removed from the corpus
    assert "Talk to us about your project today" not in corpus
    assert manifest["boilerplate_sections_removed"] >= 3
    # ...but unique content survives
    assert "Choose the export button and pick CSV format." in corpus

    # 3. heading-level normalization: pricing jumps h1 -> h4, clamped to h1 -> h2
    assert "#### Enterprise" not in corpus
    assert "## Enterprise" in corpus

    # 4. html table converted to a markdown table
    assert "| Format | Speed |" in corpus


def test_html_tables_to_markdown():
    from core.ingest import _html_tables_to_markdown
    md = _html_tables_to_markdown(
        '<p>x</p><table><tr><th>Format</th><th>Speed</th></tr><tr><td>CSV</td><td>Fast</td></tr></table>')
    assert "| Format | Speed |" in md and "<table" not in md
    kept = _html_tables_to_markdown('<table><tr><td colspan="2">wide</td></tr></table>')
    assert "<table" in kept                              # merged cells stay raw HTML
