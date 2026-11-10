from server.prompts import CITATION_SCHEMA, build_messages, build_system_prompt


def test_trailer_prompt_has_citations_block():
    p = build_system_prompt("S", "CORPUS", protocol="trailer")
    assert "CITATIONS:" in p and "<corpus>" in p and "CORPUS" in p


def test_structured_prompt_has_no_trailing_block_instruction():
    p = build_system_prompt("S", "CORPUS", protocol="structured")
    assert "CITATIONS:" not in p
    assert "Respond with the JSON object" in p
    assert "CORPUS" in p


def test_schema_shape():
    schema = CITATION_SCHEMA["json_schema"]["schema"]
    assert schema["properties"]["answer"]["type"] == "string"          # first = streamable
    assert schema["required"] == ["answer", "citations"]
    assert schema["additionalProperties"] is False


def test_page_context_injected_dynamically():
    msgs = build_messages("SYS", [], "what is this page?", "https://s.com/about", "About")
    last = msgs[-1]["content"]
    assert "currently viewing https://s.com/about" in last
    assert '"About"' in last
    assert "what is this page?" in last
    assert msgs[0]["content"] == "SYS"                                  # cached prefix intact
    # stored history question stays clean (no context line)
    assert build_messages("SYS", [{"question": "q", "answer": "a"}], "hi")[1]["content"] == "q"


def test_no_page_context_without_url():
    last = build_messages("SYS", [], "hello")[-1]["content"]
    assert "currently viewing" not in last
