"""Incrementally extract the display text of the "answer" field from a JSON object
that arrives as streamed tokens (structured protocol). Tolerant to chunk boundaries,
standard JSON escapes, and citation payloads after the answer string."""
from __future__ import annotations

import re

_ANSWER_KEY_RE = re.compile(r'"answer"\s*:\s*"')
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
            '"': '"', "\\": "\\", "/": "/"}


class StreamedAnswerParser:
    """feed(delta) -> newly decoded answer text to display (often empty).
    The parser never emits anything from the citations payload."""

    LOOKING, IN_STR, DONE = 0, 1, 2

    def __init__(self) -> None:
        self.raw = ""
        self.display = ""
        self.state = self.LOOKING
        self.i = 0  # parse pointer into self.raw

    def feed(self, delta: str) -> str:
        self.raw += delta
        return self._drain()

    def final_display(self) -> str:
        self._drain()
        return self.display

    # -- internals -------------------------------------------------------------
    def _drain(self) -> str:
        raw = self.raw
        out: list[str] = []
        i = self.i
        while True:
            if self.state == self.LOOKING:
                m = _ANSWER_KEY_RE.search(raw, i)
                if not m:
                    break
                i = m.end()          # just past the opening quote
                self.state = self.IN_STR
                continue
            if self.state == self.IN_STR:
                while i < len(raw):
                    c = raw[i]
                    if c == "\\":
                        if i + 1 >= len(raw):
                            break    # need more raw to complete the escape
                        nxt = raw[i + 1]
                        if nxt == "u":
                            if i + 6 > len(raw):
                                break
                            cp = int(raw[i + 2:i + 6], 16)
                            if 0xD800 <= cp <= 0xDBFF and raw[i + 6:i + 8] == "\\u" and i + 12 <= len(raw):
                                lo = int(raw[i + 8:i + 12], 16)
                                if 0xDC00 <= lo <= 0xDFFF:
                                    out.append(chr(((cp - 0xD800) << 10) + (lo - 0xDC00) + 0x10000))
                                    i += 12
                                    continue
                            out.append(chr(cp) if not (0xD800 <= cp <= 0xDFFF) else "\ufffd")
                            i += 6
                            continue
                        if nxt in _ESCAPES:
                            out.append(_ESCAPES[nxt])
                        else:
                            out.append(nxt)
                        i += 2
                        continue
                    if c == '"':
                        i += 1
                        self.state = self.DONE
                        break
                    out.append(c)
                    i += 1
                if self.state == self.DONE:
                    break
                break                    # IN_STR ran out of raw
            break                        # DONE
        self.i = i
        self.display += "".join(out)
        return "".join(out)


class StreamMarkerSuppressor:
    """Strips [src: ...] corpus markers from a token stream. Holds a small tail so a
    marker split across deltas is still caught; a runaway marker (no closing bracket)
    releases after 400 held characters."""

    TAIL = 6
    MAX_HOLD = 400

    def __init__(self) -> None:
        self.display = ""
        self.pending = ""       # held text (possible partial marker or inside marker)
        self.in_marker = False

    def feed(self, delta: str) -> str:
        buf = self.pending + delta
        self.pending = ""
        out: list[str] = []
        i = 0
        low = buf.lower()
        n = len(buf)
        while i < n:
            if self.in_marker:
                end = low.find("]", i)
                if end == -1:
                    self.pending = buf[i:]
                    if len(self.pending) > self.MAX_HOLD:      # runaway -> release
                        out.append(self.pending)
                        self.pending = ""
                        self.in_marker = False
                    break
                i = end + 1
                self.in_marker = False
                continue
            nxt = low.find("[src:", i)
            if nxt == -1:
                safe_end = max(i, n - self.TAIL)               # hold possible partial "[src:"
                out.append(buf[i:safe_end])
                self.pending = buf[safe_end:]
                break
            out.append(buf[i:nxt])                             # text before the marker
            i = nxt
            self.in_marker = True
        text = "".join(out)
        self.display += text
        return text

    def flush(self) -> str:
        out, self.pending = self.pending, ""
        self.display += out
        return out

    @property
    def emitted(self) -> str:
        return self.display
