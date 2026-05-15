from __future__ import annotations

import pytest


def test_validate_accepts_well_formed():
    from worker.tasks.segment import validate_segments

    segments = [
        {"start": 0.0, "end": 360.0, "title": "часть 1", "summary": "..."},
        {"start": 360.0, "end": 720.0, "title": "часть 2", "summary": "..."},
    ]
    out = validate_segments(segments, video_duration=720.0)
    assert len(out) == 2
    assert out[0]["end"] == 360.0


def test_validate_rejects_overlap():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [
        {"start": 0.0, "end": 400.0, "title": "a", "summary": "."},
        {"start": 300.0, "end": 700.0, "title": "b", "summary": "."},
    ]
    with pytest.raises(SegmentValidationError, match="overlap"):
        validate_segments(segments, video_duration=700.0)


def test_validate_rejects_too_short():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [{"start": 0.0, "end": 30.0, "title": "a", "summary": "."}]
    with pytest.raises(SegmentValidationError, match="duration"):
        validate_segments(segments, video_duration=3600.0)


def test_validate_rejects_too_long():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [{"start": 0.0, "end": 1500.0, "title": "a", "summary": "."}]
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
