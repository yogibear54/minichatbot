import json

from server.citations import (KnowledgeBase, extract_citations, fuzzy_anchor,
                              ground_citations, rescue_citations, validate_citations)

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
        "md_raw": PAGE_MD,
        "md_normalized": normalize_ws(PAGE_MD),
        "md_loose": normalize_loose(PAGE_MD),
        "anchors": {"export-guide", "csv-export"},
        "headings": [{"level": 1, "text": "Export Guide", "slug": "export-guide"},
                     {"level": 2, "text": "CSV Export", "slug": "csv-export"}],
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


def test_rescue_prefers_current_page(tmp_path):
    kb = _kb(tmp_path)
    kb.pages["https://s.com/pricing"] = {
        "title": "Pricing", "md_normalized": "the team plan costs nine dollars per month",
        "anchors": set()}
    kb.pages["https://s.com/contact"] = {
        "title": "Contact", "md_normalized": "fill out the form and we respond quickly",
        "anchors": set()}
    # question with zero lexical overlap on the current page -> current page still wins
    out = rescue_citations("what is this page about?", kb, prefer_url="https://s.com/contact")
    assert out and out[0]["url"] == "https://s.com/contact"


def test_fuzzy_anchor(tmp_path):
    kb = _kb(tmp_path)
    headings = kb.pages["https://s.com/export"]["headings"]
    assert fuzzy_anchor("CSV Export", headings) == ("csv-export", "CSV Export")
    assert fuzzy_anchor("csv export", headings) == ("csv-export", "CSV Export")     # text ci
    assert fuzzy_anchor("cvs export", headings)[0] == "csv-export"                  # typo fuzzy
    assert fuzzy_anchor("export guide", headings)[0] == "export-guide"
    assert fuzzy_anchor("completely unrelated topic xyz", headings) == ("", "")


def test_ground_citations_repairs_and_derives(tmp_path):
    kb = _kb(tmp_path)
    # wrong slug-ish anchor + stitched quote -> fuzzy-fixed anchor + DERIVED verbatim quote
    out = ground_citations([{"url": "https://s.com/export", "anchor": "cvs-export",
                             "section": "CSV Export",
                             "quote": "export button pick CSV format choose the"}], kb)
    assert len(out) == 1
    assert out[0]["url"] == "https://s.com/export#csv-export"
    assert out[0]["derived"] is True
    assert out[0]["quote"] and out[0]["quote"] in kb.pages["https://s.com/export"]["md_loose"]

    # verbatim quote kept as-is, not replaced
    out = ground_citations([{"url": "https://s.com/export", "section": "CSV Export",
                             "quote": "Choose the export button and pick CSV format."}], kb)
    assert out[0].get("derived") is not True
    assert out[0]["quote"] == "Choose the export button and pick CSV format."

    # section-name-only citation (structured protocol) -> anchor + derived quote
    out = ground_citations([{"url": "https://s.com/export", "section": "CSV Export",
                             "quote": ""}], kb)
    assert out[0]["anchor"] == "csv-export" and out[0]["quote"]

    # unknown url dropped
    assert ground_citations([{"url": "https://s.com/nope", "section": "X"}], kb) == []


def test_extract_bare_array_without_marker():
    display, raw = extract_citations('Answer text.\n\n[{"url": "https://s.com/export", "anchor": "#csv-export"}]')
    assert display == "Answer text."
    assert raw and raw[0]["url"] == "https://s.com/export"


def test_extract_fenced_array_without_marker():
    display, raw = extract_citations('Answer.\n```json\n[{"url": "u", "quote": "q"}]\n```')
    assert display == "Answer."
    assert raw == [{"url": "u", "quote": "q"}]


def test_extract_bold_label_stripped():
    display, raw = extract_citations('Answer.\n**Citations:**\n[{"url": "u"}]')
    assert display == "Answer."                       # "**Citations:**" never shown
    assert raw == [{"url": "u"}]


def test_truncated_array_is_dropped_not_shown():
    display, raw = extract_citations('Answer.\nCITATIONS:\n[{"url": "u", "anchor": "x", "quote": "truncat')
    assert "[{" not in display and "truncat" not in display
    assert raw is None


def test_extract_bold_marker_is_label_not_block_start():
    # regression: "**Citations:**" matches the marker regex and used to leave "**" behind
    display, raw = extract_citations('Answer.\n**Citations:**\n[{"url": "u", "quote": "q"}]')
    assert display == "Answer."
    assert raw == [{"url": "u", "quote": "q"}]
