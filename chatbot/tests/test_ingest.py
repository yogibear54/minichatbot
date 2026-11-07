from core.pipeline import full_ingest
from tests.helpers import make_config


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
