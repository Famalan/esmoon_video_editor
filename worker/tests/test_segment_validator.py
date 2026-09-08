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


@pytest.mark.parametrize(("start", "end"), [(0.0, 89.999), (0.0, 1500.001)])
def test_validate_rejects_duration_outside_strict_bounds(start, end):
    from worker.tasks.segment import SegmentValidationError, validate_segments

    with pytest.raises(SegmentValidationError, match="invalid range"):
        validate_segments([_segment(start=start, end=end)], video_duration=3000.0)


@pytest.mark.parametrize(("start", "end"), [(0.0, 90.0), (10.0, 1510.0)])
def test_validate_accepts_inclusive_duration_bounds(start, end):
    from worker.tasks.segment import validate_segments

    segment = _segment(start=start, end=end)
    assert validate_segments([segment], video_duration=3000.0) == [segment]


def test_validate_rejects_overshoot():
    from worker.tasks.segment import SegmentValidationError, validate_segments

    with pytest.raises(SegmentValidationError, match="invalid range"):
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


def test_long_transcript_windows_overlap_by_25_minutes():
    from worker.tasks.segment import _chunk_cues

    cues = [{"start": second, "end": second + 1, "text": str(second)} for second in range(0, 11000, 100)]
    windows = _chunk_cues(cues, 11000.0)

    assert [(start, end) for start, end, _ in windows] == [(0.0, 7200.0), (5700.0, 11000.0)]
    assert any(cue["start"] == 6000 for cue in windows[0][2])
    assert any(cue["start"] == 6000 for cue in windows[1][2])


def test_overlap_resolution_never_stitches_intervals():
    from worker.tasks.segment import _resolve_publish_overlaps

    first = _segment(start=100.0, end=500.0)
    second = _segment(start=450.0, end=900.0)
    for item in (first, second):
        item["narrative"] = {"ok": True, "reason": "complete"}
    result = _resolve_publish_overlaps([first, second])

    published = [item for item in result if item["decision"] == "publish"]
    assert len(published) == 1
    assert (published[0]["start"], published[0]["end"]) in {(100.0, 500.0), (450.0, 900.0)}


def test_overlap_resolution_handles_one_interval_conflicting_with_two():
    from worker.tasks.segment import _resolve_publish_overlaps

    left = _segment(start=100.0, end=300.0, pain=70, value=70)
    right = _segment(start=400.0, end=600.0, pain=70, value=70)
    whole = _segment(start=200.0, end=550.0, pain=100, value=100)
    for item in (left, right, whole):
        item["narrative"] = {"ok": True, "reason": "complete"}

    result = _resolve_publish_overlaps([left, right, whole])

    assert [item for item in result if item["decision"] == "publish"] == [whole]


def test_invalid_model_bounds_are_rejected_without_clamping():
    from worker.tasks.segment import _normalize_candidates

    candidate = _segment(start=-12.0, end=120.0)
    candidate["rejection_reason"] = None
    result = _normalize_candidates([candidate], 1000.0)[0]

    assert (result["start"], result["end"]) == (-12.0, 120.0)
    assert result["decision"] == "skip"


def test_narrative_review_marks_boundaries_and_timestamped_context(monkeypatch):
    from worker.tasks import segment as module

    captured = {}
    candidate = _segment(start=100.0, end=200.0)
    candidate["rejection_reason"] = None
    cues = [
        {"start": 80.0, "end": 90.0, "text": "контекст до"},
        {"start": 100.0, "end": 200.0, "text": "полный ответ"},
        {"start": 205.0, "end": 215.0, "text": "контекст после"},
    ]

    def fake_call_json(**kwargs):
        captured.update(kwargs)
        return {"reviews": [{"candidate_id": 0, "ok": True, "reason": "завершён", "start": 100.0, "end": 200.0}]}

    monkeypatch.setattr(module.llm, "call_json", fake_call_json)
    result = module._narrative_review(cues, [candidate], "работа", "руководители", 1000.0)

    assert result[0]["narrative"]["ok"] is True
    assert "ВЫБРАННЫЙ ДИАПАЗОН: 100.000–200.000" in captured["user"]
    assert "[80.000–90.000" in captured["user"]


def test_window_prompt_interpolates_topic_and_audience(monkeypatch):
    from worker.tasks import segment as module

    captured = {}

    def fake_call_json(**kwargs):
        captured.update(kwargs)
        return {"chapters": [{"start": 0.0, "title": "Начало"}], "segments": []}

    monkeypatch.setattr(module.llm, "call_json", fake_call_json)
    module._llm_segment([{"start": 0.0, "end": 1.0, "text": "текст"}], 500.0, topic="карьера", audience="менеджеры")

    assert "Тема пользователя: карьера" in captured["user"]
    assert "Аудитория: менеджеры" in captured["user"]


def test_manual_review_cannot_approve_a_different_proposed_range(monkeypatch):
    from worker.tasks import segment
    candidate=_segment(start=100,end=200)
    monkeypatch.setattr(segment.llm,'call_json',lambda **kwargs:{'reviews':[{'candidate_id':0,'ok':True,'reason':'Only corrected range is complete','start':90,'end':200}]})
    result=segment._narrative_review([{'start':90,'end':200,'text':'Whole answer'}],[candidate],None,None,500,allow_adjustment=False)
    assert result[0]['narrative']['ok'] is False
    assert result[0]['start']==100
