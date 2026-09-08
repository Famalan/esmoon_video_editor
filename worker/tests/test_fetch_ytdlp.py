from __future__ import annotations

import subprocess
import sys
import json

import pytest


def test_ytdlp_uses_worker_python_environment(monkeypatch, tmp_path):
    from worker.tasks import fetch

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if "--skip-download" not in command:
            (tmp_path / "source.mp4").write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(fetch.subprocess, "run", fake_run)

    video, subtitles = fetch._ytdlp_download("https://example.com/video", tmp_path)

    assert video == tmp_path / "source.mp4"
    assert subtitles is None
    assert commands[0][:3] == [sys.executable, "-m", "yt_dlp"]
    assert commands[1][:3] == [sys.executable, "-m", "yt_dlp"]
    assert commands[0][commands[0].index("--retries") + 1] == "5"


def test_transcript_fetch_is_skip_download_only(monkeypatch, tmp_path):
    from worker.tasks import fetch

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if "--dump-single-json" in command:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"title": "Видео"}), stderr="")
        (tmp_path / "subs.ru.vtt").write_text("WEBVTT\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(fetch.subprocess, "run", fake_run)
    info, subtitle = fetch._ytdlp_subtitles("https://example.com/video", tmp_path)

    assert info["title"] == "Видео"
    assert subtitle.name == "subs.ru.vtt"
    assert commands and all("--skip-download" in command for command in commands)
    assert all("bestvideo" not in " ".join(command) and "bestaudio" not in " ".join(command) for command in commands)


def test_transcript_fetch_bot_error_requests_timestamped_transcript(monkeypatch, tmp_path):
    from worker.tasks import fetch

    monkeypatch.setattr(
        fetch.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="Sign in to confirm you're not a bot"),
    )
    with pytest.raises(RuntimeError, match="расшифровку с таймкодами"):
        fetch._ytdlp_subtitles("https://example.com/video", tmp_path)
