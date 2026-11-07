"""SQLite store: sessions, chat history, token-bucket rate limits, guard block log."""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
  session_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  PRIMARY KEY (session_id, idx)
);
CREATE TABLE IF NOT EXISTS rate_buckets (
  key TEXT PRIMARY KEY,
  tokens REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS block_log (
  ts REAL NOT NULL,
  session_id TEXT,
  category TEXT NOT NULL,
  snippet TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path, history_turns: int = 4):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.executescript(_SCHEMA)
        self.lock = threading.Lock()
        self.history_turns = history_turns

    # -- sessions -------------------------------------------------------------
    def ensure_session(self, session_id: str | None) -> str:
        with self.lock:
            sid = session_id or uuid.uuid4().hex
            if session_id:
                row = self.conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone()
                if not row:
                    self.conn.execute("INSERT INTO sessions(id, created_at) VALUES (?,?)",
                                      (sid, time.time()))
                    self.conn.commit()
            else:
                self.conn.execute("INSERT INTO sessions(id, created_at) VALUES (?,?)",
                                  (sid, time.time()))
                self.conn.commit()
            return sid

    def get_history(self, session_id: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT question, answer FROM turns WHERE session_id=? ORDER BY idx DESC LIMIT ?",
                (session_id, self.history_turns),
            ).fetchall()
        return [{"question": q, "answer": a} for q, a in reversed(rows)]

    def append_turn(self, session_id: str, question: str, answer: str) -> None:
        with self.lock:
            idx = self.conn.execute("SELECT COALESCE(MAX(idx),-1)+1 FROM turns WHERE session_id=?",
                                    (session_id,)).fetchone()[0]
            self.conn.execute(
                "INSERT INTO turns(session_id, idx, question, answer) VALUES (?,?,?,?)",
                (session_id, idx, question, answer))
            # prune old turns beyond the window
            self.conn.execute(
                "DELETE FROM turns WHERE session_id=? AND idx <= ? - ?",
                (session_id, idx, self.history_turns * 2))
            self.conn.commit()

    # -- rate limiting (token bucket) ------------------------------------------
    def allow(self, key: str, per_minute: float, burst: int) -> bool:
        rate = per_minute / 60.0
        now = time.time()
        with self.lock:
            row = self.conn.execute("SELECT tokens, updated_at FROM rate_buckets WHERE key=?",
                                    (key,)).fetchone()
            tokens, ts = (burst, now) if row is None else (row[0], row[1])
            tokens = min(burst, tokens + (now - ts) * rate)
            if tokens < 1.0:
                self.conn.execute(
                    "INSERT INTO rate_buckets(key, tokens, updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET tokens=?, updated_at=?",
                    (key, tokens, now, tokens, now))
                self.conn.commit()
                return False
            tokens -= 1.0
            self.conn.execute(
                "INSERT INTO rate_buckets(key, tokens, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET tokens=?, updated_at=?",
                (key, tokens, now, tokens, now))
            self.conn.commit()
            return True

    # -- guard block log --------------------------------------------------------
    def log_block(self, session_id: str, category: str, snippet: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO block_log(ts, session_id, category, snippet) VALUES (?,?,?,?)",
                (time.time(), session_id, category, snippet[:200]))
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()
