from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _make_test_video(path: Path, duration: int = 30) -> None:
    """Create a synthetic mp4 of given duration with testsrc + sine audio.

    -g 25 → keyframe every second; otherwise libx264 defaults to keyint=250
    which makes stream-copy cuts snap inaccurately in short test clips.
    """
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=640x360:rate=25",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )


def _probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)]
    )
    return float(json.loads(out)["format"]["duration"])


def test_cut_produces_segment_of_expected_duration(tmp_path):
    from worker.services.ffmpeg import cut_segment, probe_duration

    src = tmp_path / "src.mp4"
    _make_test_video(src, duration=30)

    out = tmp_path / "seg.mp4"
    cut_segment(src=src, dst=out, start_sec=5.0, end_sec=15.0)

    assert out.exists()
    d = probe_duration(out)
    assert 9.0 <= d <= 11.0  # stream copy may snap to keyframes


def test_extract_thumbnail_writes_jpg(tmp_path):
    from worker.services.ffmpeg import extract_thumbnail

    src = tmp_path / "src.mp4"
    _make_test_video(src, duration=10)

    out = tmp_path / "thumb.jpg"
    extract_thumbnail(src=src, dst=out, at_sec=5.0)

    assert out.exists() and out.stat().st_size > 1000


def test_extract_audio_writes_mp3(tmp_path):
    from worker.services.ffmpeg import extract_audio

    src = tmp_path / "src.mp4"
    _make_test_video(src, duration=5)

    out = tmp_path / "audio.mp3"
    extract_audio(src=src, dst=out)

    assert out.exists() and out.stat().st_size > 500


def test_probe_duration_reads_video_length(tmp_path):
    from worker.services.ffmpeg import probe_duration

    src = tmp_path / "src.mp4"
    _make_test_video(src, duration=7)

    d = probe_duration(src)
    assert 6.5 <= d <= 7.5
