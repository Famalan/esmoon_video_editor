from __future__ import annotations

import subprocess
import sys


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
