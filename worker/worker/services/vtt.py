from __future__ import annotations

import html
import re
from typing import TypedDict


_TS_RE = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{3})"
    r"\s*-->\s*"
    r"(?P<eh>\d{1,2}):(?P<em>\d{2}):(?P<es>\d{2})\.(?P<ems>\d{3})"
)

_INLINE_TS_RE = re.compile(r"<\d{1,2}:\d{2}:\d{2}\.\d{3}>")
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_WS_RE = re.compile(r"\s+")


class Cue(TypedDict):
    start: float
    end: float
    text: str


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _clean_text(raw: str) -> str:
    s = _INLINE_TS_RE.sub("", raw)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def parse_vtt(content: str) -> list[Cue]:
    raw_cues: list[Cue] = []
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
            text_lines.append(lines[i])
            i += 1
        cleaned = _clean_text(" ".join(text_lines))
        if cleaned:
            raw_cues.append(Cue(start=start, end=end, text=cleaned))
    return _dedup_rolling(raw_cues)


def _dedup_rolling(cues: list[Cue]) -> list[Cue]:
    """Collapse YouTube rolling-caption duplicates.

    YouTube auto-subs emit progressively-growing cues with the same time-range,
    then a final "block" cue. Keep only the longest text per (start, end) group,
    and drop a cue whose text is a strict prefix of the next cue's text.
    """
    if not cues:
        return cues

    # Step 1: collapse exact (start, end) duplicates, keep longest text.
    by_range: dict[tuple[float, float], Cue] = {}
    order: list[tuple[float, float]] = []
    for c in cues:
        key = (c["start"], c["end"])
        if key not in by_range:
            by_range[key] = c
            order.append(key)
        elif len(c["text"]) > len(by_range[key]["text"]):
            by_range[key] = c
    collapsed = [by_range[k] for k in order]

    # Step 2: drop a cue if its text is a strict prefix of the next cue's text.
    result: list[Cue] = []
    for idx, c in enumerate(collapsed):
        if idx + 1 < len(collapsed):
            nxt = collapsed[idx + 1]
            if nxt["text"].startswith(c["text"]) and len(nxt["text"]) > len(c["text"]):
                continue
        result.append(c)
    return result
