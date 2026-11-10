import json

import pytest
from fastapi.testclient import TestClient

from core.pipeline import full_ingest
from server.llm import LLMError
from server.main import create_app
from tests.helpers import make_config

QUOTE = "Choose the export button and pick CSV format."


class StubLLM:
    """Records messages; streams an answer + a valid CITATIONS block."""

    calls = []

    def __init__(self, *a, **k):
        pass

    async def stream_chat(self, model, messages, max_tokens, temperature):
        StubLLM.calls.append(messages)
        yield "You can export reports as CSV. "
        yield "CITATIONS:\n"
        yield json.dumps([{
            "url": StubLLM.page_url, "anchor": "#csv-export",
            "quote": QUOTE, "label": "CSV Export",
        }])

    async def complete(self, *a, **k):
        return "safe"


@pytest.fixture()
def app_and_client(tmp_path, fixture_site, monkeypatch):
    cfg = make_config(tmp_path, fixture_site)
    cfg.guard.enabled = True
    manifest = full_ingest(cfg)
    page_url = next(p["url"] for p in manifest["pages"] if p["url"].endswith("/docs/export"))
    StubLLM.page_url = page_url
    monkeypatch.setattr("server.main.OpenAICompatClient", StubLLM)
    app = create_app(cfg)
    return TestClient(app), page_url


def test_chat_sse_flow(app_and_client):
    client, page_url = app_and_client
    res = client.post("/api/chat", json={"message": "How do I export CSVs?"})
    assert res.status_code == 200
    events = [json.loads(line[5:]) for line in res.text.splitlines() if line.startswith("data:")]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "meta"
    assert "token" in kinds
    assert "citations" in kinds
    assert kinds[-1] == "done"
    cits = next(e for e in events if e["type"] == "citations")["citations"]
    assert cits[0]["url"] == f"{page_url}#csv-export"
    assert cits[0]["quote"] == QUOTE
    # tokens streamed to the user must NOT contain the CITATIONS block
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert "CITATIONS" not in tokens


def test_info_and_assets(app_and_client):
    client, _ = app_and_client
    info = client.get("/api/info").json()
    assert info["site_name"] == "Acme"
    assert info["verdict"] == "PASS"
    assert "Acme" in client.get("/chat").text
    assert "cb-launcher" in client.get("/widget.js").text
    assert client.get("/chat.css").status_code == 200
    assert "READY" in client.get("/chat.js").text


def test_invalid_message_rejected(app_and_client):
    client, _ = app_and_client
    assert client.post("/api/chat", json={"message": ""}).status_code == 400


# ------------------------- structured protocol (json_schema) + trailer fallback -----------------

class StructuredStub:
    """Emits a schema-shaped JSON object; quote intentionally EMPTY so the server must
    derive it from the stored section (grounding upgrade under test)."""

    calls = []
    page_url = ""

    def __init__(self, *a, **kwargs):
        pass

    async def stream_chat(self, model, messages, max_tokens, temperature, response_format=None):
        StructuredStub.calls.append({"response_format": response_format})
        assert response_format is not None, "structured protocol must send the schema"
        yield '{"answer": "Use the export button and pick CSV format.'
        yield '", "citations": [{"url": "%s", "section": "CSV Export", "quote": "", "label": "CSV Export"}]}' % StructuredStub.page_url


def _structured_client(tmp_path, fixture_site, monkeypatch, stub):
    cfg = make_config(tmp_path, fixture_site)
    cfg.chat.protocol = "structured"
    manifest = full_ingest(cfg)
    stub.page_url = next(p["url"] for p in manifest["pages"] if p["url"].endswith("/docs/export"))
    monkeypatch.setattr("server.main.OpenAICompatClient", stub)
    return TestClient(create_app(cfg))


def test_structured_protocol_grounded(tmp_path, fixture_site, monkeypatch):
    client = _structured_client(tmp_path, fixture_site, monkeypatch, StructuredStub)
    res = client.post("/api/chat", json={"message": "How do I export CSVs?"})
    events = [json.loads(l[5:]) for l in res.text.splitlines() if l.startswith("data:")]
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "Use the export button and pick CSV format."      # JSON framing stripped
    cits = next(e for e in events if e["type"] == "citations")["citations"]
    assert cits[0]["url"].endswith("/docs/export#csv-export")
    assert cits[0]["quote"] == "CSV files open directly in Excel, Numbers, or Google Sheets."  # derived: 1st sentence of the section
    assert cits[0].get("derived") is True
    assert StructuredStub.calls[0]["response_format"]["type"] == "json_schema"


class StructuredThenFallbackStub(StructuredStub):
    """Structured call fails upstream -> server must retry with the trailer protocol."""

    calls = []   # own list — do NOT share StructuredStub.calls across tests

    async def stream_chat(self, model, messages, max_tokens, temperature, response_format=None):
        StructuredThenFallbackStub.calls.append({"response_format": response_format})
        if response_format is not None:
            raise LLMError("upstream 400: response_format not supported by provider")
        yield "You can export reports as CSV. "
        yield "CITATIONS:\n"
        yield json.dumps([{"url": StructuredThenFallbackStub.page_url, "anchor": "#csv-export",
                           "quote": QUOTE, "label": "CSV Export"}])


def test_structured_falls_back_to_trailer(tmp_path, fixture_site, monkeypatch):
    client = _structured_client(tmp_path, fixture_site, monkeypatch, StructuredThenFallbackStub)
    res = client.post("/api/chat", json={"message": "How do I export CSVs?"})
    events = [json.loads(l[5:]) for l in res.text.splitlines() if l.startswith("data:")]
    kinds = [e["type"] for e in events]
    assert "error" not in kinds
    assert kinds[-1] == "done" and events[-1]["finish"] == "answer"
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert "You can export reports as CSV." in tokens
    cits = next(e for e in events if e["type"] == "citations")["citations"]
    assert cits[0]["url"].endswith("/docs/export#csv-export")
    # two upstream calls: failed structured attempt, then the trailer retry without schema
    assert len(StructuredThenFallbackStub.calls) == 2
    assert StructuredThenFallbackStub.calls[0]["response_format"] is not None
    assert StructuredThenFallbackStub.calls[1]["response_format"] is None


# ---------------------- serve-time verdict recompute on model switch -----------------

def test_serve_recomputes_verdict_when_window_shrinks(tmp_path, fixture_site):
    cfg = make_config(tmp_path, fixture_site)
    full_ingest(cfg)                                   # PASS under 131072
    cfg.chat.context_window = 2000                     # "switched" to a tiny-context model
    mpath = tmp_path / "data" / "site" / "manifest.json"
    m = json.loads(mpath.read_text())
    assert m["model"]["context_window"] == 131072      # stale on disk
    with pytest.raises(RuntimeError, match="FAIL"):
        create_app(cfg)                                # refuses to serve
    m2 = json.loads(mpath.read_text())
    assert m2["verdict"]["status"] == "FAIL"           # recomputed + persisted
    assert m2["model"]["context_window"] == 2000


def test_serve_keeps_pass_when_window_grows(tmp_path, fixture_site):
    cfg = make_config(tmp_path, fixture_site)
    full_ingest(cfg)
    cfg.chat.context_window = 1_000_000                # switch to a bigger-window model
    client = TestClient(create_app(cfg))               # must NOT refuse
    assert client.get("/api/health").json()["verdict"] == "PASS"
    m = json.loads((tmp_path / "data" / "site" / "manifest.json").read_text())
    assert m["model"]["context_window"] == 1_000_000   # metadata persisted, no re-ingest
