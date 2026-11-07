from core.markdown_utils import annotate_page, extract_headings, normalize_ws, slugify


def test_slugify():
    assert slugify("CSV Export") == "csv-export"
    assert slugify("What's new? (v2)") == "whats-new-v2"
    assert slugify("  Multiple   spaces ") == "multiple-spaces"


def test_extract_headings_dedupes():
    md = "# Setup\nintro\n## Requirements\n## Requirements\nmore"
    hs = extract_headings(md)
    slugs = [h.slug for h in hs]
    assert slugs == ["setup", "requirements", "requirements-1"]


def test_annotate_page_markers():
    # page already begins with an H1 -> no injected duplicate title
    md = "# Guide\nbody text\n## CSV Export\nexport details\n## PDF\npdf details"
    out = annotate_page("https://s.com/guide", "Some Other Title", md)
    assert out.count("# Guide") == 1
    assert "[src: https://s.com/guide#guide]" in out
    assert "[src: https://s.com/guide#csv-export]" in out
    assert "[src: https://s.com/guide#pdf]" in out
    # page without H1 -> title injected once at top
    md2 = "intro text\n## Section A\ndetails"
    out2 = annotate_page("https://s.com/x", "Page Title", md2)
    assert out2.count("# Page Title") == 1
    assert out2.startswith(f"[src: https://s.com/x]\n# Page Title")


def test_normalize_ws():
    assert normalize_ws("a\n  b\tc") == "a b c"
