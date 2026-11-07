import json

import pytest
from fastapi.testclient import TestClient

from core.pipeline import full_ingest
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
