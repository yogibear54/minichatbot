import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import pytest

from core.config import load_config
from core.modelinfo import apply_model_limits, fetch_model_limits


def _fake_models_response(entries):
    class R:
        status_code = 200
        def json(self):
            return {"data": entries}
    return R()


def test_fetch_and_apply(monkeypatch, tmp_path):
    entries = [{"id": "meta-llama/llama-3.1-8b-instruct", "context_length": 131072,
                "top_provider": {"max_completion_tokens": 16384}},
               {"id": "mistralai/mistral-small-3.2-24b-instruct", "context_length": 256000,
                "top_provider": {"max_completion_tokens": 26214}}]
    monkeypatch.setattr(httpx, "get",
                        lambda url, headers=None, timeout=None: _fake_models_response(entries))
    cfg = load_config(tmp_path / "missing.yaml")          # pure dataclass defaults
    cfg.chat.model = "mistralai/mistral-small-3.2-24b-instruct"
    cfg.chat.context_window = 131072                     # stale config value
    info = fetch_model_limits(cfg.chat.base_url, cfg.chat.model)
    assert info == {"context_window": 256000, "max_completion": 26214}
    apply_model_limits(cfg, info)
    assert cfg.chat.context_window == 256000             # provider value wins
    assert cfg.chat.max_output == 900                    # under cap -> untouched


def test_max_output_clamped_to_provider_cap(monkeypatch, tmp_path, capsys):
    entries = [{"id": "m", "context_length": 16384,
                "top_provider": {"max_completion_tokens": 4096}}]
    monkeypatch.setattr(httpx, "get",
                        lambda url, headers=None, timeout=None: _fake_models_response(entries))
    cfg = load_config(tmp_path / "missing.yaml")
    cfg.chat.model = "m"
    cfg.chat.context_window = 16384
    cfg.chat.max_output = 9000
    apply_model_limits(cfg, fetch_model_limits(cfg.chat.base_url, cfg.chat.model))
    assert cfg.chat.max_output == 4096                   # clamped
    assert "clamping" in capsys.readouterr().out


def test_unreachable_provider_fails_open(monkeypatch, tmp_path):
    def boom(url, headers=None, timeout=None):
        raise httpx.ConnectError("nope")
    monkeypatch.setattr(httpx, "get", boom)
    cfg = load_config(tmp_path / "missing.yaml")
    assert fetch_model_limits(cfg.chat.base_url, cfg.chat.model) is None
    assert apply_model_limits(cfg, None) is None         # config values stay
    assert cfg.chat.context_window == 131072


def test_unknown_model_fails_open(monkeypatch, tmp_path):
    entries = [{"id": "other/model", "context_length": 4096}]
    monkeypatch.setattr(httpx, "get",
                        lambda url, headers=None, timeout=None: _fake_models_response(entries))
    cfg = load_config(tmp_path / "missing.yaml")
    assert fetch_model_limits(cfg.chat.base_url, "meta-llama/llama-3.1-8b-instruct") is None
