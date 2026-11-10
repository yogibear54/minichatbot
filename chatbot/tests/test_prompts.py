from server.prompts import build_messages


def test_page_context_injected_dynamically():
    msgs = build_messages("SYS", [], "what is this page?", "https://s.com/about", "About")
    last = msgs[-1]["content"]
    assert "currently viewing https://s.com/about" in last
    assert '"About"' in last
    assert "what is this page?" in last
    # static prefix untouched -> prompt caching preserved
    assert msgs[0]["content"] == "SYS"
    # stored history question stays clean (no context line)
    assert build_messages("SYS", [{"question": "q", "answer": "a"}], "hi")[1]["content"] == "q"


def test_no_page_context_without_url():
    last = build_messages("SYS", [], "hello")[ -1]["content"]
    assert "currently viewing" not in last
