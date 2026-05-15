from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from worker.config import settings


class Cue(TypedDict):
    start: float
    end: float
    text: str


@lru_cache(maxsize=1)
def _get_model():
    from faster_whisper import WhisperModel

    return WhisperModel(
        settings.whisper_model,
        device="cpu",
        compute_type="int8",
        download_root=settings.whisper_cache_dir,
    )


def transcribe_audio(path: Path, language: str = "ru") -> list[Cue]:
    model = _get_model()
    segments, _info = model.transcribe(
        str(path),
        language=language,
        beam_size=1,
        vad_filter=False,
    )
    return [
        Cue(start=float(s.start), end=float(s.end), text=s.text.strip())
        for s in segments
    ]
