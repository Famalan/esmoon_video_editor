from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, Job, JobStatus, Segment
from worker.progress import publish_progress
from worker.config import settings
from worker.prompts import segment as prompt
from worker.services import ffmpeg, llm, storage
from shared.stages import Stage


class SegmentValidationError(ValueError):
    pass


_MIN_DURATION = 420.0           # 7 мин минимум для каждого сегмента
_MIN_DURATION_EDGE = 420.0      # первый и последний подчиняются тому же правилу
_MAX_DURATION = 900.0           # 15 мин максимум для каждого сегмента
_DURATION_OVERSHOOT = 5.0
_OVERLAP_TOLERANCE = 1.0
_TAIL_GAP_TOLERANCE = 60.0      # хвост должен быть покрыт до duration−60s
_CHUNK_TARGET = 7200.0          # цель ~2ч на один LLM-вызов
_TAIL_EXTEND_MAX = 900.0        # на сколько максимум растягиваем последний сегмент


def _dur(s: dict) -> float:
    return float(s["end"]) - float(s["start"])


def _merge(a: dict, b: dict) -> dict:
    longer = a if _dur(a) >= _dur(b) else b
    return {
        "start": a["start"],
        "end": b["end"],
        "title": longer["title"],
        "summary": longer["summary"],
    }


def auto_merge_short_edges(segments: list[dict]) -> list[dict]:
    """Сливает слишком короткие сегменты с соседом.

    LLM любит выделять короткий intro/jingle/outro и иногда middle, который
    короче минимума. Сливаем такие сегменты программно перед валидацией.

    - Первый/последний (edge) сливаем, если он короче 7 минут.
    - Средний сливаем, если он короче 7 минут; выбираем соседа, после слияния
      с которым итоговая длина ближе к 660с (цель 11 минут) и не больше 15 минут.
    """
    if len(segments) < 2:
        return segments
    out = [dict(s) for s in segments]

    while len(out) >= 2 and _dur(out[0]) < _MIN_DURATION_EDGE:
        if _dur(out[0]) + _dur(out[1]) <= _MAX_DURATION:
            out[0] = _merge(out[0], out[1])
            out.pop(1)
        else:
            break
    while len(out) >= 2 and _dur(out[-1]) < _MIN_DURATION_EDGE:
        if _dur(out[-2]) + _dur(out[-1]) <= _MAX_DURATION:
            out[-2] = _merge(out[-2], out[-1])
            out.pop()
        else:
            break

    changed = True
    while changed and len(out) >= 3:
        changed = False
        for i in range(1, len(out) - 1):
            if _dur(out[i]) >= _MIN_DURATION:
                continue
            left_after = _dur(out[i - 1]) + _dur(out[i])
            right_after = _dur(out[i]) + _dur(out[i + 1])
            target = 660.0
            candidates = []
            if left_after <= _MAX_DURATION:
                candidates.append((abs(left_after - target), "left"))
            if right_after <= _MAX_DURATION:
                candidates.append((abs(right_after - target), "right"))
            if not candidates:
                continue
            merge_side = min(candidates)[1]
            if merge_side == "left":
                out[i - 1] = _merge(out[i - 1], out[i])
                out.pop(i)
            else:
                out[i] = _merge(out[i], out[i + 1])
                out.pop(i + 1)
            changed = True
            break
    return out


def validate_segments(segments: list[dict], video_duration: float) -> list[dict]:
    if not segments:
        raise SegmentValidationError("empty segments")

    short_video = video_duration < _MIN_DURATION
    n = len(segments)
    for i, s in enumerate(segments):
        start = float(s["start"])
        end = float(s["end"])
        dur = end - start
        is_edge = i == 0 or i == n - 1
        min_dur = _MIN_DURATION_EDGE if is_edge else _MIN_DURATION

        if not short_video and not (min_dur <= dur <= _MAX_DURATION):
            raise SegmentValidationError(
                f"segment {i} duration {dur:.0f}s out of [{min_dur}, {_MAX_DURATION}]"
            )
        if end > video_duration + _DURATION_OVERSHOOT:
            raise SegmentValidationError(
                f"segment {i} end {end:.0f} exceeds video duration {video_duration:.0f}"
            )
        if i > 0 and start + _OVERLAP_TOLERANCE < segments[i - 1]["end"]:
            raise SegmentValidationError(
                f"segments {i - 1} and {i} overlap"
            )

    if not short_video:
        last_end = float(segments[-1]["end"])
        if video_duration - last_end > _TAIL_GAP_TOLERANCE:
            raise SegmentValidationError(
                f"last segment ends at {last_end:.0f}, leaves "
                f"{video_duration - last_end:.0f}s tail uncovered "
                f"(video duration {video_duration:.0f}s)"
            )
    return segments


def _pad_tail(segments: list[dict], duration: float) -> list[dict]:
    """Если хвост недостающий, но в пределах _TAIL_EXTEND_MAX — растянем последний сегмент."""
    if not segments:
        return segments
    gap = duration - float(segments[-1]["end"])
    if _TAIL_GAP_TOLERANCE < gap <= _TAIL_EXTEND_MAX:
        out = [dict(s) for s in segments]
        out[-1]["end"] = duration
        return out
    return segments


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
            segments = validate_segments(
                _pad_tail(auto_merge_short_edges(raw), duration), duration
            )
        except SegmentValidationError as first_err:
            raw = _llm_segment(cues, duration, retry_hint=str(first_err))
            segments = validate_segments(
                _pad_tail(auto_merge_short_edges(raw), duration), duration
            )

        with session_scope() as db:
            for idx, s in enumerate(segments):
                db.add(Segment(
                    job_id=job_id,
                    index=idx,
                    start_sec=float(s["start"]),
                    end_sec=float(s["end"]),
                    title=s["title"],
                    summary=s["summary"],
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


def _llm_segment(cues: list[dict], duration: float, retry_hint: str = "") -> list[dict]:
    """Прогоняет LLM по транскрипту, разбивая длинные видео на чанки ~2ч."""
    chunks = _chunk_cues(cues, duration)
    raw: list[dict] = []
    for idx, (cs, ce, chunk_cues) in enumerate(chunks):
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
                f"{ce - cs:.0f} сек. Сегментируй ТОЛЬКО этот кусок: первый сегмент "
                f"должен начинаться около {cs:.0f}, последний — заканчиваться около {ce:.0f}. "
                f"Используй РЕАЛЬНЫЕ таймкоды cue из транскрипта.\n\n{chunk_text}"
            )
        if retry_hint:
            user += f"\n\nПрошлый ответ не прошёл валидацию: {retry_hint}. Исправь."
        data = llm.call_json(
            system=prompt.SYSTEM, user=user,
            schema=prompt.JSON_SCHEMA, schema_name="video_segments",
            model=settings.codex_model,
        )
        raw.extend(data["segments"])
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
