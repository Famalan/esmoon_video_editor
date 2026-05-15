# Video Slicer — Phase 2 Real Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить 6 заглушек стадий из Phase 1 на работающий пайплайн: yt-dlp скачивает видео и субтитры, polza.ai (`google/gemini-3.1-flash-lite`) режет транскрипт на 5–15-минутные сегменты, ffmpeg нарезает клипы и превью, второй LLM-вызов генерирует черновики YouTube-метаданных. UI позволяет редактировать метаданные и выбирать превью.

**Architecture:** Линейный Celery `chain` (как в Phase 1) с реальными стадиями. Артефакты лежат в MinIO; БД хранит только метаданные (ключи в MinIO, статусы, тексты). Загрузка в YouTube вынесена в Phase 3.

**Tech Stack:** Python 3.12 + Celery 5 + SQLAlchemy 2 + boto3 (MinIO S3 API) + `yt-dlp` + `faster-whisper` + `ffmpeg` (stream copy) + OpenAI-compatible HTTP клиент к `https://api.polza.ai/api/v1`. Frontend — Next.js 15 (App Router) + Tailwind.

---

## File Structure

```
worker/
├── pyproject.toml                          [modify]  Add: yt-dlp, faster-whisper, openai, boto3
├── Dockerfile                              [modify]  ENV WHISPER_CACHE_DIR=/whisper-cache
├── worker/
│   ├── config.py                           [new]     Pydantic-settings for polza/minio/whisper env
│   ├── models.py                           [modify]  Extend AssetKind, add fields
│   ├── services/
│   │   ├── __init__.py                     [new]
│   │   ├── storage.py                      [new]     MinIO/boto3 client + bucket create + upload/download
│   │   ├── llm.py                          [new]     polza.ai chat.completions with JSON schema
│   │   ├── vtt.py                          [new]     VTT → transcript.json parser
│   │   ├── ffmpeg.py                       [new]     cut, thumbnail, extract_audio, probe_duration
│   │   └── whisper.py                      [new]     faster-whisper lazy-load wrapper
│   ├── prompts/
│   │   ├── __init__.py                     [new]
│   │   ├── segment.py                      [new]     SYSTEM, USER_TEMPLATE, JSON_SCHEMA
│   │   └── metadata.py                     [new]     SYSTEM, USER_TEMPLATE, JSON_SCHEMA
│   └── tasks/
│       ├── fetch.py                        [rewrite] yt-dlp + MinIO upload
│       ├── transcribe.py                   [rewrite] VTT parser or Whisper fallback
│       ├── segment.py                      [rewrite] LLM call + validation + insert Segment rows
│       ├── cut.py                          [rewrite] ffmpeg stream-copy per segment
│       ├── thumbnail.py                    [rewrite] ffmpeg 3 frames per segment
│       └── metadata.py                     [rewrite] LLM call per segment → Upload rows
└── tests/
    ├── test_vtt_parser.py                  [new]
    ├── test_segment_validator.py           [new]
    ├── test_thumbnail_offsets.py           [new]
    ├── test_llm_client.py                  [new]
    └── test_ffmpeg_cut.py                  [new]

api/
├── pyproject.toml                          [modify]  Add: boto3
├── alembic/versions/
│   └── 0002_phase2_schema.py               [new]     Extend asset_kind, add fields, FK
├── app/
│   ├── models.py                           [modify]  Same changes as worker/models.py
│   ├── schemas.py                          [modify]  + SegmentOut, SegmentPatch, ThumbnailOut
│   ├── main.py                             [modify]  include segments and assets routers
│   ├── services/
│   │   ├── __init__.py                     [new]
│   │   └── storage.py                      [new]     boto3 presigned URL generator
│   └── routers/
│       ├── segments.py                     [new]     GET/PATCH /segments/{id}, GET /jobs/{id}/segments, GET /segments/{id}/download
│       └── assets.py                       [new]     GET /assets/{id}/url
└── tests/
    └── test_segments_router.py             [new]

web/
├── lib/api.ts                              [modify]  + Segment, Upload types; listSegments, patchSegment, segmentDownloadUrl, assetUrl
├── app/jobs/[id]/page.tsx                  [modify]  Render <SegmentList> after status=succeeded
└── components/
    ├── SegmentList.tsx                     [new]     Server component, fetches list, renders cards
    ├── SegmentCard.tsx                     [new]     Client, one segment with picker+editor
    ├── ThumbnailPicker.tsx                 [new]     Client, 3 buttons with selected highlight
    └── MetadataEditor.tsx                  [new]     Client, debounced PATCH

docker-compose.yml                          [modify]  whisper_models volume on worker
.env.example                                [modify]  Add POLZA_API_KEY, POLZA_BASE_URL, POLZA_MODEL, WHISPER_MODEL
```

---

## Task 1: Зависимости и Docker

**Files:**
- Modify: `worker/pyproject.toml`
- Modify: `worker/Dockerfile`
- Modify: `api/pyproject.toml`
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: Расширить `worker/pyproject.toml`**

Заменить блок `[tool.poetry.dependencies]`:

```toml
[tool.poetry.dependencies]
python = "^3.12"
celery = { version = "^5.4", extras = ["redis"] }
sqlalchemy = "^2.0"
psycopg = { version = "^3.2", extras = ["binary"] }
redis = "^5.2"
pydantic = "^2.9"
pydantic-settings = "^2.6"
boto3 = "^1.35"
yt-dlp = "^2024.10.7"
faster-whisper = "^1.0"
openai = "^1.50"
video-slicer-shared = { path = "../shared", develop = true }
```

- [ ] **Step 2: Расширить `worker/Dockerfile`**

Заменить файл целиком:

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir poetry==1.8.4 && poetry config virtualenvs.create false
COPY worker/pyproject.toml /app/
COPY shared /shared
RUN poetry install --no-root --no-interaction
COPY worker /app
ENV PYTHONPATH=/app:/shared \
    WHISPER_CACHE_DIR=/whisper-cache
```

- [ ] **Step 3: Расширить `api/pyproject.toml`**

В блоке `[tool.poetry.dependencies]` добавить строку перед `video-slicer-shared`:

```toml
boto3 = "^1.35"
```

- [ ] **Step 4: Расширить `docker-compose.yml`**

В сервисе `worker` секцию `volumes` заменить на:

```yaml
    volumes:
      - ./worker:/app
      - ./shared:/shared
      - whisper_models:/whisper-cache
```

В конце файла блок `volumes:` заменить на:

```yaml
volumes:
  pg_data:
  minio_data:
  whisper_models:
```

- [ ] **Step 5: Расширить `.env.example`**

Дописать в конец файла:

```bash
# LLM (polza.ai, OpenAI-compatible gateway)
POLZA_API_KEY=
POLZA_BASE_URL=https://api.polza.ai/api/v1
POLZA_MODEL=google/gemini-3.1-flash-lite

# Whisper fallback
WHISPER_MODEL=base
```

- [ ] **Step 6: Пересобрать образы и убедиться что worker стартует**

Run:
```bash
docker compose build worker api
docker compose up -d worker
sleep 5
docker compose logs --tail=20 worker
```
Expected: `celery@... ready.` в логах, никаких ImportError.

- [ ] **Step 7: Commit**

```bash
git add worker/pyproject.toml worker/Dockerfile api/pyproject.toml docker-compose.yml .env.example
git commit -m "chore: add phase-2 runtime deps (yt-dlp, faster-whisper, openai, boto3) and whisper volume"
```

---

## Task 2: Миграция 0002 — расширение Asset и Segment

**Files:**
- Modify: `api/app/models.py`
- Modify: `worker/worker/models.py`
- Create: `api/alembic/versions/0002_phase2_schema.py`

- [ ] **Step 1: Расширить `AssetKind` в `api/app/models.py`**

Заменить класс `AssetKind`:

```python
class AssetKind(StrEnum):
    SOURCE_VIDEO = "source_video"
    SOURCE_SUBS = "source_subs"
    SOURCE_AUDIO = "source_audio"
    TRANSCRIPT = "transcript"
    SEGMENT_VIDEO = "segment_video"
    THUMBNAIL = "thumbnail"
```

- [ ] **Step 2: Добавить поле `position_idx` в `Asset` и `selected_thumbnail_id` в `Segment` (api/app/models.py)**

В классе `Asset` под `segment_id` добавить строку:

```python
    position_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

В классе `Segment` под `error: Mapped[str | None] = ...` добавить:

```python
    selected_thumbnail_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
```

- [ ] **Step 3: Зеркально применить изменения в `worker/worker/models.py`**

Та же замена `AssetKind` и те же два новых поля. Файл должен по содержимому совпадать с `api/app/models.py` кроме строки `from app.db import Base` (в worker — `from worker.db import Base`).

- [ ] **Step 4: Сгенерировать миграцию вручную (без autogenerate, чтобы корректно обработать enum-rebuild)**

Создать файл `api/alembic/versions/0002_phase2_schema.py`:

```python
"""phase 2 schema: expanded asset_kind, position_idx, selected_thumbnail_id

Revision ID: 0002_phase2
Revises: 16ff47b418aa
Create Date: 2026-05-15 12:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_phase2"
down_revision = "16ff47b418aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) rebuild asset_kind enum: drop old, create new (dev: no data preserved across phase boundaries)
    op.execute("ALTER TABLE assets DROP COLUMN kind")
    op.execute("DROP TYPE asset_kind")
    asset_kind = sa.Enum(
        "source_video", "source_subs", "source_audio",
        "transcript", "segment_video", "thumbnail",
        name="asset_kind",
    )
    asset_kind.create(op.get_bind())
    op.add_column(
        "assets",
        sa.Column("kind", asset_kind, nullable=False),
    )

    # 2) add Asset.position_idx
    op.add_column(
        "assets",
        sa.Column("position_idx", sa.Integer(), nullable=True),
    )

    # 3) add Segment.selected_thumbnail_id (FK on assets.id, SET NULL)
    op.add_column(
        "segments",
        sa.Column("selected_thumbnail_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "segments_selected_thumbnail_id_fkey",
        "segments", "assets",
        ["selected_thumbnail_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("segments_selected_thumbnail_id_fkey", "segments", type_="foreignkey")
    op.drop_column("segments", "selected_thumbnail_id")
    op.drop_column("assets", "position_idx")
    op.execute("ALTER TABLE assets DROP COLUMN kind")
    op.execute("DROP TYPE asset_kind")
    asset_kind = sa.Enum("source", "transcript", "segment", "thumbnail", name="asset_kind")
    asset_kind.create(op.get_bind())
    op.add_column("assets", sa.Column("kind", asset_kind, nullable=False))
```

- [ ] **Step 5: Прогнать миграцию на чистой БД**

Run:
```bash
docker compose down -v
docker compose up -d postgres
sleep 5
docker compose run --rm api alembic upgrade head
docker compose exec postgres psql -U videoslicer -d videoslicer -c '\d assets'
docker compose exec postgres psql -U videoslicer -d videoslicer -c '\d segments'
```

Expected: `assets` имеет столбцы `kind` (asset_kind enum), `position_idx integer`; `segments` имеет `selected_thumbnail_id uuid`.

- [ ] **Step 6: Commit**

```bash
git add api/app/models.py worker/worker/models.py api/alembic/versions/0002_phase2_schema.py
git commit -m "feat(db): migration 0002 — expand asset_kind, add position_idx and selected_thumbnail_id"
```

---

## Task 3: Worker config + storage service

**Files:**
- Create: `worker/worker/config.py`
- Create: `worker/worker/services/__init__.py`
- Create: `worker/worker/services/storage.py`
- Create: `api/app/services/__init__.py`
- Create: `api/app/services/storage.py`

- [ ] **Step 1: Создать `worker/worker/config.py`**

```python
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "videoslicer"
    postgres_password: str = "videoslicer"
    postgres_db: str = "videoslicer"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_url: str = "redis://redis:6379/0"

    minio_endpoint: str = "http://minio:9000"
    minio_root_user: str = "minioadmin"
    minio_root_password: str = "minioadmin"
    minio_bucket: str = "video-slicer"

    polza_api_key: str = ""
    polza_base_url: str = "https://api.polza.ai/api/v1"
    polza_model: str = "google/gemini-3.1-flash-lite"

    whisper_model: str = "base"
    whisper_cache_dir: str = "/whisper-cache"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = WorkerSettings()
```

- [ ] **Step 2: Создать `worker/worker/services/__init__.py`** (пустой файл)

```python
```

- [ ] **Step 3: Создать `worker/worker/services/storage.py`**

```python
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.config import Config

from worker.config import settings


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.minio_endpoint,
        aws_access_key_id=settings.minio_root_user,
        aws_secret_access_key=settings.minio_root_password,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket() -> None:
    c = _client()
    existing = {b["Name"] for b in c.list_buckets().get("Buckets", [])}
    if settings.minio_bucket not in existing:
        c.create_bucket(Bucket=settings.minio_bucket)


def upload_file(local_path: Path, key: str, content_type: str) -> int:
    ensure_bucket()
    c = _client()
    c.upload_file(
        str(local_path),
        settings.minio_bucket,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    return local_path.stat().st_size


def upload_bytes(body: bytes, key: str, content_type: str) -> int:
    ensure_bucket()
    c = _client()
    c.put_object(Bucket=settings.minio_bucket, Key=key, Body=body, ContentType=content_type)
    return len(body)


def download_file(key: str, local_path: Path) -> None:
    c = _client()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    c.download_file(settings.minio_bucket, key, str(local_path))


def download_bytes(key: str) -> bytes:
    c = _client()
    obj = c.get_object(Bucket=settings.minio_bucket, Key=key)
    return obj["Body"].read()
```

- [ ] **Step 4: Создать `api/app/services/__init__.py`** (пустой файл)

```python
```

- [ ] **Step 5: Создать `api/app/services/storage.py`**

```python
from __future__ import annotations

import boto3
from botocore.config import Config

from app.config import settings


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.minio_endpoint,
        aws_access_key_id=settings.minio_root_user,
        aws_secret_access_key=settings.minio_root_password,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def presigned_get_url(key: str, expires_in: int = 3600) -> str:
    c = _client()
    return c.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.minio_bucket, "Key": key},
        ExpiresIn=expires_in,
    )
```

- [ ] **Step 6: Расширить `api/app/config.py`** (если ещё нет minio полей)

Открыть `api/app/config.py` и убедиться что класс `Settings` содержит поля `minio_endpoint`, `minio_root_user`, `minio_root_password`, `minio_bucket`. Если каких-то нет — добавить со значениями по умолчанию как в `worker/worker/config.py`.

- [ ] **Step 7: Smoke-проверка bucket-создания**

Run:
```bash
docker compose up -d minio worker
sleep 3
docker compose exec worker python -c "from worker.services.storage import ensure_bucket; ensure_bucket(); print('ok')"
docker compose exec minio mc alias set local http://localhost:9000 minioadmin minioadmin >/dev/null 2>&1 || true
docker compose exec minio mc ls local/video-slicer/ 2>&1 || echo "bucket created"
```
Expected: `ok` без ошибок, бакет существует.

- [ ] **Step 8: Commit**

```bash
git add worker/worker/config.py worker/worker/services/ api/app/services/ api/app/config.py
git commit -m "feat: minio storage clients for worker (boto3 upload/download) and api (presigned url)"
```

---

## Task 4: LLM-клиент и prompt-модули

**Files:**
- Create: `worker/worker/services/llm.py`
- Create: `worker/worker/prompts/__init__.py`
- Create: `worker/worker/prompts/segment.py`
- Create: `worker/worker/prompts/metadata.py`
- Create: `worker/tests/test_llm_client.py`

- [ ] **Step 1: Создать `worker/worker/prompts/__init__.py`** (пустой)

```python
```

- [ ] **Step 2: Создать `worker/worker/prompts/segment.py`**

```python
SYSTEM = """Ты — редактор YouTube-канала с многолетним опытом. Тебе дан транскрипт длинного видео с таймкодами. Разбей его на 4–10 смысловых сегментов длительностью 5–15 минут каждый.

Принципы:
1. Каждый сегмент — самостоятельная мысль, история или часть аргументации, которую можно опубликовать как отдельный YouTube-ролик.
2. Соседние сегменты идут по порядку, не пересекаются, покрывают всё видео целиком.
3. Границы — на естественных паузах (смена темы, риторический вопрос, переход).
4. Если видео короче 5 минут — верни один сегмент на всё видео.

Верни строго JSON по схеме. Никакого другого текста."""


USER_TEMPLATE = """Транскрипт (длительность {duration_sec:.1f} сек):

{transcript_lines}"""


JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {
                "type": "object",
                "required": ["start", "end", "title", "summary"],
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string", "maxLength": 120},
                    "summary": {"type": "string", "maxLength": 500},
                },
                "additionalProperties": False,
            },
        }
    },
    "required": ["segments"],
    "additionalProperties": False,
}
```

- [ ] **Step 3: Создать `worker/worker/prompts/metadata.py`**

```python
SYSTEM = """Ты пишешь метаданные для YouTube-клипа. Видео — горизонтальное (16:9), длительность 5–15 минут, целевая аудитория — русскоязычная.

Требования:
- Заголовок ≤60 символов, цепляет, не кликбейт.
- Описание 200–500 слов: о чём ролик, ключевые мысли, кому будет полезно. Без призывов «подписаться/лайк» — это шаблон.
- Теги: 5–10 шт, на русском, через массив.

Верни строго JSON по схеме."""


USER_TEMPLATE = """Заголовок сегмента: {segment_title}
Краткое содержание: {segment_summary}

Полный текст сегмента:
{segment_transcript}"""


JSON_SCHEMA = {
    "type": "object",
    "required": ["title", "description", "tags"],
    "properties": {
        "title": {"type": "string", "maxLength": 60},
        "description": {"type": "string", "maxLength": 2000},
        "tags": {
            "type": "array",
            "minItems": 5,
            "maxItems": 10,
            "items": {"type": "string", "maxLength": 30},
        },
    },
    "additionalProperties": False,
}
```

- [ ] **Step 4: TDD — написать тест `worker/tests/test_llm_client.py`**

```python
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
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    fake_client = MagicMock()
    fake_client.chat.completions.create = fake_create
    monkeypatch.setattr(llm, "_client", lambda: fake_client)

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
    resp = MagicMock()
    resp.choices = [choice]

    fake_client = MagicMock()
    fake_client.chat.completions.create = MagicMock(return_value=resp)
    monkeypatch.setattr(llm, "_client", lambda: fake_client)

    with pytest.raises(llm.LLMError):
        llm.call_json(system="s", user="u", schema={}, schema_name="x")
```

- [ ] **Step 5: Запустить тест, убедиться что падает с ImportError**

Run:
```bash
docker compose run --rm worker pytest tests/test_llm_client.py -v
```
Expected: FAIL `ModuleNotFoundError: No module named 'worker.services.llm'`

- [ ] **Step 6: Создать `worker/worker/services/llm.py`**

```python
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
```

- [ ] **Step 7: Запустить тест и убедиться что проходит**

Run:
```bash
docker compose run --rm worker pytest tests/test_llm_client.py -v
```
Expected: 2 passed.

- [ ] **Step 8: Commit**

```bash
git add worker/worker/services/llm.py worker/worker/prompts/ worker/tests/test_llm_client.py
git commit -m "feat(worker): polza.ai chat client with JSON schema + prompts for segment/metadata"
```

---

## Task 5: VTT-парсер

**Files:**
- Create: `worker/worker/services/vtt.py`
- Create: `worker/tests/test_vtt_parser.py`
- Create: `worker/tests/fixtures/sample.vtt`

- [ ] **Step 1: Создать фикстуру `worker/tests/fixtures/sample.vtt`**

```
WEBVTT
Kind: captions
Language: ru

00:00:00.080 --> 00:00:03.520
Привет, сегодня поговорим
о времени.

00:00:03.520 --> 00:00:07.200
Как мы его теряем по чуть-чуть.

00:00:07.200 --> 00:00:12.000
Тысяча мелких порезов
вместо одного большого.
```

- [ ] **Step 2: TDD — написать тест `worker/tests/test_vtt_parser.py`**

```python
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "sample.vtt"


def test_parse_vtt_returns_cues_in_order():
    from worker.services.vtt import parse_vtt

    cues = parse_vtt(FIXTURE.read_text(encoding="utf-8"))

    assert len(cues) == 3
    assert cues[0]["start"] == pytest.approx(0.08, abs=0.01)
    assert cues[0]["end"] == pytest.approx(3.52, abs=0.01)
    assert cues[0]["text"] == "Привет, сегодня поговорим о времени."
    assert cues[1]["text"] == "Как мы его теряем по чуть-чуть."
    assert cues[2]["start"] == pytest.approx(7.20, abs=0.01)


def test_parse_vtt_skips_header_and_empty():
    from worker.services.vtt import parse_vtt

    cues = parse_vtt("WEBVTT\n\n")
    assert cues == []


def test_parse_vtt_handles_hour_timestamps():
    from worker.services.vtt import parse_vtt

    text = "WEBVTT\n\n01:02:03.400 --> 01:02:05.800\nдалёкий cue\n"
    cues = parse_vtt(text)
    assert cues[0]["start"] == pytest.approx(3723.4, abs=0.01)
    assert cues[0]["end"] == pytest.approx(3725.8, abs=0.01)
```

- [ ] **Step 3: Запустить тест, убедиться что падает**

Run:
```bash
docker compose run --rm worker pytest tests/test_vtt_parser.py -v
```
Expected: FAIL — ModuleNotFoundError.

- [ ] **Step 4: Создать `worker/worker/services/vtt.py`**

```python
from __future__ import annotations

import re
from typing import TypedDict


_TS_RE = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{3})"
    r"\s*-->\s*"
    r"(?P<eh>\d{1,2}):(?P<em>\d{2}):(?P<es>\d{2})\.(?P<ems>\d{3})"
)


class Cue(TypedDict):
    start: float
    end: float
    text: str


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(content: str) -> list[Cue]:
    cues: list[Cue] = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = _TS_RE.match(line)
        if not m:
            i += 1
            continue
        start = _ts_to_sec(m["h"], m["m"], m["s"], m["ms"])
        end = _ts_to_sec(m["eh"], m["em"], m["es"], m["ems"])
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        if text_lines:
            cues.append(Cue(start=start, end=end, text=" ".join(text_lines)))
    return cues
```

- [ ] **Step 5: Запустить тест и убедиться что зелёный**

Run:
```bash
docker compose run --rm worker pytest tests/test_vtt_parser.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add worker/worker/services/vtt.py worker/tests/test_vtt_parser.py worker/tests/fixtures/
git commit -m "feat(worker): vtt parser to normalized transcript cues"
```

---

## Task 6: ffmpeg-обёртки

**Files:**
- Create: `worker/worker/services/ffmpeg.py`
- Create: `worker/tests/test_ffmpeg_cut.py`

- [ ] **Step 1: TDD — написать тест `worker/tests/test_ffmpeg_cut.py`**

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _make_test_video(path: Path, duration: int = 30) -> None:
    """Create a synthetic mp4 of given duration with testsrc + sine audio."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=640x360:rate=25",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
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
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run:
```bash
docker compose run --rm worker pytest tests/test_ffmpeg_cut.py -v
```
Expected: FAIL — ModuleNotFoundError.

- [ ] **Step 3: Создать `worker/worker/services/ffmpeg.py`**

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
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


def cut_segment(*, src: Path, dst: Path, start_sec: float, end_sec: float) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_sec:.3f}",
        "-to", f"{end_sec:.3f}",
        "-i", str(src),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        str(dst),
    ])


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
```

- [ ] **Step 4: Запустить тест и убедиться что зелёный**

Run:
```bash
docker compose run --rm worker pytest tests/test_ffmpeg_cut.py -v
```
Expected: 4 passed (ffmpeg уже установлен в worker image из Phase 1).

- [ ] **Step 5: Commit**

```bash
git add worker/worker/services/ffmpeg.py worker/tests/test_ffmpeg_cut.py
git commit -m "feat(worker): ffmpeg wrappers — cut_segment (stream copy), extract_thumbnail, extract_audio, probe_duration"
```

---

## Task 7: Whisper-обёртка с ленивой загрузкой

**Files:**
- Create: `worker/worker/services/whisper.py`

- [ ] **Step 1: Создать `worker/worker/services/whisper.py`**

```python
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TypedDict

from worker.config import settings


class Cue(TypedDict):
    start: float
    end: float
    text: str


@lru_cache(maxsize=1)
def _get_model():
    from faster_whisper import WhisperModel

    return WhisperModel(
        settings.whisper_model,
        device="cpu",
        compute_type="int8",
        download_root=settings.whisper_cache_dir,
    )


def transcribe_audio(path: Path, language: str = "ru") -> list[Cue]:
    model = _get_model()
    segments, _info = model.transcribe(
        str(path),
        language=language,
        beam_size=1,
        vad_filter=False,
    )
    return [
        Cue(start=float(s.start), end=float(s.end), text=s.text.strip())
        for s in segments
    ]
```

- [ ] **Step 2: Sanity-check, что модуль импортируется без падения**

Run:
```bash
docker compose run --rm worker python -c "from worker.services.whisper import transcribe_audio; print('import ok')"
```
Expected: `import ok`. (Сама модель загружается лениво при первом вызове `_get_model()` — не сейчас.)

- [ ] **Step 3: Commit**

```bash
git add worker/worker/services/whisper.py
git commit -m "feat(worker): faster-whisper wrapper with lazy model load and int8 cpu inference"
```

---

## Task 8: Стадия `fetch` — yt-dlp + MinIO

**Files:**
- Modify: `worker/worker/tasks/fetch.py`

- [ ] **Step 1: Переписать `worker/worker/tasks/fetch.py`**

```python
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, Job, JobStatus
from worker.progress import publish_progress
from worker.services import storage
from shared.stages import Stage


def _ytdlp_download(url: str, workdir: Path) -> tuple[Path, Path | None]:
    """Возвращает (path_to_mp4, path_to_vtt_or_None). Бросает RuntimeError при ошибке."""
    import subprocess

    video_tmpl = str(workdir / "source.%(ext)s")
    vid = subprocess.run(
        [
            "yt-dlp",
            "-f", "bestvideo[height<=1080]+bestaudio/best",
            "--merge-output-format", "mp4",
            "-o", video_tmpl,
            "--no-warnings",
            "--quiet",
            url,
        ],
        capture_output=True,
        text=True,
    )
    if vid.returncode != 0:
        raise RuntimeError(f"yt-dlp video failed: {(vid.stderr or '')[-400:]}")

    mp4 = workdir / "source.mp4"
    if not mp4.exists():
        candidates = list(workdir.glob("source.*"))
        if not candidates:
            raise RuntimeError("yt-dlp produced no source file")
        mp4 = candidates[0]

    subs_tmpl = str(workdir / "subs")
    subprocess.run(
        [
            "yt-dlp",
            "--skip-download",
            "--write-auto-subs",
            "--write-subs",
            "--sub-lang", "ru",
            "--sub-format", "vtt",
            "-o", subs_tmpl,
            "--no-warnings",
            "--quiet",
            url,
        ],
        capture_output=True,
        text=True,
    )
    vtt_files = list(workdir.glob("subs*.vtt"))
    return mp4, (vtt_files[0] if vtt_files else None)


@app.task(name="worker.tasks.fetch.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.FETCH, "running")

    with session_scope() as db:
        job = db.get(Job, job_id)
        job.status = JobStatus.RUNNING
        job.current_stage = Stage.FETCH
        url = job.source_url
        db.commit()

    if not url:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = "fetch: source_url is required in Phase 2"
            db.commit()
        publish_progress(job_id, Stage.FETCH, "failed")
        raise RuntimeError("source_url required")

    tmp = Path(tempfile.mkdtemp(prefix=f"fetch_{job_id}_"))
    try:
        mp4, vtt = _ytdlp_download(url, tmp)

        video_key = f"{job_id}/source.mp4"
        size = storage.upload_file(mp4, video_key, "video/mp4")

        with session_scope() as db:
            db.add(Asset(
                job_id=job_id,
                kind=AssetKind.SOURCE_VIDEO,
                s3_key=video_key,
                mime="video/mp4",
                size_bytes=size,
            ))
            db.commit()

        if vtt is not None:
            subs_key = f"{job_id}/subs.vtt"
            subs_size = storage.upload_file(vtt, subs_key, "text/vtt")
            with session_scope() as db:
                db.add(Asset(
                    job_id=job_id,
                    kind=AssetKind.SOURCE_SUBS,
                    s3_key=subs_key,
                    mime="text/vtt",
                    size_bytes=subs_size,
                ))
                db.commit()
    except Exception as exc:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = f"fetch: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.FETCH, "failed")
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    publish_progress(job_id, Stage.FETCH, "done")
    return job_id
```

- [ ] **Step 2: Commit**

```bash
git add worker/worker/tasks/fetch.py
git commit -m "feat(worker): real fetch stage — yt-dlp download to MinIO with optional auto-subs"
```

(Sanity-check этой стадии будет на полном e2e — Task 16. Изолированно тестировать сложно: нужен реальный yt-dlp + интернет.)

---

## Task 9: Стадия `transcribe`

**Files:**
- Modify: `worker/worker/tasks/transcribe.py`

- [ ] **Step 1: Переписать `worker/worker/tasks/transcribe.py`**

```python
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from sqlalchemy import select

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, Job, JobStatus
from worker.progress import publish_progress
from worker.services import ffmpeg, storage, vtt, whisper
from shared.stages import Stage


@app.task(name="worker.tasks.transcribe.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.TRANSCRIBE, "running")
    with session_scope() as db:
        db.get(Job, job_id).current_stage = Stage.TRANSCRIBE
        db.commit()

    try:
        with session_scope() as db:
            subs_asset = db.execute(
                select(Asset).where(
                    Asset.job_id == job_id, Asset.kind == AssetKind.SOURCE_SUBS
                )
            ).scalar_one_or_none()

        if subs_asset is not None:
            vtt_text = storage.download_bytes(subs_asset.s3_key).decode("utf-8")
            cues = vtt.parse_vtt(vtt_text)
        else:
            cues = _transcribe_with_whisper(job_id)

        transcript_key = f"{job_id}/transcript.json"
        body = json.dumps(cues, ensure_ascii=False).encode("utf-8")
        size = storage.upload_bytes(body, transcript_key, "application/json")
        with session_scope() as db:
            db.add(Asset(
                job_id=job_id,
                kind=AssetKind.TRANSCRIPT,
                s3_key=transcript_key,
                mime="application/json",
                size_bytes=size,
            ))
            db.commit()
    except Exception as exc:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = f"transcribe: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.TRANSCRIBE, "failed")
        raise

    publish_progress(job_id, Stage.TRANSCRIBE, "done")
    return job_id


def _transcribe_with_whisper(job_id: str) -> list[dict]:
    with session_scope() as db:
        video_asset = db.execute(
            select(Asset).where(
                Asset.job_id == job_id, Asset.kind == AssetKind.SOURCE_VIDEO
            )
        ).scalar_one()
        video_key = video_asset.s3_key

    tmp = Path(tempfile.mkdtemp(prefix=f"whisper_{job_id}_"))
    try:
        mp4 = tmp / "source.mp4"
        mp3 = tmp / "audio.mp3"
        storage.download_file(video_key, mp4)
        ffmpeg.extract_audio(src=mp4, dst=mp3)

        audio_key = f"{job_id}/audio.mp3"
        audio_size = storage.upload_file(mp3, audio_key, "audio/mpeg")
        with session_scope() as db:
            db.add(Asset(
                job_id=job_id,
                kind=AssetKind.SOURCE_AUDIO,
                s3_key=audio_key,
                mime="audio/mpeg",
                size_bytes=audio_size,
            ))
            db.commit()

        cues = whisper.transcribe_audio(mp3, language="ru")
        return [dict(c) for c in cues]
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 2: Commit**

```bash
git add worker/worker/tasks/transcribe.py
git commit -m "feat(worker): real transcribe stage — vtt parser or whisper fallback"
```

---

## Task 10: Стадия `segment` — LLM + валидация

**Files:**
- Create: `worker/tests/test_segment_validator.py`
- Modify: `worker/worker/tasks/segment.py`

- [ ] **Step 1: TDD — написать тест `worker/tests/test_segment_validator.py`**

```python
from __future__ import annotations

import pytest


def test_validate_accepts_well_formed():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [
        {"start": 0.0, "end": 360.0, "title": "часть 1", "summary": "..."},
        {"start": 360.0, "end": 720.0, "title": "часть 2", "summary": "..."},
    ]
    out = validate_segments(segments, video_duration=720.0)
    assert len(out) == 2
    assert out[0]["end"] == 360.0


def test_validate_rejects_overlap():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [
        {"start": 0.0, "end": 400.0, "title": "a", "summary": "."},
        {"start": 300.0, "end": 700.0, "title": "b", "summary": "."},
    ]
    with pytest.raises(SegmentValidationError, match="overlap"):
        validate_segments(segments, video_duration=700.0)


def test_validate_rejects_too_short():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [{"start": 0.0, "end": 30.0, "title": "a", "summary": "."}]
    with pytest.raises(SegmentValidationError, match="duration"):
        validate_segments(segments, video_duration=3600.0)


def test_validate_rejects_too_long():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [{"start": 0.0, "end": 1500.0, "title": "a", "summary": "."}]
    with pytest.raises(SegmentValidationError, match="duration"):
        validate_segments(segments, video_duration=3600.0)


def test_validate_rejects_overshoot():
    from worker.tasks.segment import validate_segments, SegmentValidationError

    segments = [{"start": 0.0, "end": 700.0, "title": "a", "summary": "."}]
    with pytest.raises(SegmentValidationError, match="exceeds"):
        validate_segments(segments, video_duration=600.0)


def test_validate_allows_short_video_single_segment():
    from worker.tasks.segment import validate_segments

    segments = [{"start": 0.0, "end": 200.0, "title": "a", "summary": "."}]
    out = validate_segments(segments, video_duration=200.0)
    assert len(out) == 1
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run:
```bash
docker compose run --rm worker pytest tests/test_segment_validator.py -v
```
Expected: FAIL — ImportError.

- [ ] **Step 3: Переписать `worker/worker/tasks/segment.py`**

```python
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from sqlalchemy import select

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, Job, JobStatus, Segment
from worker.progress import publish_progress
from worker.prompts import segment as prompt
from worker.services import ffmpeg, llm, storage
from shared.stages import Stage


class SegmentValidationError(ValueError):
    pass


_MIN_DURATION = 240.0   # 4 min (с запасом от 5 min target)
_MAX_DURATION = 1200.0  # 20 min (с запасом от 15 min target)
_DURATION_OVERSHOOT = 5.0
_OVERLAP_TOLERANCE = 1.0


def validate_segments(segments: list[dict], video_duration: float) -> list[dict]:
    if not segments:
        raise SegmentValidationError("empty segments")

    short_video = video_duration < _MIN_DURATION
    for i, s in enumerate(segments):
        start = float(s["start"])
        end = float(s["end"])
        dur = end - start

        if not short_video and not (_MIN_DURATION <= dur <= _MAX_DURATION):
            raise SegmentValidationError(
                f"segment {i} duration {dur:.0f}s out of [{_MIN_DURATION}, {_MAX_DURATION}]"
            )
        if end > video_duration + _DURATION_OVERSHOOT:
            raise SegmentValidationError(
                f"segment {i} end {end:.0f} exceeds video duration {video_duration:.0f}"
            )
        if i > 0 and start + _OVERLAP_TOLERANCE < segments[i - 1]["end"]:
            raise SegmentValidationError(
                f"segments {i-1} and {i} overlap"
            )
    return segments


@app.task(name="worker.tasks.segment.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.SEGMENT, "running")
    with session_scope() as db:
        db.get(Job, job_id).current_stage = Stage.SEGMENT
        db.commit()

    try:
        with session_scope() as db:
            transcript_asset = db.execute(
                select(Asset).where(
                    Asset.job_id == job_id, Asset.kind == AssetKind.TRANSCRIPT
                )
            ).scalar_one()
            video_asset = db.execute(
                select(Asset).where(
                    Asset.job_id == job_id, Asset.kind == AssetKind.SOURCE_VIDEO
                )
            ).scalar_one()
            video_key = video_asset.s3_key
            transcript_key = transcript_asset.s3_key

        cues: list[dict] = json.loads(storage.download_bytes(transcript_key).decode("utf-8"))

        tmp = Path(tempfile.mkdtemp(prefix=f"seg_{job_id}_"))
        try:
            mp4 = tmp / "source.mp4"
            storage.download_file(video_key, mp4)
            duration = ffmpeg.probe_duration(mp4)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

        transcript_text = _format_transcript(cues)
        user = prompt.USER_TEMPLATE.format(
            duration_sec=duration, transcript_lines=transcript_text
        )

        try:
            data = llm.call_json(
                system=prompt.SYSTEM, user=user,
                schema=prompt.JSON_SCHEMA, schema_name="video_segments",
            )
            segments = validate_segments(data["segments"], duration)
        except SegmentValidationError as first_err:
            retry_user = user + f"\n\nПрошлый ответ не прошёл валидацию: {first_err}. Исправь."
            data = llm.call_json(
                system=prompt.SYSTEM, user=retry_user,
                schema=prompt.JSON_SCHEMA, schema_name="video_segments",
            )
            segments = validate_segments(data["segments"], duration)

        with session_scope() as db:
            for idx, s in enumerate(segments):
                db.add(Segment(
                    job_id=job_id,
                    index=idx,
                    start_sec=float(s["start"]),
                    end_sec=float(s["end"]),
                    title=s["title"],
                    summary=s["summary"],
                    transcript_excerpt=_extract_excerpt(cues, s["start"], s["end"]),
                ))
            db.commit()
    except Exception as exc:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = f"segment: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.SEGMENT, "failed")
        raise

    publish_progress(job_id, Stage.SEGMENT, "done")
    return job_id


def _format_transcript(cues: list[dict]) -> str:
    lines = []
    for c in cues:
        ts = _fmt_ts(c["start"])
        lines.append(f"[{ts}] {c['text']}")
    return "\n".join(lines)


def _fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _extract_excerpt(cues: list[dict], start: float, end: float, max_chars: int = 500) -> str:
    parts: list[str] = []
    total = 0
    for c in cues:
        if c["end"] < start or c["start"] > end:
            continue
        parts.append(c["text"])
        total += len(c["text"])
        if total >= max_chars:
            break
    return " ".join(parts)[:max_chars]
```

- [ ] **Step 4: Запустить тест и убедиться что зелёный**

Run:
```bash
docker compose run --rm worker pytest tests/test_segment_validator.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add worker/worker/tasks/segment.py worker/tests/test_segment_validator.py
git commit -m "feat(worker): real segment stage — polza.ai LLM call with json schema and validator"
```

---

## Task 11: Стадия `cut` — ffmpeg stream copy

**Files:**
- Modify: `worker/worker/tasks/cut.py`

- [ ] **Step 1: Переписать `worker/worker/tasks/cut.py`**

```python
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, Job, JobStatus, Segment, SegmentStatus
from worker.progress import publish_progress
from worker.services import ffmpeg, storage
from shared.stages import Stage


@app.task(name="worker.tasks.cut.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.CUT, "running")
    with session_scope() as db:
        db.get(Job, job_id).current_stage = Stage.CUT
        db.commit()

    with session_scope() as db:
        video_asset = db.execute(
            select(Asset).where(
                Asset.job_id == job_id, Asset.kind == AssetKind.SOURCE_VIDEO
            )
        ).scalar_one()
        video_key = video_asset.s3_key
        segments = db.execute(
            select(Segment).where(Segment.job_id == job_id).order_by(Segment.index)
        ).scalars().all()
        segment_data = [
            (str(s.id), float(s.start_sec), float(s.end_sec)) for s in segments
        ]

    tmp = Path(tempfile.mkdtemp(prefix=f"cut_{job_id}_"))
    try:
        mp4 = tmp / "source.mp4"
        storage.download_file(video_key, mp4)

        any_ok = False
        for seg_id, start, end in segment_data:
            out = tmp / f"{seg_id}.mp4"
            try:
                ffmpeg.cut_segment(src=mp4, dst=out, start_sec=start, end_sec=end)
                key = f"{job_id}/segments/{seg_id}.mp4"
                size = storage.upload_file(out, key, "video/mp4")
                with session_scope() as db:
                    db.add(Asset(
                        job_id=job_id,
                        kind=AssetKind.SEGMENT_VIDEO,
                        s3_key=key,
                        mime="video/mp4",
                        size_bytes=size,
                        segment_id=seg_id,
                    ))
                    db.get(Segment, seg_id).status = SegmentStatus.CUT
                    db.commit()
                any_ok = True
            except Exception as exc:
                with session_scope() as db:
                    seg = db.get(Segment, seg_id)
                    seg.status = SegmentStatus.FAILED
                    seg.error = f"cut: {exc}"[:500]
                    db.commit()

        if not any_ok:
            raise RuntimeError("cut: all segments failed")
    except Exception as exc:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = f"cut: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.CUT, "failed")
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    publish_progress(job_id, Stage.CUT, "done")
    return job_id
```

- [ ] **Step 2: Commit**

```bash
git add worker/worker/tasks/cut.py
git commit -m "feat(worker): real cut stage — ffmpeg stream copy per segment with per-segment failure tolerance"
```

---

## Task 12: Стадия `thumbnail` — 3 кадра

**Files:**
- Create: `worker/tests/test_thumbnail_offsets.py`
- Modify: `worker/worker/tasks/thumbnail.py`

- [ ] **Step 1: TDD — написать тест `worker/tests/test_thumbnail_offsets.py`**

```python
from __future__ import annotations

import pytest


def test_offsets_for_normal_segment():
    from worker.tasks.thumbnail import calc_thumbnail_offsets

    offs = calc_thumbnail_offsets(start_sec=60.0, end_sec=360.0)
    assert offs == pytest.approx([75.0, 210.0, 345.0], abs=0.5)


def test_offsets_for_short_segment():
    from worker.tasks.thumbnail import calc_thumbnail_offsets

    offs = calc_thumbnail_offsets(start_sec=0.0, end_sec=2.0)
    # 5/50/95% of 2s
    assert offs[0] == pytest.approx(0.1, abs=0.05)
    assert offs[1] == pytest.approx(1.0, abs=0.05)
    assert offs[2] == pytest.approx(1.9, abs=0.05)


def test_offsets_never_negative_or_past_end():
    from worker.tasks.thumbnail import calc_thumbnail_offsets

    offs = calc_thumbnail_offsets(start_sec=0.0, end_sec=10.0)
    for o in offs:
        assert 0.0 <= o <= 10.0
```

- [ ] **Step 2: Запустить тест и убедиться что падает**

Run:
```bash
docker compose run --rm worker pytest tests/test_thumbnail_offsets.py -v
```
Expected: FAIL — ImportError.

- [ ] **Step 3: Переписать `worker/worker/tasks/thumbnail.py`**

```python
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, Job, JobStatus, Segment, SegmentStatus, Upload
from worker.progress import publish_progress
from worker.services import ffmpeg, storage
from shared.stages import Stage


def calc_thumbnail_offsets(*, start_sec: float, end_sec: float) -> list[float]:
    duration = end_sec - start_sec
    return [start_sec + duration * frac for frac in (0.05, 0.50, 0.95)]


@app.task(name="worker.tasks.thumbnail.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.THUMBNAIL, "running")
    with session_scope() as db:
        db.get(Job, job_id).current_stage = Stage.THUMBNAIL
        db.commit()

    with session_scope() as db:
        video_asset = db.execute(
            select(Asset).where(
                Asset.job_id == job_id, Asset.kind == AssetKind.SOURCE_VIDEO
            )
        ).scalar_one()
        video_key = video_asset.s3_key
        segments = db.execute(
            select(Segment).where(
                Segment.job_id == job_id, Segment.status == SegmentStatus.CUT
            ).order_by(Segment.index)
        ).scalars().all()
        segment_data = [
            (str(s.id), float(s.start_sec), float(s.end_sec)) for s in segments
        ]

    tmp = Path(tempfile.mkdtemp(prefix=f"thumb_{job_id}_"))
    try:
        mp4 = tmp / "source.mp4"
        storage.download_file(video_key, mp4)

        for seg_id, start, end in segment_data:
            offsets = calc_thumbnail_offsets(start_sec=start, end_sec=end)
            asset_ids: list[str] = []
            for idx, off in enumerate(offsets):
                out = tmp / f"{seg_id}_{idx}.jpg"
                try:
                    ffmpeg.extract_thumbnail(src=mp4, dst=out, at_sec=off)
                    key = f"{job_id}/thumbnails/{seg_id}/{idx}.jpg"
                    size = storage.upload_file(out, key, "image/jpeg")
                    with session_scope() as db:
                        a = Asset(
                            job_id=job_id,
                            kind=AssetKind.THUMBNAIL,
                            s3_key=key,
                            mime="image/jpeg",
                            size_bytes=size,
                            segment_id=seg_id,
                            position_idx=idx,
                        )
                        db.add(a)
                        db.commit()
                        asset_ids.append(str(a.id))
                except Exception as exc:
                    with session_scope() as db:
                        seg = db.get(Segment, seg_id)
                        seg.error = (seg.error or "") + f" thumb{idx}: {exc};"
                        db.commit()

            if len(asset_ids) >= 2:
                middle_asset_id = asset_ids[1] if len(asset_ids) >= 2 else asset_ids[0]
                with session_scope() as db:
                    seg = db.get(Segment, seg_id)
                    seg.selected_thumbnail_id = middle_asset_id
                    seg.status = SegmentStatus.THUMBNAIL_READY
                    db.commit()
    except Exception as exc:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = f"thumbnail: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.THUMBNAIL, "failed")
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    publish_progress(job_id, Stage.THUMBNAIL, "done")
    return job_id
```

- [ ] **Step 4: Запустить тест и убедиться что зелёный**

Run:
```bash
docker compose run --rm worker pytest tests/test_thumbnail_offsets.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add worker/worker/tasks/thumbnail.py worker/tests/test_thumbnail_offsets.py
git commit -m "feat(worker): real thumbnail stage — 3 frames per segment with middle as default selection"
```

---

## Task 13: Стадия `metadata` — LLM-черновики

**Files:**
- Modify: `worker/worker/tasks/metadata.py`

- [ ] **Step 1: Переписать `worker/worker/tasks/metadata.py`**

```python
from __future__ import annotations

from sqlalchemy import select

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Job, JobStatus, Segment, SegmentStatus, Upload, UploadStatus
from worker.progress import publish_progress
from worker.prompts import metadata as prompt
from worker.services import llm
from shared.stages import Stage


@app.task(name="worker.tasks.metadata.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.METADATA, "running")
    with session_scope() as db:
        db.get(Job, job_id).current_stage = Stage.METADATA
        db.commit()

    with session_scope() as db:
        segments = db.execute(
            select(Segment).where(
                Segment.job_id == job_id,
                Segment.status == SegmentStatus.THUMBNAIL_READY,
            ).order_by(Segment.index)
        ).scalars().all()
        segment_data = [
            (str(s.id), s.title or "", s.summary or "", s.transcript_excerpt or "")
            for s in segments
        ]

    for seg_id, title, summary, excerpt in segment_data:
        try:
            user = prompt.USER_TEMPLATE.format(
                segment_title=title,
                segment_summary=summary,
                segment_transcript=excerpt,
            )
            data = llm.call_json(
                system=prompt.SYSTEM, user=user,
                schema=prompt.JSON_SCHEMA, schema_name="youtube_metadata",
            )
            with session_scope() as db:
                upload = db.execute(
                    select(Upload).where(Upload.segment_id == seg_id)
                ).scalar_one_or_none()
                if upload is None:
                    upload = Upload(segment_id=seg_id)
                    db.add(upload)
                upload.youtube_title = data["title"]
                upload.youtube_description = data["description"]
                upload.tags = data["tags"]
                upload.status = UploadStatus.PENDING
                db.get(Segment, seg_id).status = SegmentStatus.METADATA_READY
                db.commit()
        except Exception as exc:
            with session_scope() as db:
                seg = db.get(Segment, seg_id)
                seg.error = (seg.error or "") + f" metadata: {exc};"
                db.commit()

    publish_progress(job_id, Stage.METADATA, "done")
    return job_id
```

- [ ] **Step 2: Commit**

```bash
git add worker/worker/tasks/metadata.py
git commit -m "feat(worker): real metadata stage — llm generates youtube title/description/tags as draft in uploads"
```

---

## Task 14: API segments router + schemas

**Files:**
- Modify: `api/app/schemas.py`
- Create: `api/app/routers/segments.py`
- Create: `api/app/routers/assets.py`
- Modify: `api/app/main.py`
- Create: `api/tests/test_segments_router.py`

- [ ] **Step 1: Расширить `api/app/schemas.py`**

В конец файла добавить:

```python
class ThumbnailOut(BaseModel):
    asset_id: uuid.UUID
    position_idx: int
    url: str

    model_config = {"from_attributes": True}


class SegmentOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    index: int
    start_sec: float
    end_sec: float
    title: str | None
    summary: str | None
    yt_title: str | None
    yt_description: str | None
    yt_tags: list[str] | None
    selected_thumbnail_id: uuid.UUID | None
    thumbnails: list[ThumbnailOut]
    video_download_url: str | None
    status: str

    model_config = {"from_attributes": True}


class SegmentsList(BaseModel):
    items: list[SegmentOut]


class SegmentPatch(BaseModel):
    yt_title: str | None = None
    yt_description: str | None = None
    yt_tags: list[str] | None = None
    selected_thumbnail_id: uuid.UUID | None = None
```

- [ ] **Step 2: TDD — написать тест `api/tests/test_segments_router.py`**

```python
from __future__ import annotations

import uuid

import pytest


def _seed_segment_with_thumbs(session, job_id):
    from app.models import (
        Asset, AssetKind, Job, JobStatus, Segment, SegmentStatus,
        SourceType, Upload, UploadStatus,
    )
    from shared.stages import Stage

    job = Job(
        id=job_id,
        source_type=SourceType.URL,
        source_url="https://x",
        status=JobStatus.SUCCEEDED,
        current_stage=Stage.DONE,
        created_by="t@t",
    )
    session.add(job)
    seg = Segment(
        job_id=job_id, index=0, start_sec=0.0, end_sec=300.0,
        title="t", summary="s", status=SegmentStatus.METADATA_READY,
    )
    session.add(seg)
    session.flush()
    thumb_asset = Asset(
        job_id=job_id, kind=AssetKind.THUMBNAIL,
        s3_key=f"{job_id}/thumbnails/{seg.id}/1.jpg",
        mime="image/jpeg", size_bytes=10,
        segment_id=seg.id, position_idx=1,
    )
    session.add(thumb_asset)
    session.flush()
    seg.selected_thumbnail_id = thumb_asset.id
    upload = Upload(
        segment_id=seg.id,
        youtube_title="title v1",
        youtube_description="desc v1",
        tags=["a", "b"],
        status=UploadStatus.PENDING,
    )
    session.add(upload)
    session.commit()
    return seg.id, thumb_asset.id


def test_list_segments_for_job(client, db_session):
    job_id = uuid.uuid4()
    seg_id, thumb_id = _seed_segment_with_thumbs(db_session, job_id)

    r = client.get(f"/jobs/{job_id}/segments")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == str(seg_id)
    assert body["items"][0]["yt_title"] == "title v1"
    assert body["items"][0]["thumbnails"][0]["position_idx"] == 1


def test_patch_segment_updates_metadata(client, db_session):
    from app.models import Segment, Upload
    from sqlalchemy import select

    job_id = uuid.uuid4()
    seg_id, _ = _seed_segment_with_thumbs(db_session, job_id)

    r = client.patch(
        f"/segments/{seg_id}",
        json={"yt_title": "new title", "yt_tags": ["x", "y"]},
    )
    assert r.status_code == 200

    db_session.expire_all()
    upload = db_session.execute(select(Upload).where(Upload.segment_id == seg_id)).scalar_one()
    assert upload.youtube_title == "new title"
    assert upload.tags == ["x", "y"]


def test_patch_segment_changes_thumbnail_selection(client, db_session):
    from app.models import Asset, AssetKind, Segment
    from sqlalchemy import select

    job_id = uuid.uuid4()
    seg_id, _ = _seed_segment_with_thumbs(db_session, job_id)

    new_thumb = Asset(
        job_id=job_id, kind=AssetKind.THUMBNAIL,
        s3_key=f"{job_id}/thumbnails/{seg_id}/2.jpg",
        mime="image/jpeg", size_bytes=10,
        segment_id=seg_id, position_idx=2,
    )
    db_session.add(new_thumb)
    db_session.commit()

    r = client.patch(
        f"/segments/{seg_id}",
        json={"selected_thumbnail_id": str(new_thumb.id)},
    )
    assert r.status_code == 200

    db_session.expire_all()
    seg = db_session.get(Segment, seg_id)
    assert seg.selected_thumbnail_id == new_thumb.id


def test_get_segment_download_redirects_to_presigned(client, db_session, monkeypatch):
    from app.models import Asset, AssetKind

    job_id = uuid.uuid4()
    seg_id, _ = _seed_segment_with_thumbs(db_session, job_id)

    seg_video = Asset(
        job_id=job_id, kind=AssetKind.SEGMENT_VIDEO,
        s3_key=f"{job_id}/segments/{seg_id}.mp4",
        mime="video/mp4", size_bytes=1000,
        segment_id=seg_id,
    )
    db_session.add(seg_video)
    db_session.commit()

    from app.services import storage as api_storage
    monkeypatch.setattr(
        api_storage, "presigned_get_url",
        lambda key, expires_in=3600: f"http://minio.local/{key}?sig=x",
    )

    r = client.get(f"/segments/{seg_id}/download", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].startswith("http://minio.local/")
```

- [ ] **Step 3: Запустить тест, убедиться что падает (роутера ещё нет)**

Run:
```bash
docker compose run --rm api pytest tests/test_segments_router.py -v
```
Expected: FAIL — 404 на всех эндпоинтах.

- [ ] **Step 4: Создать `api/app/routers/segments.py`**

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Asset, AssetKind, Segment, Upload
from app.schemas import SegmentOut, SegmentPatch, SegmentsList, ThumbnailOut
from app.services import storage

router = APIRouter()


def _segment_to_out(seg: Segment, db: Session) -> SegmentOut:
    thumbs = db.execute(
        select(Asset).where(
            Asset.segment_id == seg.id,
            Asset.kind == AssetKind.THUMBNAIL,
        ).order_by(Asset.position_idx)
    ).scalars().all()

    upload = db.execute(
        select(Upload).where(Upload.segment_id == seg.id)
    ).scalar_one_or_none()

    seg_video = db.execute(
        select(Asset).where(
            Asset.segment_id == seg.id,
            Asset.kind == AssetKind.SEGMENT_VIDEO,
        )
    ).scalar_one_or_none()

    return SegmentOut(
        id=seg.id,
        job_id=seg.job_id,
        index=seg.index,
        start_sec=seg.start_sec,
        end_sec=seg.end_sec,
        title=seg.title,
        summary=seg.summary,
        yt_title=upload.youtube_title if upload else None,
        yt_description=upload.youtube_description if upload else None,
        yt_tags=list(upload.tags) if upload and upload.tags else None,
        selected_thumbnail_id=seg.selected_thumbnail_id,
        thumbnails=[
            ThumbnailOut(
                asset_id=a.id,
                position_idx=a.position_idx or 0,
                url=storage.presigned_get_url(a.s3_key),
            )
            for a in thumbs
        ],
        video_download_url=(
            f"/segments/{seg.id}/download" if seg_video else None
        ),
        status=seg.status.value,
    )


@router.get("/jobs/{job_id}/segments", response_model=SegmentsList)
def list_segments(job_id: uuid.UUID, db: Session = Depends(get_session)) -> SegmentsList:
    segments = db.execute(
        select(Segment).where(Segment.job_id == job_id).order_by(Segment.index)
    ).scalars().all()
    return SegmentsList(items=[_segment_to_out(s, db) for s in segments])


@router.get("/segments/{segment_id}", response_model=SegmentOut)
def get_segment(segment_id: uuid.UUID, db: Session = Depends(get_session)) -> SegmentOut:
    seg = db.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(status_code=404, detail="segment not found")
    return _segment_to_out(seg, db)


@router.patch("/segments/{segment_id}", response_model=SegmentOut)
def patch_segment(
    segment_id: uuid.UUID,
    body: SegmentPatch,
    db: Session = Depends(get_session),
) -> SegmentOut:
    seg = db.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(status_code=404, detail="segment not found")

    if body.selected_thumbnail_id is not None:
        thumb = db.get(Asset, body.selected_thumbnail_id)
        if thumb is None or thumb.segment_id != seg.id or thumb.kind != AssetKind.THUMBNAIL:
            raise HTTPException(status_code=400, detail="invalid thumbnail")
        seg.selected_thumbnail_id = thumb.id

    if body.yt_title is not None or body.yt_description is not None or body.yt_tags is not None:
        upload = db.execute(
            select(Upload).where(Upload.segment_id == seg.id)
        ).scalar_one_or_none()
        if upload is None:
            upload = Upload(segment_id=seg.id)
            db.add(upload)
        if body.yt_title is not None:
            upload.youtube_title = body.yt_title
        if body.yt_description is not None:
            upload.youtube_description = body.yt_description
        if body.yt_tags is not None:
            upload.tags = body.yt_tags

    db.commit()
    db.refresh(seg)
    return _segment_to_out(seg, db)


@router.get("/segments/{segment_id}/download")
def download_segment(segment_id: uuid.UUID, db: Session = Depends(get_session)) -> Response:
    asset = db.execute(
        select(Asset).where(
            Asset.segment_id == segment_id,
            Asset.kind == AssetKind.SEGMENT_VIDEO,
        )
    ).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="segment video not ready")
    url = storage.presigned_get_url(asset.s3_key)
    return RedirectResponse(url=url, status_code=302)
```

- [ ] **Step 5: Создать `api/app/routers/assets.py`**

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Asset
from app.services import storage

router = APIRouter()


@router.get("/assets/{asset_id}/url")
def asset_url(asset_id: uuid.UUID, db: Session = Depends(get_session)) -> dict[str, str]:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    return {"url": storage.presigned_get_url(asset.s3_key)}
```

- [ ] **Step 6: Подключить роутеры в `api/app/main.py`**

Заменить содержимое:

```python
from fastapi import FastAPI

from app.routers import assets, jobs, segments

app = FastAPI(title="Video Slicer API")
app.include_router(jobs.router)
app.include_router(segments.router)
app.include_router(assets.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 7: Запустить тесты и убедиться что зелёные**

Run:
```bash
docker compose run --rm api pytest tests/test_segments_router.py -v
```
Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add api/app/schemas.py api/app/routers/segments.py api/app/routers/assets.py api/app/main.py api/tests/test_segments_router.py
git commit -m "feat(api): segments and assets routers with list/get/patch/download endpoints"
```

---

## Task 15: Web UI — список сегментов с редактором

**Files:**
- Modify: `web/lib/api.ts`
- Modify: `web/app/jobs/[id]/page.tsx`
- Create: `web/components/SegmentList.tsx`
- Create: `web/components/SegmentCard.tsx`
- Create: `web/components/ThumbnailPicker.tsx`
- Create: `web/components/MetadataEditor.tsx`

- [ ] **Step 1: Расширить `web/lib/api.ts`**

В конец файла добавить:

```ts
export interface Thumbnail {
  asset_id: string;
  position_idx: number;
  url: string;
}

export interface Segment {
  id: string;
  job_id: string;
  index: number;
  start_sec: number;
  end_sec: number;
  title: string | null;
  summary: string | null;
  yt_title: string | null;
  yt_description: string | null;
  yt_tags: string[] | null;
  selected_thumbnail_id: string | null;
  thumbnails: Thumbnail[];
  video_download_url: string | null;
  status: string;
}

export interface SegmentsList {
  items: Segment[];
}

export interface SegmentPatch {
  yt_title?: string;
  yt_description?: string;
  yt_tags?: string[];
  selected_thumbnail_id?: string;
}

export async function listSegments(jobId: string): Promise<SegmentsList> {
  const r = await fetch(`${BASE}/jobs/${jobId}/segments`, { cache: "no-store" });
  if (!r.ok) throw new Error(`listSegments ${r.status}`);
  return r.json();
}

export async function patchSegment(id: string, body: SegmentPatch): Promise<Segment> {
  const r = await fetch(`${BASE}/segments/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`patchSegment ${r.status}`);
  return r.json();
}
```

- [ ] **Step 2: Создать `web/components/ThumbnailPicker.tsx`**

```tsx
"use client";

import Image from "next/image";
import { useState } from "react";

import { Thumbnail, patchSegment } from "@/lib/api";

export function ThumbnailPicker({
  segmentId,
  thumbnails,
  initialSelected,
}: {
  segmentId: string;
  thumbnails: Thumbnail[];
  initialSelected: string | null;
}) {
  const [selected, setSelected] = useState(initialSelected);

  async function pick(assetId: string) {
    setSelected(assetId);
    try {
      await patchSegment(segmentId, { selected_thumbnail_id: assetId });
    } catch (e) {
      console.error(e);
    }
  }

  return (
    <div className="flex gap-2">
      {thumbnails.map((t) => (
        <button
          key={t.asset_id}
          type="button"
          onClick={() => pick(t.asset_id)}
          className={`relative h-20 w-32 overflow-hidden rounded border-2 ${
            selected === t.asset_id ? "border-black" : "border-transparent opacity-60"
          }`}
        >
          <img src={t.url} alt={`thumb ${t.position_idx}`} className="h-full w-full object-cover" />
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Создать `web/components/MetadataEditor.tsx`**

```tsx
"use client";

import { useEffect, useRef, useState } from "react";

import { patchSegment } from "@/lib/api";

interface Props {
  segmentId: string;
  initialTitle: string;
  initialDescription: string;
  initialTags: string[];
}

export function MetadataEditor({ segmentId, initialTitle, initialDescription, initialTags }: Props) {
  const [title, setTitle] = useState(initialTitle);
  const [description, setDescription] = useState(initialDescription);
  const [tagsInput, setTagsInput] = useState(initialTags.join(", "));
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const tags = tagsInput.split(",").map((t) => t.trim()).filter(Boolean);
      patchSegment(segmentId, {
        yt_title: title,
        yt_description: description,
        yt_tags: tags,
      }).catch(console.error);
    }, 1000);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [segmentId, title, description, tagsInput]);

  return (
    <div className="space-y-2">
      <input
        type="text"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Заголовок (≤60 символов)"
        maxLength={60}
        className="w-full rounded border px-3 py-2 text-sm"
      />
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Описание"
        rows={6}
        className="w-full rounded border px-3 py-2 text-sm"
      />
      <input
        type="text"
        value={tagsInput}
        onChange={(e) => setTagsInput(e.target.value)}
        placeholder="Теги через запятую"
        className="w-full rounded border px-3 py-2 text-sm"
      />
    </div>
  );
}
```

- [ ] **Step 4: Создать `web/components/SegmentCard.tsx`**

```tsx
import { Segment } from "@/lib/api";

import { MetadataEditor } from "./MetadataEditor";
import { ThumbnailPicker } from "./ThumbnailPicker";

function fmt(sec: number): string {
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  return `${m}:${String(ss).padStart(2, "0")}`;
}

export function SegmentCard({ segment }: { segment: Segment }) {
  return (
    <article className="space-y-3 rounded border bg-white p-4">
      <header className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">
          Сегмент {segment.index + 1} · {fmt(segment.start_sec)}–{fmt(segment.end_sec)}
        </h2>
        <span className="rounded bg-neutral-100 px-2 py-1 text-xs">{segment.status}</span>
      </header>

      {segment.thumbnails.length > 0 && (
        <ThumbnailPicker
          segmentId={segment.id}
          thumbnails={segment.thumbnails}
          initialSelected={segment.selected_thumbnail_id}
        />
      )}

      <MetadataEditor
        segmentId={segment.id}
        initialTitle={segment.yt_title ?? segment.title ?? ""}
        initialDescription={segment.yt_description ?? segment.summary ?? ""}
        initialTags={segment.yt_tags ?? []}
      />

      {segment.video_download_url && (
        <a
          href={segment.video_download_url}
          className="inline-block rounded bg-black px-4 py-2 text-sm text-white"
        >
          Скачать клип
        </a>
      )}
    </article>
  );
}
```

- [ ] **Step 5: Создать `web/components/SegmentList.tsx`**

```tsx
import { listSegments } from "@/lib/api";

import { SegmentCard } from "./SegmentCard";

export async function SegmentList({ jobId }: { jobId: string }) {
  const { items } = await listSegments(jobId);
  if (items.length === 0) {
    return <p className="text-sm text-neutral-500">Сегменты ещё не созданы.</p>;
  }
  return (
    <section className="space-y-4">
      <h2 className="text-xl font-semibold">Сегменты</h2>
      <div className="space-y-4">
        {items.map((s) => (
          <SegmentCard key={s.id} segment={s} />
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 6: Обновить `web/app/jobs/[id]/page.tsx`**

Заменить файл целиком:

```tsx
import { JobProgress } from "@/components/JobProgress";
import { SegmentList } from "@/components/SegmentList";
import { getJob } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function JobDetailsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const job = await getJob(id);
  return (
    <main className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Задача {id.slice(0, 8)}</h1>
        <p className="text-sm text-neutral-500">{job.source_url ?? "файл"}</p>
      </header>
      <JobProgress initial={job} />
      {job.status === "succeeded" && <SegmentList jobId={id} />}
    </main>
  );
}
```

- [ ] **Step 7: Пересобрать веб и проверить страницу**

Run:
```bash
docker compose up -d --build web
curl -sS -I http://localhost:3000
```
Expected: HTTP 200 (страница со списком пуста, потому что Phase 2 джобов ещё нет — норм для этой задачи).

- [ ] **Step 8: Commit**

```bash
git add web/lib/api.ts web/app/jobs/ web/components/SegmentList.tsx web/components/SegmentCard.tsx web/components/ThumbnailPicker.tsx web/components/MetadataEditor.tsx
git commit -m "feat(web): segment list with thumbnail picker, metadata editor and download link"
```

---

## Task 16: E2E smoke + тег `phase-2-pipeline`

**Files:** —

- [ ] **Step 1: Поднять всё с нуля, прогнать миграции**

Run:
```bash
docker compose down -v
docker compose up -d --build
sleep 10
docker compose run --rm api alembic upgrade head
```
Expected: `0001_initial -> 0002_phase2` применилась.

- [ ] **Step 2: Прогнать все юнит-тесты**

Run:
```bash
docker compose run --rm worker pytest tests/ -v
docker compose run --rm api pytest tests/ -v
```
Expected: всё зелёное.

- [ ] **Step 3: Создать задачу на тестовом видео через UI или curl**

```bash
curl -sS -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -H "X-User: smoke@local" \
  -d '{"source_type":"url","source_url":"https://www.youtube.com/watch?v=7MaCttnXc4g"}'
```
Сохранить `id` из ответа.

- [ ] **Step 4: Дождаться завершения пайплайна**

```bash
JOB_ID=<id>
while :; do
  STATUS=$(curl -sS http://localhost:8000/jobs/$JOB_ID | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
  STAGE=$(curl -sS http://localhost:8000/jobs/$JOB_ID | python3 -c "import sys,json;print(json.load(sys.stdin)['current_stage'])")
  echo "$STATUS / $STAGE"
  [ "$STATUS" = "succeeded" ] && break
  [ "$STATUS" = "failed" ] && { echo "FAILED"; curl -sS http://localhost:8000/jobs/$JOB_ID; exit 1; }
  sleep 10
done
```
Expected: через ~5–15 минут (yt-dlp + LLM-вызовы) увидеть `succeeded / done`.

- [ ] **Step 5: Проверить сегменты**

```bash
curl -sS http://localhost:8000/jobs/$JOB_ID/segments | python3 -m json.tool | head -100
```
Expected: ≥3 сегмента, у каждого 3 thumbnails, заполненные `yt_title` и `yt_description`.

- [ ] **Step 6: Открыть `http://localhost:3000/jobs/$JOB_ID` и убедиться что UI работает**

Проверить: сегменты рендерятся, можно поменять выбранный thumbnail, отредактировать заголовок (через 1 сек PATCH улетает), скачать клип по ссылке.

- [ ] **Step 7: Тегнуть веху**

```bash
git tag phase-2-pipeline
git log --oneline -1
```

---

## Self-Review

**Spec coverage:**
- § 3.1 fetch — Task 8.
- § 3.2 transcribe — Task 9 (VTT путь и Whisper fallback).
- § 3.3 segment — Task 10 (LLM + валидация + 1 retry).
- § 3.4 cut — Task 11.
- § 3.5 thumbnail — Task 12 (3 кадра + default selected = середина).
- § 3.6 metadata — Task 13 (заполнение `Upload`).
- § 4 миграция — Task 2 (расширение enum + 2 новых поля).
- § 5 API endpoints — Task 14 (segments router + assets router).
- § 6 UI — Task 15 (4 новых компонента + обновление страницы).
- § 7 зависимости/Docker — Task 1.
- § 8 промпты — Task 4 (модули `prompts/segment.py`, `prompts/metadata.py`).
- § 9 обработка ошибок — встроена в каждую стадию (`Job.failed` или per-segment soft fail).
- § 10 тесты — юниты в каждой соответствующей задаче, E2E — Task 16.

**Placeholder scan:** все шаги содержат полный код или конкретные команды; нет TBD/TODO/"similar to".

**Type consistency:**
- `AssetKind` определён одинаково в `api/app/models.py` и `worker/worker/models.py` (Task 2).
- `SegmentOut.status` сериализует enum как строку (`seg.status.value`); фронт ожидает `string` в `Segment.status` — совпадает.
- Имена эндпоинтов `/jobs/{id}/segments`, `/segments/{id}`, `/segments/{id}/download`, `/assets/{id}/url` совпадают между API кодом (Task 14), TS-клиентом (Task 15) и спекой (§ 5).
- Поля `yt_title/yt_description/yt_tags` в `SegmentOut` (API) и `Segment` интерфейсе (TS) совпадают.
- `selected_thumbnail_id` — UUID на бэке, string на фронте; PATCH принимает string и парсит как UUID — совпадает.
