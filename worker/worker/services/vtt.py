from __future__ import annotations

import re
from typing import TypedDict


_TS_RE = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{3})"
    r"\s*-->\s*"
    r"(?P<eh>\d{1,2}):(?P<em>\d{2}):(?P<es>\d{2})\.(?P<ems>\d{3})"
)


class Cue(TypedDict):
    start: float
    end: float
    text: str


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(content: str) -> list[Cue]:
    cues: list[Cue] = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = _TS_RE.match(line)
        if not m:
            i += 1
            continue
        start = _ts_to_sec(m["h"], m["m"], m["s"], m["ms"])
        end = _ts_to_sec(m["eh"], m["em"], m["es"], m["ems"])
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        if text_lines:
            cues.append(Cue(start=start, end=end, text=" ".join(text_lines)))
    return cues
