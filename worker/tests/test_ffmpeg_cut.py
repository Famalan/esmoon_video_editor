from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _make_test_video(path: Path, duration: int = 30) -> None:
    """Create a synthetic mp4 of given duration with testsrc + sine audio.

    Sparse keyframes make this a regression for stream-copy boundary drift.
    """
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=640x360:rate=25",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-g", "250", "-pix_fmt", "yuv420p", "-c:a", "aac",
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


def _make_boundary_video(path: Path, duration: float) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s=32x32:r=1:d={duration}",
            "-f", "lavfi", "-i", f"anullsrc=r=8000:cl=mono:d={duration}",
            "-c:v", "libx264", "-g", "300", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )


def test_cut_produces_segment_of_expected_duration(tmp_path):
    from worker.services.ffmpeg import cut_segment, probe_duration

    src = tmp_path / "src.mp4"
    _make_test_video(src, duration=30)

    out = tmp_path / "seg.mp4"
    cut_segment(src=src, dst=out, start_sec=5.0, end_sec=15.0)

    assert out.exists()
    d = probe_duration(out)
    assert d == pytest.approx(10.0, abs=0.08)


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


@pytest.mark.parametrize("duration", [90.0, 1500.0])
def test_exact_reencode_acceptance_boundaries_have_actual_duration(tmp_path, duration):
    from shared.policy import validate_actual_duration
    from worker.services.ffmpeg import cut_segment, probe_duration, validate_decode

    src = tmp_path / "boundary-src.mp4"
    out = tmp_path / "boundary-clip.mp4"
    _make_boundary_video(src, duration + 2)

    cut_segment(src=src, dst=out, start_sec=1.0, end_sec=1.0 + duration)
    actual = probe_duration(out)
    validate_actual_duration(actual)
    validate_decode(out)

    assert actual == pytest.approx(duration, abs=0.08)


def test_previous_89_421_second_output_is_rejected(tmp_path):
    from shared.policy import PolicyError, validate_actual_duration
    from worker.services.ffmpeg import probe_duration

    clip = tmp_path / "old-regression.mp4"
    _make_boundary_video(clip, 89.421)
    actual = probe_duration(clip)

    with pytest.raises(PolicyError):
        validate_actual_duration(actual)


def test_h264_mkv_requires_mp4_preview(tmp_path):
    from worker.services.ffmpeg import is_browser_compatible, make_preview
    original=tmp_path/'source.mp4';mkv=tmp_path/'source.mkv';preview=tmp_path/'preview.mp4'
    _make_test_video(original,duration=2)
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(original),'-c','copy',str(mkv)],check=True)
    assert not is_browser_compatible(mkv)
    make_preview(src=mkv,dst=preview)
    assert is_browser_compatible(preview)
