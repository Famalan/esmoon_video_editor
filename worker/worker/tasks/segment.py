from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from shared.policy import MAX_DURATION, MIN_DURATION, PolicyError, analysis_duration, validate_interval
from shared.stages import Stage
from worker.celery_app import app
from worker.config import settings
from worker.db import session_scope
from worker.models import JobStatus, Segment, SegmentDecision, SegmentRevision, Source
from worker.progress import publish_progress
from worker.prompts import segment as prompt
from worker.runtime import require_job_attempt
from worker.services import llm, storage


class SegmentValidationError(ValueError):
    pass


_CHUNK_TARGET = 7200.0
_WINDOW_OVERLAP = 1500.0
_SCORE_NAMES = ("relevance", "pain", "hook", "value")


def decision_for_scores(segment: dict) -> str:
    return SegmentDecision.PUBLISH.value if segment["pain"] >= 70 and segment["value"] >= 70 else SegmentDecision.SKIP.value


def validate_segments(segments: list[dict], video_duration: float) -> list[dict]:
    previous_end: float | None = None
    for i, item in enumerate(segments):
        try:
            validate_interval(item["start"], item["end"], video_duration)
        except (PolicyError, KeyError) as exc:
            raise SegmentValidationError(f"segment {i} has invalid range: {exc}") from exc
        start, end = float(item["start"]), float(item["end"])
        if previous_end is not None and start < previous_end:
            raise SegmentValidationError(f"segments {i - 1} and {i} overlap")
        previous_end = end
        for score_name in _SCORE_NAMES:
            score = item.get(score_name)
            if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
                raise SegmentValidationError(f"segment {i} {score_name} must be an integer from 0 to 100")
        expected = decision_for_scores(item)
        if item.get("decision") != expected:
            raise SegmentValidationError(f"segment {i} decision {item.get('decision')!r} does not match scores; expected {expected!r}")
    return segments


def validate_chapters(chapters: list[dict], video_duration: float) -> list[dict]:
    if not chapters:
        raise SegmentValidationError("empty chapters")
    previous = -1.0
    for i, chapter in enumerate(chapters):
        start = float(chapter["start"])
        if start < 0 or start > video_duration or start <= previous:
            raise SegmentValidationError(f"chapter {i} is outside or not chronological")
        if not str(chapter.get("title", "")).strip():
            raise SegmentValidationError(f"chapter {i} has empty title")
        previous = start
    if float(chapters[0]["start"]) > 90:
        raise SegmentValidationError("first chapter does not cover the beginning of the video")
    return chapters


def _chunk_cues(cues: list[dict], duration: float) -> list[tuple[float, float, list[dict]]]:
    if duration <= _CHUNK_TARGET:
        return [(0.0, duration, cues)]
    windows: list[tuple[float, float, list[dict]]] = []
    start = 0.0
    while start < duration:
        end = min(duration, start + _CHUNK_TARGET)
        windows.append((start, end, [cue for cue in cues if cue["end"] >= start and cue["start"] <= end]))
        if end == duration:
            break
        start = end - _WINDOW_OVERLAP
    return windows


def _normalize_candidates(candidates: list[dict], duration: float) -> list[dict]:
    normalized: list[dict] = []
    for item in sorted(candidates, key=lambda value: (float(value["start"]), float(value["end"]))):
        start, end = float(item["start"]), float(item["end"])
        item = {**item, "start": start, "end": end, "rejection_reason": item.get("rejection_reason")}
        length = end - start
        if start < 0 or end <= start or end > duration:
            item["decision"] = "skip"
            item["rejection_reason"] = item["rejection_reason"] or "Модель вернула границы за пределами исходника; автоматически исправлять их нельзя."
            normalized.append(item)
            continue
        elif length < MIN_DURATION or length > MAX_DURATION:
            item["decision"] = "skip"
            item["rejection_reason"] = item["rejection_reason"] or "Цельная тема выходит за допустимую длительность 90–1500 секунд."
        elif item["decision"] == "skip":
            item["rejection_reason"] = item["rejection_reason"] or "Недостаточно ясной боли или законченного решения."
        # Overlap-window duplicates: retain the more complete interval.
        duplicate = next((old for old in normalized if old["decision"] == "publish" and min(old["end"], end) - max(old["start"], start) > 0.7 * min(old["end"] - old["start"], length)), None)
        if duplicate:
            if length > duplicate["end"] - duplicate["start"]:
                normalized[normalized.index(duplicate)] = item
            continue
        normalized.append(item)
    return sorted(normalized, key=lambda value: value["start"])


_REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["reviews"],
    "properties": {"reviews": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["candidate_id", "ok", "reason", "start", "end"], "properties": {
            "candidate_id": {"type": "integer"}, "ok": {"type": "boolean"}, "reason": {"type": "string", "maxLength": 500},
            "start": {"type": "number"}, "end": {"type": "number"}
        }}}},
}


def _narrative_review(cues: list[dict], candidates: list[dict], topic: str | None, audience: str | None, duration: float, *, allow_adjustment: bool = True) -> list[dict]:
    if not candidates:
        return candidates
    reviewable: list[tuple[int, dict]] = []
    for index, item in enumerate(candidates):
        if item["decision"] == "publish":
            reviewable.append((index, item))
        else:
            item["narrative"] = {"ok": False, "reason": item.get("rejection_reason") or "Кандидат отклонён при разметке."}
    reviews: dict[int, dict] = {}
    for offset in range(0, len(reviewable), 6):
        excerpts = []
        for index, item in reviewable[offset:offset + 6]:
            context_cues = [cue for cue in cues if cue["end"] >= max(0, item["start"] - 45) and cue["start"] <= item["end"] + 45]
            context = _format_transcript(context_cues)
            excerpts.append(f"Кандидат {index}. ВЫБРАННЫЙ ДИАПАЗОН: {item['start']:.3f}–{item['end']:.3f}. Строки до и после — только контекст:\n{context}")
        data = llm.call_json(
            system=("Ты финальный редактор. Для каждого непрерывного кандидата проверь, что начало даёт нужный контекст, "
                    "конец содержит вывод, внутри одна тема и ответ не оборван на границе окна. Нельзя предлагать склейки. "
                    "ok=false для обрыва, смешения тем или неполного ответа. "
                    + ("Верни start/end на точных границах cue; можно сдвинуть каждую границу максимум на 45 секунд, чтобы сохранить вступление или вывод."
                       if allow_adjustment else
                       "Проверь ровно указанные границы пользователя и верни те же start/end без изменения. Короткая пауза по краям допустима. Если ответ в этих границах неполон, верни ok=false.")),
            user=f"Тема: {topic or 'не задана'}\nАудитория: {audience or 'не задана'}\n\n" + "\n\n".join(excerpts),
            schema=_REVIEW_SCHEMA, schema_name="narrative_reviews", model=settings.codex_model,
        )
        reviews.update({int(row["candidate_id"]): row for row in data["reviews"]})
    for index, item in enumerate(candidates):
        if index not in reviews:
            if item["decision"] == "publish":
                item["decision"] = "skip"
                item["rejection_reason"] = "Модель не вернула проверку кандидата."
                item["narrative"] = {"ok": False, "reason": item["rejection_reason"]}
            continue
        review = reviews.get(index, {"ok": False, "reason": "Модель не вернула проверку кандидата."})
        if review["ok"]:
            proposed_start, proposed_end = float(review["start"]), float(review["end"])
            cue_starts = {round(float(cue["start"]), 3) for cue in cues}
            cue_ends = {round(float(cue["end"]), 3) for cue in cues}
            try:
                validate_interval(proposed_start, proposed_end, duration)
                exact_cues = round(proposed_start, 3) in cue_starts and round(proposed_end, 3) in cue_ends
                close = abs(proposed_start - item["start"]) <= 45 and abs(proposed_end - item["end"]) <= 45
                unchanged = proposed_start == item["start"] and proposed_end == item["end"]
                if allow_adjustment and exact_cues and close:
                    item["start"], item["end"] = proposed_start, proposed_end
                elif not unchanged:
                    review = {**review,"ok":False,"reason":"Модель подтвердила другой диапазон; текущие границы не прошли проверку."}
            except PolicyError as exc:
                review = {**review,"ok":False,"reason":str(exc)}
        item["narrative"] = {"ok": bool(review["ok"]), "reason": str(review["reason"]),
                             "start_sec": item["start"], "end_sec": item["end"], "prompt_version":"narrative-v2"}
        if not review["ok"]:
            item["decision"] = "skip"
            item["rejection_reason"] = str(review["reason"])
    return _resolve_publish_overlaps(candidates)


def _resolve_publish_overlaps(candidates: list[dict]) -> list[dict]:
    accepted: list[dict] = []
    for item in candidates:
        if item["decision"] != "publish":
            continue
        conflicts = [other for other in accepted if item["start"] < other["end"] and other["start"] < item["end"]]
        if not conflicts:
            accepted.append(item)
            continue
        score = item["pain"] + item["value"] + min(100, int((item["end"] - item["start"]) / 15))
        conflict_scores = [other["pain"] + other["value"] + min(100, int((other["end"] - other["start"]) / 15)) for other in conflicts]
        if score > max(conflict_scores):
            for rejected in conflicts:
                rejected["decision"] = "skip"
                rejected["rejection_reason"] = "Кандидат пересекается с более полным эпизодом этой разметки."
                rejected["narrative"] = {"ok": False, "reason": rejected["rejection_reason"]}
                accepted.remove(rejected)
            accepted.append(item)
        else:
            item["decision"] = "skip"
            item["rejection_reason"] = "Кандидат пересекается с более полным эпизодом этой разметки."
            item["narrative"] = {"ok": False, "reason": item["rejection_reason"]}
    return candidates


@app.task(name="worker.tasks.segment.run")
def run(job_id: str, attempt_id: str | None = None) -> str:
    publish_progress(job_id, Stage.SEGMENT, "running", attempt_id=attempt_id, detail="Ищем цельные эпизоды")
    with session_scope() as db:
        job = require_job_attempt(db, job_id, attempt_id)
        attempt_id = str(job.attempt_id) if job.attempt_id else attempt_id
        if (job.progress or {}).get("analysis_complete") and db.execute(select(Segment.id).where(Segment.job_id == job.id)).first():
            return job_id
        cues = json.loads(storage.download_bytes(job.transcript_snapshot["key"]).decode("utf-8"))
        source = db.get(Source, job.source_id)
        duration, topic, audience = analysis_duration(job, source), job.topic, job.audience
    try:
        raw = _llm_segment(cues, duration, topic=topic, audience=audience)
        chapters = _merge_chapters(raw["chapters"], duration)
        candidates = _narrative_review(cues, _normalize_candidates(raw["segments"], duration), topic, audience, duration)
        persist_analysis(job_id, attempt_id, chapters, candidates)
    except Exception as exc:
        with session_scope() as db:
            job = require_job_attempt(db, job_id, attempt_id)
            job.status, job.error = JobStatus.FAILED, f"segment: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.SEGMENT, "failed", attempt_id=attempt_id, detail=str(exc)[:300])
        raise
    publish_progress(job_id, Stage.SEGMENT, "done", attempt_id=attempt_id, detail=f"Найдено эпизодов: {len(candidates)}")
    return job_id


def persist_analysis(job_id: str, attempt_id: str | None, chapters: list[dict], candidates: list[dict]) -> str:
    """Persist a trusted, completed Astra result under the current attempt fence.

    This is intentionally an internal Python interface. It validates the same
    single-range policy as a live model run, but never invokes the model itself.
    """
    with session_scope() as db:
        job = require_job_attempt(db, job_id, attempt_id)
        if (job.progress or {}).get("analysis_complete") and db.execute(
            select(Segment.id).where(Segment.job_id == job.id)
        ).first():
            return job_id
        source = db.get(Source, job.source_id)
        duration = analysis_duration(job, source)
        cues = json.loads(storage.download_bytes(job.transcript_snapshot["key"]).decode("utf-8"))

        # A trusted result already passed window deduplication and narrative
        # boundary review. Validate it without rewriting those reviewed choices.
        chapters = validate_chapters(chapters, duration)
        for index, item in enumerate(candidates):
            try:
                start, end = float(item["start"]), float(item["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SegmentValidationError(f"segment {index} has invalid range") from exc
            if start < 0 or end <= start or end > duration:
                raise SegmentValidationError(f"segment {index} is outside the transcript timeline")
            for score_name in _SCORE_NAMES:
                score = item.get(score_name)
                if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
                    raise SegmentValidationError(f"segment {index} {score_name} must be an integer from 0 to 100")
            expected = decision_for_scores(item)
            if item.get("decision") == "publish" and expected != "publish":
                raise SegmentValidationError(f"segment {index} publish decision does not match scores")
            narrative = item.get("narrative") or {}
            if item.get("decision") == "publish" and narrative.get("ok") is not True:
                raise SegmentValidationError(f"segment {index} has no approved narrative review")
        publish_candidates = [candidate for candidate in candidates if candidate["decision"] == "publish"]
        validate_segments(publish_candidates, duration)

        deferred = not bool(source and source.original_key)
        stages = ({"render": "not_requested", "verify": "not_requested", "thumbnail": "not_requested", "metadata": "not_requested"}
                  if deferred else
                  {"render": "pending", "verify": "pending", "thumbnail": "pending", "metadata": "pending"})
        job.chapters = [{"start_sec": c["start"], "title": c["title"].strip()} for c in chapters]
        for index, item in enumerate(candidates):
            approved = item["decision"] == "publish" and item.get("narrative", {}).get("ok") is True
            segment_obj = Segment(
                job_id=job.id, index=index, start_sec=item["start"], end_sec=item["end"],
                title=item["title"], summary=item["summary"], relevance=item["relevance"], pain=item["pain"],
                hook=item["hook"], value=item["value"], decision=SegmentDecision(item["decision"]),
                rejection_reason=item.get("rejection_reason"),
                transcript_excerpt=_extract_text(cues, item["start"], item["end"], 1200),
            )
            db.add(segment_obj)
            db.flush()
            revision = SegmentRevision(
                segment_id=segment_obj.id, number=1, start_sec=item["start"], end_sec=item["end"],
                attempt_id=uuid.uuid4(), status="analyzed" if deferred and approved else ("rejected" if deferred else "queued"),
                stages=dict(stages), validation={"narrative": item.get("narrative") or {"ok": False, "reason": item.get("rejection_reason") or "Кандидат отклонён."}},
                transcript_text=_extract_text(cues, item["start"], item["end"]),
            )
            db.add(revision)
            db.flush()
            segment_obj.current_revision_id = revision.id
        job.progress = {
            **(job.progress or {}), "analysis_complete": True, "media_deferred": deferred,
            "stage": Stage.SEGMENT.value, "status": "done",
        }
        db.commit()
    return job_id


def _llm_segment(cues: list[dict], duration: float, retry_hint: str = "", *, topic: str | None = None, audience: str | None = None) -> dict:
    chunks = _chunk_cues(cues, duration)
    raw: dict[str, list[dict]] = {"chapters": [], "segments": []}
    for index, (start, end, chunk) in enumerate(chunks):
        user = (f"Окно {index + 1}/{len(chunks)} исходника {start:.3f}–{end:.3f} сек. Окна перекрываются на 25 минут; "
                f"не обрывай тему по краю окна. Тема пользователя: {topic or 'не задана'}. Аудитория: {audience or 'не задана'}.\n\n"
                + _format_transcript(chunk))
        if retry_hint:
            user += f"\n\nИсправь ошибку прошлого ответа: {retry_hint}"
        data = llm.call_json(system=prompt.SYSTEM, user=user, schema=prompt.JSON_SCHEMA, schema_name="video_segments", model=settings.codex_model)
        raw["chapters"].extend(data["chapters"])
        raw["segments"].extend(data["segments"])
    return raw


def _merge_chapters(chapters: list[dict], duration: float) -> list[dict]:
    result: list[dict] = []
    for chapter in sorted(chapters, key=lambda row: float(row["start"])):
        start = float(chapter["start"])
        if 0 <= start <= duration and (not result or start - result[-1]["start"] >= 20):
            result.append({"start": start, "title": str(chapter["title"])})
    return validate_chapters(result, duration)


def _format_transcript(cues: list[dict]) -> str:
    return "\n".join(f"[{float(cue['start']):.3f}–{float(cue['end']):.3f} | {_fmt_ts(cue['start'])}] {cue['text']}" for cue in cues)


def _fmt_ts(sec: float) -> str:
    minutes, seconds = divmod(int(sec), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _extract_text(cues: list[dict], start: float, end: float, max_chars: int | None = None) -> str:
    text = " ".join(str(cue["text"]) for cue in cues if cue["end"] >= start and cue["start"] <= end)
    return text[:max_chars] if max_chars else text


def _extract_excerpt(cues: list[dict], start: float, end: float, max_chars: int = 500) -> str:
    return _extract_text(cues, start, end, max_chars)


def ensure_revision_narrative(segment_id: str, revision_id: str, attempt_id: str) -> bool:
    from worker.models import Job, SegmentRevision, Source
    from worker.runtime import require_revision_attempt

    with session_scope() as db:
        segment, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
        job = db.get(Job, segment.job_id)
        source = db.get(Source, job.source_id)
        cues = json.loads(storage.download_bytes(job.transcript_snapshot["key"]).decode("utf-8"))
        if revision.validation.get("narrative", {}).get("ok") and revision.transcript_text:
            return True
        duration = analysis_duration(job, source)
        validate_interval(revision.start_sec, revision.end_sec, duration)
        candidate = {
            "start": revision.start_sec, "end": revision.end_sec, "title": segment.title or "",
            "summary": segment.summary or "", "relevance": segment.relevance, "pain": segment.pain,
            "hook": segment.hook, "value": segment.value, "decision": "publish", "rejection_reason": None,
        }
        topic, audience = job.topic, job.audience
    reviewed = _narrative_review(cues, [candidate], topic, audience, duration, allow_adjustment=False)[0]
    with session_scope() as db:
        _, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
        narrative = reviewed["narrative"]
        revision.validation = {**revision.validation, "narrative": narrative}
        revision.transcript_text = _extract_text(cues, revision.start_sec, revision.end_sec)
        if not narrative["ok"]:
            revision.status = "failed"
            revision.error = f"narrative: {narrative['reason']}"[:1000]
        db.commit()
    return bool(narrative["ok"])
