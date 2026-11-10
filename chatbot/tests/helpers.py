"""Shared test helpers: a tiny fixture website served locally, and config factory."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urljoin

import yaml
import pytest

CTA = ('<section class="cta"><h2>Ready to start?</h2><p>Talk to us about your project '
       'today and we will respond within one business day.</p></section>')

PAGES = {
    "/": f"""<html><head><title>Acme Docs</title></head><body>
      <nav><a href="/docs/export">Export</a> <a href="/pricing">Pricing</a> <a href="/about">About</a></nav>
      <h1>Welcome to Acme</h1>
      <p>Acme builds time-tracking software for small teams. Reports can be exported as CSV
      from the Reports tab. Our customers love the weekly summary emails and the mobile app.</p>
      <p>Getting started takes about five minutes: create an account, invite your team, and
      connect your calendar. Support is available around the clock via chat and email.</p>
      {CTA}
      </body></html>""",
    "/docs/export": f"""<html><head><title>Export Guide</title></head><body>
      <h1>Export Guide</h1>
      <p>Reports can be exported as CSV from the Reports tab. Choose the export button and pick
      CSV format. Exports include time entries, project totals, and user summaries.</p>
      <h2>CSV Export</h2>
      <p>CSV files open directly in Excel, Numbers, or Google Sheets. Column order matches the
      report view. Large exports are emailed as a download link within a few minutes.</p>
      <table><tr><th>Format</th><th>Speed</th></tr><tr><td>CSV</td><td>Fast</td></tr></table>
      {CTA}
      </body></html>""",
    "/pricing": f"""<html><head><title>Pricing</title></head><body>
      <h1>Pricing</h1>
      <p>The Starter plan is free for up to three users. The Team plan costs nine dollars per
      user per month and adds unlimited projects, integrations, and priority support.</p>
      <h4>Enterprise</h4>
      <p>Enterprise plans add single sign-on, audit logs, and a dedicated success manager.
      Contact sales for a quote tailored to your organization.</p>
      {CTA}
      </body></html>""",
}

ABOUT_MD = """# About Us (first-party)

We are a small Hong Kong based team building fast web platforms. First-party markdown
keeps the exact authoring structure: headings, lists and links stay intact.
"""

LLMS_TXT = """# Acme
- [About Us (first-party)](/about.md): about page in markdown
"""

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{base}/</loc><lastmod>2026-01-01</lastmod></url>
  <url><loc>{base}/about</loc><lastmod>2026-01-04</lastmod></url>
  <url><loc>{base}/docs/export</loc><lastmod>2026-01-02</lastmod></url>
  <url><loc>{base}/pricing</loc><lastmod>2026-01-03</lastmod></url>
</urlset>"""


class Handler(BaseHTTPRequestHandler):
    base = ""

    def do_GET(self):
        path = self.path.split("?")[0]
        self.send_response(200)
        if path == "/sitemap.xml":
            ctype, body = "application/xml", SITEMAP.format(base=Handler.base)
        elif path == "/llms.txt":
            ctype, body = "text/plain", LLMS_TXT
        elif path.endswith(".md"):
            ctype, body = "text/markdown", ABOUT_MD
        else:
            ctype = "text/html"
            body = PAGES.get(path.rstrip("/") or "/", "<html><body>missing</body></html>")
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def make_config(tmp_path, base_url, **overrides):
    cfg_data = {
        "site": {"name": "Acme", "start_url": base_url + "/", "max_pages": 20,
                 "crawl_fallback": True},
        "chat": {"base_url": "http://llm.invalid/v1", "api_key_env": "NOPE",
                 "model": "test-model"},
        "guard": {"enabled": False},
    }
    cfg_data.update(overrides)
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg_data))
    from core.config import load_config
    return load_config(p)


@pytest.fixture()
def fixture_site():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    Handler.base = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield Handler.base
    server.shutdown()
