import time

from server.sessions import Store


def test_history_window(tmp_path):
    s = Store(tmp_path / "t.db", history_turns=2)
    sid = s.ensure_session(None)
    for i in range(5):
        s.append_turn(sid, f"q{i}", f"a{i}")
    hist = s.get_history(sid)
    assert len(hist) == 2
    assert hist[0]["question"] == "q3" and hist[-1]["answer"] == "a4"
    assert s.ensure_session(sid) == sid


def test_rate_bucket(tmp_path):
    s = Store(tmp_path / "t.db")
    key = "chat:1.2.3.4"
    results = [s.allow(key, per_minute=6, burst=2) for _ in range(4)]
    assert results == [True, True, False, False]
    time.sleep(0.05)  # tiny refill, still blocked
    assert s.allow(key, 6, 2) is False
