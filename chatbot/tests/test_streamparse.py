import pytest

from server.streamparse import StreamedAnswerParser

JSON_OK = '{"answer": "Hello world", "citations": []}'


def test_simple_object():
    p = StreamedAnswerParser()
    assert p.feed(JSON_OK) == "Hello world"
    assert p.final_display() == "Hello world"


def test_arbitrary_chunking_same_result():
    p = StreamedAnswerParser()
    out = ""
    for i in range(0, len(JSON_OK), 3):
        out += p.feed(JSON_OK[i:i + 3])
    assert out == "Hello world"


def test_escapes_decoded():
    p = StreamedAnswerParser()
    got = p.feed('{"answer": "line1\\nline2 \\"quoted\\" tab\\t"}')
    assert got == 'line1\nline2 "quoted" tab\t'


def test_unicode_escape():
    p = StreamedAnswerParser()
    assert p.feed('{"answer": "caf\\u00e9 time"}') == "café time"


def test_citations_payload_never_leaks():
    raw = '{"answer": "Answer text.", "citations": [{"url": "https://x.com/a", "quote": "secret words here"}]}'
    p = StreamedAnswerParser()
    got = p.feed(raw) + p.feed("")
    assert got == "Answer text."
    assert "secret" not in got and "x.com" not in got


def test_answer_not_first_key():
    p = StreamedAnswerParser()
    assert p.feed('{"citations": [], "answer": "X"}') == "X"


def test_whitespace_between_key_and_value():
    p = StreamedAnswerParser()
    assert p.feed('{ "answer" :\n "Spaced" }') == "Spaced"


def test_partial_stream_then_more():
    p = StreamedAnswerParser()
    out = p.feed('{"answer": "Hel')
    out += p.feed('lo"}')
    assert out == "Hello"


def test_unterminated_answer_returns_partial():
    p = StreamedAnswerParser()
    p.feed('{"answer": "Partial ans')
    assert p.final_display() == "Partial ans"


def test_no_answer_key_returns_empty():
    p = StreamedAnswerParser()
    assert p.feed('{"citations": []}') == ""
