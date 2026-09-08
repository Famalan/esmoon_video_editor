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


def test_parse_vtt_decodes_html_entities():
    from worker.services.vtt import parse_vtt

    text = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:02.000\n"
        "&gt;&gt; и он сказал &amp; ушёл\n"
    )
    cues = parse_vtt(text)
    assert cues[0]["text"] == ">> и он сказал & ушёл"


def test_parse_vtt_strips_inline_timestamp_tags():
    from worker.services.vtt import parse_vtt

    text = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:05.000\n"
        "слово<00:00:01.500><c> ещё</c><00:00:03.000><c> и ещё</c>\n"
    )
    cues = parse_vtt(text)
    assert cues[0]["text"] == "слово ещё и ещё"


def test_parse_vtt_dedups_rolling_prefix_cues():
    from worker.services.vtt import parse_vtt

    text = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:05.000\nпривет\n\n"
        "00:00:00.000 --> 00:00:05.000\nпривет мир\n\n"
        "00:00:00.000 --> 00:00:05.000\nпривет мир как дела\n\n"
        "00:00:05.000 --> 00:00:10.000\nновая фраза\n"
    )
    cues = parse_vtt(text)
    assert [c["text"] for c in cues] == ["привет мир как дела", "новая фраза"]


def test_parse_vtt_removes_previous_line_carried_into_next_cue():
    from worker.services.vtt import parse_vtt

    text = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:04.000\nмы обсуждаем сложную рабочую ситуацию\n\n"
        "00:00:04.000 --> 00:00:08.000\nсложную рабочую ситуацию и теперь ищем решение\n"
    )
    cues = parse_vtt(text)

    assert [cue["text"] for cue in cues] == [
        "мы обсуждаем сложную рабочую ситуацию",
        "и теперь ищем решение",
    ]


def test_youtube_space_only_display_row_keeps_question_beginning_and_timestamps():
    from worker.services.vtt import parse_vtt
    text=('WEBVTT\n\n'
          '00:04:53.520 --> 00:04:56.230\n \nчто<00:04:53.759><c> на</c><00:04:54.000><c> рынке</c>\n\n'
          '00:04:56.230 --> 00:04:56.240\nчто на рынке\n \n\n'
          '00:04:56.240 --> 00:04:59.469\nчто на рынке\nфронтенда<00:04:56.919><c> сейчас,</c>\n')
    cues=parse_vtt(text)
    assert ' '.join(c['text'] for c in cues)=='что на рынке фронтенда сейчас,'
    assert cues[0]['start']==293.52
    assert cues[-1]['end']==299.469
