from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from worker.config import settings


class LLMError(RuntimeError):
    pass


def _client(timeout: float = 600.0) -> OpenAI:
    return OpenAI(
        api_key=settings.polza_api_key,
        base_url=settings.polza_base_url,
        timeout=timeout,
    )


def call_json(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float = 600.0,
    max_tokens: int = 16384,
) -> dict[str, Any]:
    client = _client(timeout=timeout)
    response = client.chat.completions.create(
        model=model or settings.polza_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema, "strict": True},
        },
        temperature=temperature,
        max_completion_tokens=max_tokens,
    )
    content = response.choices[0].message.content or ""
    finish_reason = response.choices[0].finish_reason
    if finish_reason == "length":
        raise LLMError(f"LLM output truncated by max_tokens (got {len(content)} chars)")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM returned invalid JSON: {content[:200]}") from exc
