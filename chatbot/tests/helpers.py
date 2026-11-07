"""Shared test helpers: a tiny fixture website served locally, and config factory."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import yaml

PAGES = {
    "/": """<html><head><title>Acme Docs</title></head><body>
      <nav><a href="/docs/export">Export</a> <a href="/pricing">Pricing</a></nav>
      <h1>Welcome to Acme</h1>
      <p>Acme builds time-tracking software for small teams. Reports can be exported as CSV
      from the Reports tab. Our customers love the weekly summary emails and the mobile app.</p>
      <p>Getting started takes about five minutes: create an account, invite your team, and
      connect your calendar. Support is available around the clock via chat and email.</p>
      </body></html>""",
    "/docs/export": """<html><head><title>Export Guide</title></head><body>
      <h1>Export Guide</h1>
      <p>Reports can be exported as CSV from the Reports tab. Choose the export button and pick
      CSV format. Exports include time entries, project totals, and user summaries.</p>
      <h2>CSV Export</h2>
      <p>CSV files open directly in Excel, Numbers, or Google Sheets. Column order matches the
      report view. Large exports are emailed as a download link within a few minutes.</p>
      </body></html>""",
    "/pricing": """<html><head><title>Pricing</title></head><body>
      <h1>Pricing</h1>
      <p>The Starter plan is free for up to three users. The Team plan costs nine dollars per
      user per month and adds unlimited projects, integrations, and priority support.</p>
      <h2>Enterprise</h2>
      <p>Enterprise plans add single sign-on, audit logs, and a dedicated success manager.
      Contact sales for a quote tailored to your organization.</p>
      </body></html>""",
}

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{base}/</loc><lastmod>2026-01-01</lastmod></url>
  <url><loc>{base}/docs/export</loc><lastmod>2026-01-02</lastmod></url>
  <url><loc>{base}/pricing</loc><lastmod>2026-01-03</lastmod></url>
</urlset>"""

Handler = type("H", (BaseHTTPRequestHandler,), {
    "base": "",
    "do_GET": lambda self: (
        setattr(self, "path", self.path.split("?")[0]),
        self.send_response(200),
        self.send_header("Content-Type",
                         "application/xml" if self.path == "/sitemap.xml" else "text/html"),
        self.end_headers(),
        self.wfile.write(
            SITEMAP.format(base=Handler.base).encode() if self.path == "/sitemap.xml"
            else PAGES.get(self.path.rstrip("/") or "/", "<html><body>missing</body></html>").encode()
        ),
    ),
    "log_message": lambda self, *a: None,
})


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
