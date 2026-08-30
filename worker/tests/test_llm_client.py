from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


def test_call_json_passes_schema_and_returns_parsed(monkeypatch):
    from worker.services import llm

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        msg = MagicMock()
        msg.content = json.dumps({"segments": [{"start": 0, "end": 10, "title": "t", "summary": "s"}]})
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    fake_client = MagicMock()
    fake_client.chat.completions.create = fake_create
    monkeypatch.setattr(llm, "_client", lambda *a, **k: fake_client)

    schema = {"type": "object", "properties": {"segments": {"type": "array"}}, "required": ["segments"]}
    result = llm.call_json(
        system="sys",
        user="usr",
        schema=schema,
        schema_name="segment_schema",
    )

    assert result == {"segments": [{"start": 0, "end": 10, "title": "t", "summary": "s"}]}
    assert captured["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["name"] == "segment_schema"
    assert captured["response_format"]["json_schema"]["schema"] == schema


def test_call_json_raises_on_invalid_json(monkeypatch):
    from worker.services import llm

    msg = MagicMock()
    msg.content = "not a json"
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]

    fake_client = MagicMock()
    fake_client.chat.completions.create = MagicMock(return_value=resp)
    monkeypatch.setattr(llm, "_client", lambda *a, **k: fake_client)

    with pytest.raises(llm.LLMError):
        llm.call_json(system="s", user="u", schema={}, schema_name="x")
