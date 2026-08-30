from __future__ import annotations

import pytest


def test_validate_accepts_well_formed():
    from worker.tasks.segment import validate_segments

    segments = [
        {"start": 0.0, "end": 720.0, "title": "часть 1", "summary": "..."},
        {"start": 720.0, "end": 1440.0, "title": "часть 2", "summary": "..."},
    ]
    out = validate_segments(segments, video_duration=1440.0)
    assert len(out) == 2
    assert out[0]["end"] == 720.0


def test_validate_rejects_overlap():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [
        {"start": 0.0, "end": 800.0, "title": "a", "summary": "."},
        {"start": 700.0, "end": 1400.0, "title": "b", "summary": "."},
    ]
    with pytest.raises(SegmentValidationError, match="overlap"):
        validate_segments(segments, video_duration=1400.0)


def test_validate_rejects_too_short():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [{"start": 0.0, "end": 30.0, "title": "a", "summary": "."}]
    with pytest.raises(SegmentValidationError, match="duration"):
        validate_segments(segments, video_duration=3600.0)


def test_validate_rejects_too_long():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [{"start": 0.0, "end": 2700.0, "title": "a", "summary": "."}]
    with pytest.raises(SegmentValidationError, match="duration"):
        validate_segments(segments, video_duration=3600.0)


def test_validate_rejects_overshoot():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [{"start": 0.0, "end": 700.0, "title": "a", "summary": "."}]
    with pytest.raises(SegmentValidationError, match="exceeds"):
        validate_segments(segments, video_duration=600.0)


def test_validate_allows_short_video_single_segment():
    from worker.tasks.segment import validate_segments

    segments = [{"start": 0.0, "end": 200.0, "title": "a", "summary": "."}]
    out = validate_segments(segments, video_duration=200.0)
    assert len(out) == 1


def test_validate_rejects_uncovered_tail():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [
        {"start": 0.0, "end": 800.0, "title": "a", "summary": "."},
        {"start": 800.0, "end": 1600.0, "title": "b", "summary": "."},
    ]
    with pytest.raises(SegmentValidationError, match="tail uncovered"):
        validate_segments(segments, video_duration=3600.0)


def test_auto_merge_swallows_short_intro():
    from worker.tasks.segment import auto_merge_short_edges

    segments = [
        {"start": 0.0, "end": 30.0, "title": "intro", "summary": "."},
        {"start": 30.0, "end": 900.0, "title": "тема 1", "summary": "."},
        {"start": 900.0, "end": 1800.0, "title": "тема 2", "summary": "."},
    ]
    out = auto_merge_short_edges(segments)
    assert len(out) == 2
    assert out[0]["start"] == 0.0
    assert out[0]["end"] == 900.0
    assert out[0]["title"] == "тема 1"


def test_auto_merge_swallows_short_outro():
    from worker.tasks.segment import auto_merge_short_edges

    segments = [
        {"start": 0.0, "end": 800.0, "title": "a", "summary": "."},
        {"start": 800.0, "end": 1600.0, "title": "b", "summary": "."},
        {"start": 1600.0, "end": 1620.0, "title": "outro", "summary": "."},
    ]
    out = auto_merge_short_edges(segments)
    assert len(out) == 2
    assert out[-1]["start"] == 800.0
    assert out[-1]["end"] == 1620.0
    assert out[-1]["title"] == "b"


def test_auto_merge_keeps_short_intro_when_merge_would_exceed_max():
    from worker.tasks.segment import auto_merge_short_edges

    segments = [
        {"start": 0.0, "end": 30.0, "title": "intro", "summary": "."},
        {"start": 30.0, "end": 930.0, "title": "long content", "summary": "."},
        {"start": 930.0, "end": 1700.0, "title": "next", "summary": "."},
    ]
    out = auto_merge_short_edges(segments)
    assert len(out) == 3
    assert out[0]["start"] == 0.0
    assert out[0]["title"] == "intro"


def test_auto_merge_swallows_short_middle():
    from worker.tasks.segment import auto_merge_short_edges

    segments = [
        {"start": 0.0, "end": 600.0, "title": "a", "summary": "."},
        {"start": 600.0, "end": 900.0, "title": "short middle", "summary": "."},
        {"start": 900.0, "end": 1500.0, "title": "c", "summary": "."},
    ]
    out = auto_merge_short_edges(segments)
    assert len(out) == 2
    assert 420 <= (out[0]["end"] - out[0]["start"]) <= 900
    assert 420 <= (out[1]["end"] - out[1]["start"]) <= 900


def test_auto_merge_leaves_well_formed_alone():
    from worker.tasks.segment import auto_merge_short_edges

    segments = [
        {"start": 0.0, "end": 900.0, "title": "a", "summary": "."},
        {"start": 900.0, "end": 1800.0, "title": "b", "summary": "."},
    ]
    out = auto_merge_short_edges(segments)
    assert len(out) == 2


def test_validate_rejects_short_edge_segments():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [
        {"start": 0.0, "end": 360.0, "title": "intro", "summary": "."},
        {"start": 360.0, "end": 1200.0, "title": "тема", "summary": "."},
        {"start": 1200.0, "end": 1500.0, "title": "финал", "summary": "."},
    ]
    with pytest.raises(SegmentValidationError, match="duration"):
        validate_segments(segments, video_duration=1500.0)


def test_validate_rejects_short_middle_segment():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [
        {"start": 0.0, "end": 720.0, "title": "a", "summary": "."},
        {"start": 720.0, "end": 1020.0, "title": "b", "summary": "."},
        {"start": 1020.0, "end": 1800.0, "title": "c", "summary": "."},
    ]
    with pytest.raises(SegmentValidationError, match="duration"):
        validate_segments(segments, video_duration=1800.0)


def test_validate_accepts_small_tail_gap():
    from worker.tasks.segment import validate_segments

    segments = [
        {"start": 0.0, "end": 800.0, "title": "a", "summary": "."},
        {"start": 800.0, "end": 1570.0, "title": "b", "summary": "."},
    ]
    out = validate_segments(segments, video_duration=1600.0)
    assert len(out) == 2
