from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "sample.vtt"


def test_parse_vtt_returns_cues_in_order():
    from worker.services.vtt import parse_vtt

    cues = parse_vtt(FIXTURE.read_text(encoding="utf-8"))

    assert len(cues) == 3
    assert cues[0]["start"] == pytest.approx(0.08, abs=0.01)
    assert cues[0]["end"] == pytest.approx(3.52, abs=0.01)
    assert cues[0]["text"] == "Привет, сегодня поговорим о времени."
    assert cues[1]["text"] == "Как мы его теряем по чуть-чуть."
    assert cues[2]["start"] == pytest.approx(7.20, abs=0.01)


def test_parse_vtt_skips_header_and_empty():
    from worker.services.vtt import parse_vtt

    cues = parse_vtt("WEBVTT\n\n")
    assert cues == []


def test_parse_vtt_handles_hour_timestamps():
    from worker.services.vtt import parse_vtt

    text = "WEBVTT\n\n01:02:03.400 --> 01:02:05.800\nдалёкий cue\n"
    cues = parse_vtt(text)
    assert cues[0]["start"] == pytest.approx(3723.4, abs=0.01)
    assert cues[0]["end"] == pytest.approx(3725.8, abs=0.01)
