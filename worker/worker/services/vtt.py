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
TRANSCRIPT_VERSION = "vtt-clean-v2"


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
    youtube_timed = bool(_INLINE_TS_RE.search(content))
    # YouTube uses a space-only line for the empty upper display row. It is
    # caption content, not a block separator; consuming it drops the next words.
    for block in content.replace("\r\n", "\n").split("\n\n"):
        lines = block.splitlines()
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        m = _TS_RE.match(lines[timing_index].strip())
        if not m:
            continue
        start = _ts_to_sec(m["h"], m["m"], m["s"], m["ms"])
        end = _ts_to_sec(m["eh"], m["em"], m["es"], m["ems"])
        if end - start < 0.04:
            continue
        text_lines = [line.strip() for line in lines[timing_index + 1:] if line.strip()]
        timed_lines = [line for line in text_lines if _INLINE_TS_RE.search(line)]
        selected = (timed_lines or text_lines[-1:]) if youtube_timed else text_lines
        cleaned = _clean_text(" ".join(selected))
        if cleaned:
            raw_cues.append(Cue(start=start, end=max(start + 0.1, end), text=cleaned))
    if youtube_timed:
        return _group_phrases(raw_cues)
    return _dedup_rolling(raw_cues)


def _inline_ts_to_sec(value: str) -> float:
    hours, minutes, seconds = value.strip("<>").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _group_phrases(cues: list[Cue]) -> list[Cue]:
    groups: list[Cue] = []
    for cue in cues:
        previous = groups[-1] if groups else None
        if (previous and cue["start"] - previous["end"] < 1.7
                and cue["end"] - previous["start"] <= 12
                and len(previous["text"]) < 200
                and not re.search(r"[.!?]$", previous["text"])):
            previous["end"] = cue["end"]
            previous["text"] += " " + cue["text"]
        else:
            groups.append(Cue(**cue))
    return groups


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

    # Step 2: rolling auto-captions commonly repeat the previous line at the
    # start of the next cue. Keep only the newly spoken suffix while retaining
    # the original cue timestamps.
    result: list[Cue] = []
    previous_words: list[str] = []
    for c in collapsed:
        words = c["text"].split()
        if previous_words:
            maximum = min(len(previous_words), len(words))
            overlap = 0
            for size in range(maximum, 2, -1):
                if [w.casefold() for w in previous_words[-size:]] == [w.casefold() for w in words[:size]]:
                    overlap = size
                    break
            words = words[overlap:]
        if words:
            result.append(Cue(start=c["start"], end=c["end"], text=" ".join(words)))
        previous_words = c["text"].split()
    return result
