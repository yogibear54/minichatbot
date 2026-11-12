from core.tokens import hard_cap_tokens, verdict_for


def test_hard_cap_reserves():
    cap = hard_cap_tokens(131072, 700, 10)
    assert 100000 < cap <= 118000  # window minus 10% margin minus reserves


def test_verdict_pass_warn_fail():
    kw = dict(context_window=131072, max_output=700, pass_tokens=40000,
              warn_tokens=100000, safety_margin_pct=10)
    assert verdict_for(1000, **kw)[0] == "PASS"
    assert verdict_for(39000, **kw)[0] == "PASS"
    assert verdict_for(41000, **kw)[0] == "WARN"
    assert verdict_for(99000, **kw)[0] == "WARN"
    assert verdict_for(120000, **kw)[0] == "FAIL"
    assert verdict_for(400000, **kw)[0] == "FAIL"


def test_token_cache_falls_back_to_tmp_when_repo_unwritable(monkeypatch):
    """Read-only install (Docker /app) -> BPE cache lands in the system temp dir."""
    import os
    import tempfile
    from pathlib import Path

    import core.tokens as T

    repo_dir = Path(T.__file__).resolve().parent.parent / "data" / ".token_cache"
    tmp_dir = Path(tempfile.gettempdir()) / "site-chatbot-token-cache"
    real_mkdir = Path.mkdir

    def fake_mkdir(self, *args, **kwargs):
        if self == repo_dir:
            raise PermissionError(13, "Permission denied", str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    T._encoding.cache_clear()
    try:
        assert T.count_tokens("hello world") > 0
        assert os.environ["TIKTOKEN_CACHE_DIR"] == str(tmp_dir)
        assert tmp_dir.exists()
    finally:
        T._encoding.cache_clear()
