from __future__ import annotations

import pytest


def _segment(**overrides):
    value = {
        "start": 0.0,
        "end": 720.0,
        "title": "Как решить конкретную проблему",
        "summary": "Законченная практическая мысль.",
        "relevance": 80,
        "pain": 75,
        "hook": 70,
        "value": 85,
        "decision": "publish",
    }
    value.update(overrides)
    return value


def test_validate_accepts_publish_and_skip_candidates_with_gaps():
    from worker.tasks.segment import validate_segments

    segments = [
        _segment(start=600.0, end=1320.0),
        _segment(
            start=1800.0,
            end=2520.0,
            relevance=45,
            pain=70,
            hook=55,
            value=60,
            decision="skip",
        ),
    ]

    out = validate_segments(segments, video_duration=3600.0)

    assert out == segments


def test_validate_accepts_empty_candidate_list():
    from worker.tasks.segment import validate_segments

    assert validate_segments([], video_duration=3600.0) == []


def test_validate_chapters_accepts_full_video_timeline():
    from worker.tasks.segment import validate_chapters

    chapters = [
        {"start": 3.0, "title": "Вступление"},
        {"start": 480.0, "title": "Первая тема"},
        {"start": 1500.0, "title": "Вторая тема"},
    ]

    assert validate_chapters(chapters, video_duration=2400.0) == chapters


def test_validate_chapters_rejects_missing_video_beginning():
    from worker.tasks.segment import SegmentValidationError, validate_chapters

    with pytest.raises(SegmentValidationError, match="beginning"):
        validate_chapters(
            [{"start": 600.0, "title": "Слишком поздно"}],
            video_duration=2400.0,
        )


def test_validate_rejects_overlap():
    from worker.tasks.segment import SegmentValidationError, validate_segments

    segments = [
        _segment(start=0.0, end=800.0),
        _segment(start=700.0, end=1400.0),
    ]

    with pytest.raises(SegmentValidationError, match="overlap"):
        validate_segments(segments, video_duration=1400.0)


@pytest.mark.parametrize(("start", "end"), [(-1.0, 720.0), (720.0, 720.0)])
def test_validate_rejects_invalid_range(start, end):
    from worker.tasks.segment import SegmentValidationError, validate_segments

    with pytest.raises(SegmentValidationError, match="invalid range"):
        validate_segments([_segment(start=start, end=end)], video_duration=3600.0)


def test_validate_accepts_natural_duration_without_minimum_or_maximum():
    from worker.tasks.segment import validate_segments

    segments = [
        _segment(start=0.0, end=45.0),
        _segment(start=60.0, end=2700.0),
    ]

    assert validate_segments(segments, video_duration=3000.0) == segments


def test_validate_rejects_overshoot():
    from worker.tasks.segment import SegmentValidationError, validate_segments

    with pytest.raises(SegmentValidationError, match="exceeds"):
        validate_segments(
            [_segment(start=0.0, end=700.0)],
            video_duration=600.0,
        )


def test_validate_allows_short_video_single_candidate():
    from worker.tasks.segment import validate_segments

    segments = [_segment(start=0.0, end=200.0)]

    assert validate_segments(segments, video_duration=200.0) == segments


@pytest.mark.parametrize("score_name", ["relevance", "pain", "hook", "value"])
@pytest.mark.parametrize("bad_score", [-1, 101, 70.5, True])
def test_validate_rejects_invalid_scores(score_name, bad_score):
    from worker.tasks.segment import SegmentValidationError, validate_segments

    with pytest.raises(SegmentValidationError, match=score_name):
        validate_segments(
            [_segment(**{score_name: bad_score})],
            video_duration=900.0,
        )


def test_validate_rejects_decision_that_does_not_match_scores():
    from worker.tasks.segment import SegmentValidationError, validate_segments

    with pytest.raises(SegmentValidationError, match="does not match scores"):
        validate_segments(
            [_segment(pain=60, decision="publish")],
            video_duration=900.0,
        )


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ({"relevance": 0, "pain": 70, "hook": 0, "value": 70}, "publish"),
        ({"relevance": 100, "pain": 69, "hook": 100, "value": 100}, "skip"),
        ({"relevance": 100, "pain": 100, "hook": 100, "value": 69}, "skip"),
    ],
)
def test_decision_for_scores_uses_publish_thresholds(scores, expected):
    from worker.tasks.segment import decision_for_scores

    assert decision_for_scores(scores) == expected
