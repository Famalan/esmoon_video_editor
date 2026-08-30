from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def test_call_json_runs_codex_with_schema_and_returns_parsed(monkeypatch):
    from worker.services import llm

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text(
            json.dumps(
                {"segments": [{"start": 0, "end": 600, "title": "t", "summary": "s"}]}
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    monkeypatch.setattr(llm.settings, "codex_cli_path", "/local/codex")
    monkeypatch.setattr(llm.settings, "codex_model", "gpt-5.6-luna")
    monkeypatch.setattr(llm.settings, "codex_reasoning_effort", "max")

    schema = {
        "type": "object",
        "properties": {"segments": {"type": "array"}},
        "required": ["segments"],
    }
    result = llm.call_json(
        system="sys",
        user="usr",
        schema=schema,
        schema_name="segment_schema",
    )

    assert result == {
        "segments": [{"start": 0, "end": 600, "title": "t", "summary": "s"}]
    }
    assert captured["command"][0:2] == ["/local/codex", "exec"]
    assert "--ephemeral" in captured["command"]
    assert "--ignore-user-config" in captured["command"]
    assert "--ignore-rules" in captured["command"]
    assert "--output-schema" in captured["command"]
    assert "gpt-5.6-luna" in captured["command"]
    assert 'model_reasoning_effort="max"' in captured["command"]
    assert "sys" in captured["input"]
    assert "usr" in captured["input"]
    assert captured["timeout"] == 1800.0


def test_call_json_raises_on_invalid_json(monkeypatch):
    from worker.services import llm

    def fake_run(command, **kwargs):
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text("not a json", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)

    with pytest.raises(llm.LLMError, match="некорректный JSON"):
        llm.call_json(system="s", user="u", schema={}, schema_name="x")


def test_call_json_raises_on_codex_failure(monkeypatch):
    from worker.services import llm

    monkeypatch.setattr(
        llm.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 2, stdout="", stderr="model is unavailable"
        ),
    )

    with pytest.raises(llm.LLMError, match="model is unavailable"):
        llm.call_json(system="s", user="u", schema={}, schema_name="x")
