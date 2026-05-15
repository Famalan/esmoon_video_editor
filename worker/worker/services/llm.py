from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from worker.config import settings


class LLMError(RuntimeError):
    pass


def _client() -> OpenAI:
    return OpenAI(api_key=settings.polza_api_key, base_url=settings.polza_base_url)


def call_json(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
    model: str | None = None,
    temperature: float = 0.2,
) -> dict[str, Any]:
    client = _client()
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
    )
    content = response.choices[0].message.content or ""
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM returned invalid JSON: {content[:200]}") from exc
