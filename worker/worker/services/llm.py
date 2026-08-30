from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from worker.config import settings


class LLMError(RuntimeError):
    pass


def _prompt(system: str, user: str, schema_name: str) -> str:
    return (
        f"Системная инструкция:\n{system}\n\n"
        f"Задача пользователя:\n{user}\n\n"
        f"Верни только JSON, который соответствует схеме {schema_name}. "
        "Не читай файлы и не выполняй команды: анализируй только текст выше."
    )


def _error_tail(value: str, limit: int = 1000) -> str:
    clean = value.strip()
    return clean[-limit:] if clean else "без диагностического сообщения"


def call_json(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float | None = None,
    max_tokens: int = 16384,
) -> dict[str, Any]:
    # Codex CLI сам управляет temperature и лимитом ответа для выбранной модели.
    # Параметры оставлены в интерфейсе, чтобы не ломать существующие вызовы.
    del temperature, max_tokens

    selected_model = model or settings.codex_model
    selected_timeout = timeout or settings.codex_timeout_sec

    with tempfile.TemporaryDirectory(prefix="esmoon-codex-") as tmp:
        workdir = Path(tmp)
        schema_path = workdir / "output-schema.json"
        output_path = workdir / "result.json"
        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")

        command = [
            settings.codex_cli_path,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-m",
            selected_model,
            "-c",
            f'model_reasoning_effort="{settings.codex_reasoning_effort}"',
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
            "-",
        ]

        try:
            result = subprocess.run(
                command,
                input=_prompt(system, user, schema_name),
                text=True,
                capture_output=True,
                timeout=selected_timeout,
                cwd=workdir,
                check=False,
            )
        except FileNotFoundError as exc:
            raise LLMError(
                f"Codex CLI не найден по пути: {settings.codex_cli_path}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError(
                f"Codex CLI не ответил за {selected_timeout:.0f} сек"
            ) from exc

        if result.returncode != 0:
            raise LLMError(
                f"Codex CLI завершился с кодом {result.returncode}: "
                f"{_error_tail(result.stderr)}"
            )
        if not output_path.exists():
            raise LLMError("Codex CLI не создал JSON-файл результата")

        content = output_path.read_text(encoding="utf-8")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError("Codex CLI вернул некорректный JSON") from exc
    if not isinstance(parsed, dict):
        raise LLMError("Codex CLI вернул JSON, но корень результата не является объектом")
    return parsed
