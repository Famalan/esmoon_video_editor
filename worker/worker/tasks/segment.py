from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, Job, JobStatus, Segment, SegmentDecision
from worker.progress import publish_progress
from worker.config import settings
from worker.prompts import segment as prompt
from worker.services import ffmpeg, llm, storage
from shared.stages import Stage


class SegmentValidationError(ValueError):
    pass


_DURATION_OVERSHOOT = 5.0
_OVERLAP_TOLERANCE = 1.0
_CHAPTER_START_TOLERANCE = 90.0
_CHUNK_TARGET = 7200.0          # цель ~2ч на один LLM-вызов


_SCORE_NAMES = ("relevance", "pain", "hook", "value")


def decision_for_scores(segment: dict) -> str:
    return (
        SegmentDecision.PUBLISH.value
        if segment["pain"] >= 70 and segment["value"] >= 70
        else SegmentDecision.SKIP.value
    )


def validate_segments(segments: list[dict], video_duration: float) -> list[dict]:
    if not segments:
        return []

    for i, s in enumerate(segments):
        start = float(s["start"])
        end = float(s["end"])

        if start < 0 or end <= start:
            raise SegmentValidationError(
                f"segment {i} has invalid range {start:.0f}..{end:.0f}"
            )
        if end > video_duration + _DURATION_OVERSHOOT:
            raise SegmentValidationError(
                f"segment {i} end {end:.0f} exceeds video duration {video_duration:.0f}"
            )
        if i > 0 and start + _OVERLAP_TOLERANCE < segments[i - 1]["end"]:
            raise SegmentValidationError(
                f"segments {i - 1} and {i} overlap"
            )
        for score_name in _SCORE_NAMES:
            score = s.get(score_name)
            if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
                raise SegmentValidationError(
                    f"segment {i} {score_name} must be an integer from 0 to 100"
                )
        expected_decision = decision_for_scores(s)
        if s.get("decision") != expected_decision:
            raise SegmentValidationError(
                f"segment {i} decision {s.get('decision')!r} does not match "
                f"scores; expected {expected_decision!r}"
            )
    return segments


def validate_chapters(chapters: list[dict], video_duration: float) -> list[dict]:
    if not chapters:
        raise SegmentValidationError("empty chapters")

    previous_start = -1.0
    for i, chapter in enumerate(chapters):
        start = float(chapter["start"])
        title = chapter.get("title")
        if start < 0 or start > video_duration + _DURATION_OVERSHOOT:
            raise SegmentValidationError(
                f"chapter {i} starts outside video at {start:.0f}s"
            )
        if start <= previous_start:
            raise SegmentValidationError("chapters must be strictly chronological")
        if not isinstance(title, str) or not title.strip():
            raise SegmentValidationError(f"chapter {i} has empty title")
        previous_start = start

    if float(chapters[0]["start"]) > _CHAPTER_START_TOLERANCE:
        raise SegmentValidationError(
            "first chapter does not cover the beginning of the video"
        )
    return chapters


def _chunk_cues(cues: list[dict], duration: float) -> list[tuple[float, float, list[dict]]]:
    """Бьёт длинные видео на ~2ч окна. Возвращает [(chunk_start, chunk_end, chunk_cues), ...]."""
    if duration <= _CHUNK_TARGET * 1.25:
        return [(0.0, duration, cues)]
    n = max(2, round(duration / _CHUNK_TARGET))
    step = duration / n
    out: list[tuple[float, float, list[dict]]] = []
    for i in range(n):
        cs = i * step
        ce = duration if i == n - 1 else (i + 1) * step
        chunk = [c for c in cues if cs <= c["start"] < ce]
        out.append((cs, ce, chunk))
    return out


@app.task(name="worker.tasks.segment.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.SEGMENT, "running")
    with session_scope() as db:
        db.get(Job, job_id).current_stage = Stage.SEGMENT
        db.commit()

    try:
        with session_scope() as db:
            transcript_asset = db.execute(
                select(Asset).where(
                    Asset.job_id == job_id, Asset.kind == AssetKind.TRANSCRIPT
                )
            ).scalar_one()
            video_asset = db.execute(
                select(Asset).where(
                    Asset.job_id == job_id, Asset.kind == AssetKind.SOURCE_VIDEO
                )
            ).scalar_one()
            video_key = video_asset.s3_key
            transcript_key = transcript_asset.s3_key

        cues: list[dict] = json.loads(storage.download_bytes(transcript_key).decode("utf-8"))

        tmp = Path(tempfile.mkdtemp(prefix=f"seg_{job_id}_"))
        try:
            mp4 = tmp / "source.mp4"
            storage.download_file(video_key, mp4)
            duration = ffmpeg.probe_duration(mp4)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        try:
            raw = _llm_segment(cues, duration)
            chapters = validate_chapters(
                sorted(raw["chapters"], key=lambda c: float(c["start"])),
                duration,
            )
            segments = validate_segments(
                sorted(raw["segments"], key=lambda s: float(s["start"])),
                duration,
            )
        except SegmentValidationError as first_err:
            raw = _llm_segment(cues, duration, retry_hint=str(first_err))
            chapters = validate_chapters(
                sorted(raw["chapters"], key=lambda c: float(c["start"])),
                duration,
            )
            segments = validate_segments(
                sorted(raw["segments"], key=lambda s: float(s["start"])),
                duration,
            )

        with session_scope() as db:
            job = db.get(Job, job_id)
            job.chapters = [
                {"start_sec": float(c["start"]), "title": c["title"].strip()}
                for c in chapters
            ]
            for idx, s in enumerate(segments):
                db.add(Segment(
                    job_id=job_id,
                    index=idx,
                    start_sec=float(s["start"]),
                    end_sec=float(s["end"]),
                    title=s["title"],
                    summary=s["summary"],
                    relevance=s["relevance"],
                    pain=s["pain"],
                    hook=s["hook"],
                    value=s["value"],
                    decision=SegmentDecision(s["decision"]),
                    transcript_excerpt=_extract_excerpt(cues, s["start"], s["end"]),
                ))
            db.commit()
    except Exception as exc:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = f"segment: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.SEGMENT, "failed")
        raise

    publish_progress(job_id, Stage.SEGMENT, "done")
    return job_id


def _llm_segment(cues: list[dict], duration: float, retry_hint: str = "") -> dict:
    """Строит главы и кандидатов, разбивая длинные видео на чанки ~2ч."""
    chunks = _chunk_cues(cues, duration)
    raw: dict[str, list[dict]] = {"chapters": [], "segments": []}
    for idx, (cs, ce, chunk_cues) in enumerate(chunks):
        if not chunk_cues:
            continue
        chunk_text = _format_transcript(chunk_cues)
        if len(chunks) == 1:
            user = prompt.USER_TEMPLATE.format(
                duration_sec=duration, transcript_lines=chunk_text
            )
        else:
            user = (
                f"Это часть {idx+1}/{len(chunks)} большого видео общей длительностью "
                f"{duration:.0f} сек ({_fmt_ts(duration)}). Тебе дан КУСОК от "
                f"{cs:.0f} ({_fmt_ts(cs)}) до {ce:.0f} ({_fmt_ts(ce)}), длина "
                f"{ce - cs:.0f} сек. Составь chapters, которые описывают весь этот кусок. "
                f"Ищи segments-кандидаты ТОЛЬКО внутри этого куска. "
                f"Не заполняй кандидатами весь диапазон и не притягивай первого или последнего кандидата "
                f"к границе куска. Используй РЕАЛЬНЫЕ таймкоды cue из транскрипта.\n\n"
                f"{chunk_text}"
            )
        if retry_hint:
            user += f"\n\nПрошлый ответ не прошёл валидацию: {retry_hint}. Исправь."
        data = llm.call_json(
            system=prompt.SYSTEM, user=user,
            schema=prompt.JSON_SCHEMA, schema_name="video_segments",
            model=settings.codex_model,
        )
        raw["chapters"].extend(data["chapters"])
        raw["segments"].extend(data["segments"])
    return raw


def _format_transcript(cues: list[dict]) -> str:
    lines = []
    for c in cues:
        ts = _fmt_ts(c["start"])
        lines.append(f"[{ts}] {c['text']}")
    return "\n".join(lines)


def _fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _extract_excerpt(cues: list[dict], start: float, end: float, max_chars: int = 500) -> str:
    parts: list[str] = []
    total = 0
    for c in cues:
        if c["end"] < start or c["start"] > end:
            continue
        parts.append(c["text"])
        total += len(c["text"])
        if total >= max_chars:
            break
    return " ".join(parts)[:max_chars]
