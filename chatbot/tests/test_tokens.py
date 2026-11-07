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
