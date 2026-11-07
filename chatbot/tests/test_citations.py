import json

from server.citations import (KnowledgeBase, extract_citations, rescue_citations,
                              validate_citations)

PAGE_MD = """# Export Guide
Reports can be exported as CSV from the Reports tab.
## CSV Export
Choose the export button and pick CSV format.
"""


def _kb(tmp_path):
    kb = KnowledgeBase(tmp_path)
    from core.markdown_utils import normalize_loose, normalize_ws
    kb.pages["https://s.com/export"] = {
        "title": "Export Guide",
        "md_normalized": normalize_ws(PAGE_MD),
        "md_loose": normalize_loose(PAGE_MD),
        "anchors": {"export-guide", "csv-export"},
    }
    return kb


def test_extract_fenced_block():
    text = 'Here is how.\n\n```json\nCITATIONS:\n[{"url": "https://s.com/export"}]\n```'
    display, raw = extract_citations(text)
    assert display == "Here is how."
    assert raw == [{"url": "https://s.com/export"}]


def test_extract_unfenced_and_broken_json():
    display, raw = extract_citations('Answer.\nCITATIONS:\n[{"url":"u"}]')
    assert display == "Answer." and raw == [{"url": "u"}]
    display, raw = extract_citations("Answer.\nCITATIONS:\n[{broken")
    assert display == "Answer." and raw is None
    display, raw = extract_citations("Plain answer, no block.")
    assert raw is None and "Plain" in display


def test_validate_citations(tmp_path):
    kb = _kb(tmp_path)
    good = {"url": "https://s.com/export", "anchor": "#csv-export",
            "quote": "Choose the export button and pick CSV format.", "label": "CSV Export"}
    bad_url = {"url": "https://s.com/other", "anchor": "#x", "quote": "Choose the export button and pick CSV format."}
    bad_quote = {"url": "https://s.com/export", "anchor": "#csv-export", "quote": "this sentence is not on the page"}
    bad_anchor_heals = {"url": "https://s.com/export", "anchor": "#missing",
                        "quote": "Reports can be exported as CSV from the Reports tab."}
    out = validate_citations([bad_url, bad_quote, bad_anchor_heals, good], kb)
    assert len(out) == 2
    assert out[0]["url"] == "https://s.com/export"          # anchor dropped, page-level kept
    assert out[0]["anchor"] == ""
    assert out[1]["url"] == "https://s.com/export#csv-export"


def test_rescue_citations(tmp_path):
    kb = _kb(tmp_path)
    kb.pages["https://s.com/pricing"] = {
        "title": "Pricing", "md_normalized": "the team plan costs nine dollars per month",
        "anchors": set()}
    out = rescue_citations("what does the team plan cost per month?", kb)
    assert out and out[0]["url"] == "https://s.com/pricing"
    assert out[0]["rescued"] is True and out[0]["quote"] == ""
    out = rescue_citations("export", kb)
    assert out and out[0]["url"] == "https://s.com/export"   # single-term question
    assert rescue_citations("hello ???", kb) == []            # only stopwords/short terms
    assert rescue_citations("quantum entanglement budget matrix", kb) == []  # below floor
