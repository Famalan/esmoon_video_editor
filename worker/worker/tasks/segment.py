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
from worker.prompts import segment as prompt
from worker.services import ffmpeg, llm, storage
from shared.stages import Stage


class SegmentValidationError(ValueError):
    pass


_MIN_DURATION = 240.0   # 4 min (с запасом от 5 min target)
_MAX_DURATION = 1200.0  # 20 min (с запасом от 15 min target)
_DURATION_OVERSHOOT = 5.0
_OVERLAP_TOLERANCE = 1.0


def validate_segments(segments: list[dict], video_duration: float) -> list[dict]:
    if not segments:
        raise SegmentValidationError("empty segments")

    short_video = video_duration < _MIN_DURATION
    for i, s in enumerate(segments):
        start = float(s["start"])
        end = float(s["end"])
        dur = end - start

        if not short_video and not (_MIN_DURATION <= dur <= _MAX_DURATION):
            raise SegmentValidationError(
                f"segment {i} duration {dur:.0f}s out of [{_MIN_DURATION}, {_MAX_DURATION}]"
            )
        if end > video_duration + _DURATION_OVERSHOOT:
            raise SegmentValidationError(
                f"segment {i} end {end:.0f} exceeds video duration {video_duration:.0f}"
            )
        if i > 0 and start + _OVERLAP_TOLERANCE < segments[i - 1]["end"]:
            raise SegmentValidationError(
                f"segments {i - 1} and {i} overlap"
            )
    return segments


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

        transcript_text = _format_transcript(cues)
        user = prompt.USER_TEMPLATE.format(
            duration_sec=duration, transcript_lines=transcript_text
        )

        try:
            data = llm.call_json(
                system=prompt.SYSTEM, user=user,
                schema=prompt.JSON_SCHEMA, schema_name="video_segments",
            )
            segments = validate_segments(data["segments"], duration)
        except SegmentValidationError as first_err:
            retry_user = user + f"\n\nПрошлый ответ не прошёл валидацию: {first_err}. Исправь."
            data = llm.call_json(
                system=prompt.SYSTEM, user=retry_user,
                schema=prompt.JSON_SCHEMA, schema_name="video_segments",
            )
            segments = validate_segments(data["segments"], duration)

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
