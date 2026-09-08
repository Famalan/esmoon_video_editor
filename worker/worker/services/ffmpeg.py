from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: float | None = None) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        tail = (result.stderr or "")[-500:]
        raise FFmpegError(f"ffmpeg failed (rc={result.returncode}): {tail}")


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ]
    )
    return float(json.loads(out)["format"]["duration"])


def probe_media(path: Path) -> dict:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,format_name:stream=index,codec_type,codec_name,pix_fmt",
        "-of", "json", str(path),
    ])
    data = json.loads(out)
    data["duration"] = float(data.get("format", {}).get("duration", 0))
    return data


def is_browser_compatible(path: Path) -> bool:
    media = probe_media(path)
    streams = media.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    containers = set(media.get("format", {}).get("format_name", "").split(","))
    return bool(containers & {"mp4", "mov"} and video and video.get("codec_name") == "h264"
                and video.get("pix_fmt") in ("yuv420p", "yuvj420p")
                and (audio is None or audio.get("codec_name") == "aac"))


def _encoder(*, software: bool = False) -> list[str]:
    if not software and os.uname().sysname == "Darwin":
        probe = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
        if "h264_videotoolbox" in probe.stdout:
            return ["-c:v", "h264_videotoolbox", "-b:v", "5M"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]


def cut_segment(*, src: Path, dst: Path, start_sec: float, end_sec: float) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.0, end_sec - start_sec)
    base = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_sec:.3f}",
        "-i", str(src),
        "-t", f"{duration:.3f}",
    ]
    tail = [
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-pix_fmt", "yuv420p",
        "-map_metadata", "-1",
        "-movflags", "+faststart",
        str(dst),
    ]
    try:
        _run([*base, *_encoder(), *tail])
    except FFmpegError:
        _run([*base, *_encoder(software=True), *tail])


def make_preview(*, src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    base = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-xerror", "-err_detect", "explode", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
    ]
    tail = [
        "-vf", "scale=min(1280\\,iw):-2", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(dst),
    ]
    try:
        _run([*base, *_encoder(), *tail])
    except FFmpegError:
        _run([*base, *_encoder(software=True), *tail])


def validate_decode(path: Path) -> None:
    _run(["ffmpeg", "-v", "error", "-xerror", "-err_detect", "explode", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"], timeout=3600)


def extract_thumbnail(*, src: Path, dst: Path, at_sec: float) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{at_sec:.3f}",
        "-i", str(src),
        "-frames:v", "1",
        "-q:v", "2",
        str(dst),
    ])


def extract_audio(*, src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        str(dst),
    ])
