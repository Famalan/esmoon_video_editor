# Video Slicer — Phase 1: Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Запустить инфраструктурный каркас (docker-compose, FastAPI, Celery, Postgres, MinIO, Redis, Next.js), модель данных и end-to-end заглушку Job, которая проходит цепочку пустых стадий пайплайна с прогрессом, видимым в UI.

**Architecture:** Линейный Celery-пайплайн. FastAPI принимает Job, ставит Celery `chain` из 7 заглушечных стадий. Каждая стадия только обновляет `Job.current_stage` и публикует прогресс в Redis pub/sub. Next.js поллит `GET /jobs/{id}` каждые 1.5 сек и показывает прогресс.

**Tech Stack:** Python 3.12, FastAPI, Celery 5, SQLAlchemy 2 + Alembic, Pydantic v2, pytest + testcontainers, ffmpeg (Docker image), Redis, Postgres 16, MinIO, Next.js 15 (App Router) + TypeScript + Tailwind.

---

## File Structure

```
esmoon_video_editor/
├── docker-compose.yml                  # Postgres, Redis, MinIO, api, worker, web
├── .env.example
├── api/
│   ├── pyproject.toml                  # Poetry, общие deps с worker через path-ref
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   │       └── 0001_initial.py         # Job, Asset, Segment, Upload, YouTubeAccount
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                     # FastAPI app, /health
│   │   ├── config.py                   # Settings из env (Pydantic Settings)
│   │   ├── db.py                       # SQLAlchemy engine/session
│   │   ├── models.py                   # SQLAlchemy ORM модели
│   │   ├── schemas.py                  # Pydantic схемы для API
│   │   ├── routers/
│   │   │   └── jobs.py                 # POST/GET /jobs, GET /jobs/{id}
│   │   └── celery_client.py            # send_task в worker
│   └── tests/
│       ├── conftest.py                 # фикстуры db, client, celery (eager mode)
│       ├── test_health.py
│       └── test_jobs.py
├── worker/
│   ├── pyproject.toml
│   ├── Dockerfile                      # base: python+ffmpeg
│   ├── worker/
│   │   ├── __init__.py
│   │   ├── celery_app.py               # Celery с очередью default
│   │   ├── db.py                       # та же модель, что в api/
│   │   ├── models.py
│   │   ├── progress.py                 # publish_progress(job_id, stage) → Redis pub/sub
│   │   └── tasks/
│   │       ├── __init__.py
│   │       ├── chain.py                # build_pipeline(job_id) → Celery chain
│   │       ├── fetch.py
│   │       ├── transcribe.py
│   │       ├── segment.py
│   │       ├── cut.py
│   │       ├── thumbnail.py
│   │       ├── metadata.py
│   │       └── upload.py
│   └── tests/
│       ├── conftest.py
│       └── test_chain_progress.py
├── shared/                             # путь добавляется как зависимость в обоих pyproject
│   ├── pyproject.toml
│   └── shared/
│       ├── __init__.py
│       └── stages.py                   # enum Stage (общий для api и worker)
└── web/
    ├── package.json
    ├── next.config.ts
    ├── tsconfig.json
    ├── tailwind.config.ts
    ├── app/
    │   ├── layout.tsx
    │   ├── page.tsx                    # список задач
    │   ├── jobs/
    │   │   ├── new/page.tsx            # форма создания
    │   │   └── [id]/page.tsx           # детали + поллинг прогресса
    │   └── api/[...path]/route.ts      # прокси к FastAPI (CORS-free dev)
    ├── lib/
    │   └── api.ts                      # клиент REST
    └── components/
        ├── JobList.tsx
        ├── JobProgress.tsx
        └── NewJobForm.tsx
```

Каждый файл с одной чёткой ответственностью. `shared/` — единственное место, где живут enum'ы, которые нужны и API, и воркеру, чтобы избежать дублирования.

---

## Task 1: docker-compose с инфраструктурой

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`

- [ ] **Step 1: Создать `.env.example`**

```bash
# Postgres
POSTGRES_USER=videoslicer
POSTGRES_PASSWORD=videoslicer
POSTGRES_DB=videoslicer
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# Redis
REDIS_URL=redis://redis:6379/0

# MinIO
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin
MINIO_ENDPOINT=http://minio:9000
MINIO_BUCKET=video-slicer

# API
API_PORT=8000

# Web
WEB_PORT=3000
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

- [ ] **Step 2: Создать `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "5432:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 5s
      timeout: 3s
      retries: 10

  api:
    build:
      context: .
      dockerfile: api/Dockerfile
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
    ports:
      - "${API_PORT}:8000"
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
    volumes:
      - ./api:/app
      - ./shared:/shared

  worker:
    build:
      context: .
      dockerfile: worker/Dockerfile
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
    command: ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info", "--concurrency=2"]
    volumes:
      - ./worker:/app
      - ./shared:/shared

  web:
    build:
      context: ./web
    env_file: .env
    depends_on:
      - api
    ports:
      - "${WEB_PORT}:3000"
    command: ["npm", "run", "dev"]
    volumes:
      - ./web:/app
      - /app/node_modules

volumes:
  pg_data:
  minio_data:
```

- [ ] **Step 3: Скопировать env и проверить, что инфраструктурные сервисы поднимаются**

Run:
```bash
cp .env.example .env
docker compose up -d postgres redis minio
docker compose ps
```
Expected: три сервиса в статусе `healthy`.

- [ ] **Step 4: Остановить инфраструктуру**

Run: `docker compose down`
Expected: команды завершаются без ошибок (api/worker/web ещё не созданы).

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "chore: scaffold docker-compose with postgres, redis, minio"
```

---

## Task 2: Shared-пакет с общим enum стадий

**Files:**
- Create: `shared/pyproject.toml`
- Create: `shared/shared/__init__.py` (пустой)
- Create: `shared/shared/stages.py`

- [ ] **Step 1: Создать `shared/pyproject.toml`**

```toml
[tool.poetry]
name = "video-slicer-shared"
version = "0.1.0"
description = "Shared types and contracts"
authors = ["team"]
packages = [{ include = "shared" }]

[tool.poetry.dependencies]
python = "^3.12"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

- [ ] **Step 2: Создать `shared/shared/__init__.py`** — пустой файл.

- [ ] **Step 3: Создать `shared/shared/stages.py`**

```python
from enum import StrEnum


class Stage(StrEnum):
    FETCH = "fetch"
    TRANSCRIBE = "transcribe"
    SEGMENT = "segment"
    CUT = "cut"
    THUMBNAIL = "thumbnail"
    METADATA = "metadata"
    UPLOAD = "upload"
    DONE = "done"


PIPELINE_ORDER: list[Stage] = [
    Stage.FETCH,
    Stage.TRANSCRIBE,
    Stage.SEGMENT,
    Stage.CUT,
    Stage.THUMBNAIL,
    Stage.METADATA,
    Stage.UPLOAD,
]
```

- [ ] **Step 4: Commit**

```bash
git add shared/
git commit -m "feat(shared): add Stage enum and pipeline order"
```

---

## Task 3: API-пакет — pyproject, Dockerfile, скелет FastAPI с /health

**Files:**
- Create: `api/pyproject.toml`
- Create: `api/Dockerfile`
- Create: `api/app/__init__.py` (пустой)
- Create: `api/app/main.py`
- Create: `api/app/config.py`
- Create: `api/tests/__init__.py` (пустой)
- Create: `api/tests/conftest.py`
- Create: `api/tests/test_health.py`

- [ ] **Step 1: Создать `api/pyproject.toml`**

```toml
[tool.poetry]
name = "video-slicer-api"
version = "0.1.0"
authors = ["team"]
packages = [{ include = "app" }]

[tool.poetry.dependencies]
python = "^3.12"
fastapi = "^0.115"
uvicorn = { version = "^0.32", extras = ["standard"] }
sqlalchemy = "^2.0"
psycopg = { version = "^3.2", extras = ["binary"] }
alembic = "^1.13"
pydantic = "^2.9"
pydantic-settings = "^2.6"
celery = { version = "^5.4", extras = ["redis"] }
redis = "^5.2"
video-slicer-shared = { path = "../shared", develop = true }

[tool.poetry.group.dev.dependencies]
pytest = "^8"
pytest-asyncio = "^0.24"
httpx = "^0.27"
testcontainers = { version = "^4.8", extras = ["postgres", "redis"] }

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

- [ ] **Step 2: Создать `api/Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir poetry==1.8.4 && poetry config virtualenvs.create false
COPY api/pyproject.toml /app/
COPY shared /shared
RUN poetry install --no-root --no-interaction
COPY api /app
ENV PYTHONPATH=/app:/shared
EXPOSE 8000
```

- [ ] **Step 3: Создать `api/app/config.py`**

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_url: str = "redis://redis:6379/0"

    minio_endpoint: str
    minio_root_user: str
    minio_root_password: str
    minio_bucket: str

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
```

- [ ] **Step 4: Создать `api/app/main.py`**

```python
from fastapi import FastAPI

app = FastAPI(title="Video Slicer API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 5: Создать `api/tests/conftest.py`**

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
```

- [ ] **Step 6: Создать `api/tests/test_health.py`**

```python
def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 7: Запустить тест и убедиться, что он проходит**

Run (внутри `api/`): `poetry install && poetry run pytest tests/test_health.py -v`
Expected: 1 passed.

- [ ] **Step 8: Поднять api в docker-compose и проверить endpoint**

Run:
```bash
docker compose up -d --build api
curl -s http://localhost:8000/health
```
Expected: `{"status":"ok"}`

- [ ] **Step 9: Commit**

```bash
git add api/
git commit -m "feat(api): scaffold FastAPI service with /health"
```

---

## Task 4: SQLAlchemy-модели

**Files:**
- Create: `api/app/db.py`
- Create: `api/app/models.py`

- [ ] **Step 1: Создать `api/app/db.py`**

```python
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 2: Создать `api/app/models.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ARRAY, BigInteger, DateTime, Enum, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from shared.stages import Stage


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SourceType(StrEnum):
    FILE = "file"
    URL = "url"


class AssetKind(StrEnum):
    SOURCE = "source"
    TRANSCRIPT = "transcript"
    SEGMENT = "segment"
    THUMBNAIL = "thumbnail"


class SegmentStatus(StrEnum):
    PENDING = "pending"
    CUT = "cut"
    THUMBNAIL_READY = "thumbnail_ready"
    METADATA_READY = "metadata_ready"
    UPLOADED = "uploaded"
    FAILED = "failed"


class UploadStatus(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType, name="source_type"))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="job_status"), default=JobStatus.QUEUED)
    current_stage: Mapped[Stage] = mapped_column(Enum(Stage, name="stage"), default=Stage.FETCH)
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    segments: Mapped[list[Segment]] = relationship(back_populates="job", cascade="all, delete-orphan")
    assets: Mapped[list[Asset]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    kind: Mapped[AssetKind] = mapped_column(Enum(AssetKind, name="asset_kind"))
    s3_key: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(127))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=True
    )

    job: Mapped[Job] = relationship(back_populates="assets")


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    index: Mapped[int] = mapped_column(Integer)
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SegmentStatus] = mapped_column(Enum(SegmentStatus, name="segment_status"), default=SegmentStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[Job] = relationship(back_populates="segments")
    upload: Mapped[Upload | None] = relationship(back_populates="segment", uselist=False, cascade="all, delete-orphan")


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    segment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"), unique=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    youtube_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    youtube_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    youtube_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    status: Mapped[UploadStatus] = mapped_column(Enum(UploadStatus, name="upload_status"), default=UploadStatus.PENDING)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    segment: Mapped[Segment] = relationship(back_populates="upload")


class YouTubeAccount(Base):
    __tablename__ = "youtube_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    channel_id: Mapped[str] = mapped_column(String(255), unique=True)
    refresh_token: Mapped[bytes] = mapped_column(LargeBinary)  # encrypted at rest
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
```

- [ ] **Step 3: Commit**

```bash
git add api/app/db.py api/app/models.py
git commit -m "feat(api): add SQLAlchemy models for Job/Asset/Segment/Upload/YouTubeAccount"
```

---

## Task 5: Alembic с начальной миграцией

**Files:**
- Create: `api/alembic.ini`
- Create: `api/alembic/env.py`
- Create: `api/alembic/script.py.mako` (стандартный)
- Create: `api/alembic/versions/0001_initial.py`

- [ ] **Step 1: Создать `api/alembic.ini`**

```ini
[alembic]
script_location = alembic
prepend_sys_path = .

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 2: Создать `api/alembic/env.py`**

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.db import Base
from app import models  # noqa: F401 — ensure tables register

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Создать `api/alembic/script.py.mako`**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Сгенерировать миграцию `0001_initial`**

Run (внутри docker compose, чтобы Alembic видел поднятый Postgres):
```bash
docker compose run --rm api alembic revision --autogenerate -m "initial"
```
Expected: создан файл `api/alembic/versions/0001_<hash>_initial.py`. Переименовать в `0001_initial.py`. Проверить, что в нём есть `op.create_table("jobs", ...)`, segments, uploads, assets, youtube_accounts и все enum-типы.

- [ ] **Step 5: Применить миграцию**

Run:
```bash
docker compose run --rm api alembic upgrade head
```
Expected: `INFO  [alembic.runtime.migration] Running upgrade  -> 0001, initial`.

- [ ] **Step 6: Проверить таблицы в Postgres**

Run:
```bash
docker compose exec postgres psql -U videoslicer -d videoslicer -c "\dt"
```
Expected: список из `alembic_version`, `assets`, `jobs`, `segments`, `uploads`, `youtube_accounts`.

- [ ] **Step 7: Commit**

```bash
git add api/alembic.ini api/alembic/
git commit -m "feat(api): add alembic with initial migration"
```

---

## Task 6: Pydantic-схемы и роутер `/jobs` — POST с валидацией

**Files:**
- Create: `api/app/schemas.py`
- Create: `api/app/routers/__init__.py` (пустой)
- Create: `api/app/routers/jobs.py`
- Modify: `api/app/main.py`
- Create: `api/tests/test_jobs.py`

- [ ] **Step 1: Написать падающий тест в `api/tests/test_jobs.py`**

```python
import io

import pytest


def test_create_job_with_url_returns_201(client):
    response = client.post(
        "/jobs",
        json={"source_type": "url", "source_url": "https://youtube.com/watch?v=abc"},
        headers={"X-User": "alice@example.com"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["current_stage"] == "fetch"
    assert body["source_url"] == "https://youtube.com/watch?v=abc"
    assert body["created_by"] == "alice@example.com"
    assert "id" in body


def test_create_job_with_url_requires_source_url(client):
    response = client.post(
        "/jobs",
        json={"source_type": "url"},
        headers={"X-User": "alice@example.com"},
    )
    assert response.status_code == 422


def test_create_job_with_file(client):
    files = {"file": ("video.mp4", io.BytesIO(b"FAKEMP4"), "video/mp4")}
    response = client.post(
        "/jobs",
        data={"source_type": "file"},
        files=files,
        headers={"X-User": "alice@example.com"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["source_type"] == "file"
    assert body["source_url"] is None
```

- [ ] **Step 2: Запустить тест — должен упасть**

Run: `docker compose run --rm api pytest tests/test_jobs.py -v`
Expected: FAIL (роутер не существует).

- [ ] **Step 3: Создать `api/app/schemas.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import JobStatus, SourceType
from shared.stages import Stage


class JobCreate(BaseModel):
    source_type: SourceType
    source_url: str | None = None


class JobOut(BaseModel):
    id: uuid.UUID
    source_type: SourceType
    source_url: str | None
    status: JobStatus
    current_stage: Stage
    created_by: str
    created_at: datetime
    error: str | None

    model_config = {"from_attributes": True}


class JobsList(BaseModel):
    items: list[JobOut]
```

- [ ] **Step 4: Создать `api/app/celery_client.py`** (заглушка, пока без вызова)

```python
from celery import Celery

from app.config import settings

celery = Celery("video_slicer", broker=settings.redis_url, backend=settings.redis_url)


def enqueue_pipeline(job_id: str) -> None:
    """Кладёт цепочку пайплайна в очередь. Реализация в worker.tasks.chain."""
    celery.send_task("worker.tasks.chain.build_pipeline", args=[job_id])
```

- [ ] **Step 5: Создать `api/app/routers/jobs.py`**

```python
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.celery_client import enqueue_pipeline
from app.db import get_session
from app.models import Job, JobStatus, SourceType
from app.schemas import JobOut
from shared.stages import Stage

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobOut, status_code=201)
def create_job(
    source_type: Annotated[SourceType, Form()] = None,  # type: ignore[assignment]
    source_url: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
    user: Annotated[str, Header(alias="X-User")] = "anonymous",
    db: Session = Depends(get_session),
) -> JobOut:
    # JSON path: source_type приходит из тела как form, либо JSON — поддержим оба
    raise HTTPException(status_code=500, detail="dispatcher placeholder")
```

> Pydantic+FastAPI поддерживает `application/json` через тип `JobCreate` и `multipart/form-data` через `Form`/`File`. Чтобы не плодить два пути, выделяем JSON-вариант отдельным эндпоинтом, а multipart — через `Form`. Перепишем шаг ниже.

- [ ] **Step 6: Заменить роутер `api/app/routers/jobs.py` рабочей версией**

```python
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.celery_client import enqueue_pipeline
from app.db import get_session
from app.models import Job, JobStatus, SourceType
from app.schemas import JobCreate, JobOut
from shared.stages import Stage

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _persist_job(db: Session, source_type: SourceType, source_url: str | None, user: str) -> Job:
    job = Job(
        source_type=source_type,
        source_url=source_url,
        status=JobStatus.QUEUED,
        current_stage=Stage.FETCH,
        created_by=user,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_pipeline(str(job.id))
    return job


@router.post("", response_model=JobOut, status_code=201)
def create_job_json(
    payload: JobCreate,
    user: Annotated[str, Header(alias="X-User")] = "anonymous",
    db: Session = Depends(get_session),
) -> Job:
    if payload.source_type == SourceType.URL and not payload.source_url:
        raise HTTPException(status_code=422, detail="source_url required for URL source")
    return _persist_job(db, payload.source_type, payload.source_url, user)


@router.post("/upload", response_model=JobOut, status_code=201)
def create_job_upload(
    file: Annotated[UploadFile, File()],
    user: Annotated[str, Header(alias="X-User")] = "anonymous",
    db: Session = Depends(get_session),
) -> Job:
    # Сохранение файла в MinIO — задача стадии fetch.
    # Здесь только создаём запись Job; файл прокинется через временное хранилище в Task 8.
    # Для скелета: помечаем как file без source_url, fetch-стадия позже подберёт.
    if file.content_type and not file.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="only video/* accepted")
    return _persist_job(db, SourceType.FILE, None, user)
```

> Адаптируем тест: загрузка файла теперь идёт на `/jobs/upload`. Обновляю тест.

- [ ] **Step 7: Обновить `api/tests/test_jobs.py`**

```python
import io


def test_create_job_with_url_returns_201(client):
    response = client.post(
        "/jobs",
        json={"source_type": "url", "source_url": "https://youtube.com/watch?v=abc"},
        headers={"X-User": "alice@example.com"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["current_stage"] == "fetch"
    assert body["source_url"] == "https://youtube.com/watch?v=abc"
    assert body["created_by"] == "alice@example.com"
    assert "id" in body


def test_create_job_with_url_requires_source_url(client):
    response = client.post(
        "/jobs",
        json={"source_type": "url"},
        headers={"X-User": "alice@example.com"},
    )
    assert response.status_code == 422


def test_create_job_upload(client):
    files = {"file": ("video.mp4", io.BytesIO(b"FAKEMP4"), "video/mp4")}
    response = client.post(
        "/jobs/upload",
        files=files,
        headers={"X-User": "alice@example.com"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["source_type"] == "file"
    assert body["source_url"] is None
```

- [ ] **Step 8: Обновить `conftest.py` — фикстуры db через testcontainers**

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.celery_client import celery
from app.db import Base, get_session
from app.main import app


@pytest.fixture(scope="session")
def postgres():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def redis_container():
    with RedisContainer("redis:7-alpine") as r:
        yield r


@pytest.fixture(scope="session")
def engine(postgres):
    eng = create_engine(postgres.get_connection_url().replace("psycopg2", "psycopg"))
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def db_session(engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def _stub_celery(monkeypatch):
    monkeypatch.setattr(celery, "send_task", lambda *a, **k: None)


@pytest.fixture
def client(db_session, redis_container):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
```

- [ ] **Step 9: Подключить роутер в `api/app/main.py`**

```python
from fastapi import FastAPI

from app.routers import jobs

app = FastAPI(title="Video Slicer API")
app.include_router(jobs.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 10: Запустить тесты — должны пройти**

Run: `docker compose run --rm api pytest tests/ -v`
Expected: 4 passed (`test_health` + 3 в `test_jobs`).

- [ ] **Step 11: Commit**

```bash
git add api/
git commit -m "feat(api): POST /jobs and /jobs/upload with persistence and celery dispatch"
```

---

## Task 7: GET `/jobs` (список) и GET `/jobs/{id}` (детали)

**Files:**
- Modify: `api/app/routers/jobs.py`
- Modify: `api/tests/test_jobs.py`

- [ ] **Step 1: Дописать падающие тесты в `api/tests/test_jobs.py`**

```python
import uuid


def test_get_job_by_id(client):
    create = client.post(
        "/jobs",
        json={"source_type": "url", "source_url": "https://youtu.be/x"},
        headers={"X-User": "alice@example.com"},
    )
    job_id = create.json()["id"]

    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["id"] == job_id


def test_get_job_not_found(client):
    response = client.get(f"/jobs/{uuid.uuid4()}")
    assert response.status_code == 404


def test_list_jobs(client):
    client.post(
        "/jobs",
        json={"source_type": "url", "source_url": "https://youtu.be/x"},
        headers={"X-User": "alice@example.com"},
    )
    response = client.get("/jobs")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 1
```

- [ ] **Step 2: Запустить — должны упасть**

Run: `docker compose run --rm api pytest tests/test_jobs.py -v`
Expected: 3 новых fail (`404 not implemented`).

- [ ] **Step 3: Дополнить `api/app/routers/jobs.py`**

```python
from sqlalchemy import select

from app.schemas import JobsList


@router.get("", response_model=JobsList)
def list_jobs(db: Session = Depends(get_session)) -> JobsList:
    rows = db.execute(select(Job).order_by(Job.created_at.desc())).scalars().all()
    return JobsList(items=[JobOut.model_validate(j) for j in rows])


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_session)) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job
```

- [ ] **Step 4: Запустить тесты — все 7 проходят**

Run: `docker compose run --rm api pytest tests/ -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add api/
git commit -m "feat(api): list and read endpoints for jobs"
```

---

## Task 8: Worker — pyproject, Dockerfile, заглушка Celery-приложения

**Files:**
- Create: `worker/pyproject.toml`
- Create: `worker/Dockerfile`
- Create: `worker/worker/__init__.py` (пустой)
- Create: `worker/worker/celery_app.py`
- Create: `worker/worker/db.py`
- Create: `worker/worker/models.py`
- Create: `worker/worker/progress.py`

- [ ] **Step 1: Создать `worker/pyproject.toml`**

```toml
[tool.poetry]
name = "video-slicer-worker"
version = "0.1.0"
authors = ["team"]
packages = [{ include = "worker" }]

[tool.poetry.dependencies]
python = "^3.12"
celery = { version = "^5.4", extras = ["redis"] }
sqlalchemy = "^2.0"
psycopg = { version = "^3.2", extras = ["binary"] }
redis = "^5.2"
pydantic = "^2.9"
pydantic-settings = "^2.6"
video-slicer-shared = { path = "../shared", develop = true }

[tool.poetry.group.dev.dependencies]
pytest = "^8"
testcontainers = { version = "^4.8", extras = ["postgres", "redis"] }

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

- [ ] **Step 2: Создать `worker/Dockerfile`**

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
ENV PYTHONPATH=/app:/shared
```

- [ ] **Step 3: Создать `worker/worker/db.py` и `worker/worker/models.py`**

> Идентичны API-версиям. Чтобы не дублировать в коде, в Phase 1 терпим копию; в будущем плане вынесем в `shared/` либо в общий пакет. Сейчас — копируем содержимое из `api/app/db.py` и `api/app/models.py` дословно, заменив `from app.config import settings` на собственный `worker/worker/config.py`.

- [ ] **Step 4: Создать `worker/worker/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    redis_url: str = "redis://redis:6379/0"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
```

- [ ] **Step 5: Создать `worker/worker/db.py`** (та же реализация, ссылка на `worker.config`)

```python
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from worker.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def session_scope() -> Session:
    return SessionLocal()
```

- [ ] **Step 6: Создать `worker/worker/models.py`** — точная копия `api/app/models.py`, но с импортом `from worker.db import Base`.

- [ ] **Step 7: Создать `worker/worker/celery_app.py`**

```python
from celery import Celery

from worker.config import settings

app = Celery(
    "video_slicer",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "worker.tasks.chain",
        "worker.tasks.fetch",
        "worker.tasks.transcribe",
        "worker.tasks.segment",
        "worker.tasks.cut",
        "worker.tasks.thumbnail",
        "worker.tasks.metadata",
        "worker.tasks.upload",
    ],
)

app.conf.task_default_queue = "default"
app.conf.task_acks_late = True
app.conf.worker_prefetch_multiplier = 1
```

- [ ] **Step 8: Создать `worker/worker/progress.py`**

```python
import json

import redis

from worker.config import settings
from shared.stages import Stage

_redis = redis.from_url(settings.redis_url)

CHANNEL = "job-progress"


def publish_progress(job_id: str, stage: Stage, status: str = "running") -> None:
    payload = json.dumps({"job_id": job_id, "stage": stage.value, "status": status})
    _redis.publish(CHANNEL, payload)
```

- [ ] **Step 9: Commit**

```bash
git add worker/
git commit -m "feat(worker): scaffold celery app, db and progress publisher"
```

---

## Task 9: Заглушечные стадии и Celery `chain`

**Files:**
- Create: `worker/worker/tasks/__init__.py` (пустой)
- Create: `worker/worker/tasks/chain.py`
- Create: `worker/worker/tasks/fetch.py`
- Create: `worker/worker/tasks/transcribe.py`
- Create: `worker/worker/tasks/segment.py`
- Create: `worker/worker/tasks/cut.py`
- Create: `worker/worker/tasks/thumbnail.py`
- Create: `worker/worker/tasks/metadata.py`
- Create: `worker/worker/tasks/upload.py`
- Create: `worker/tests/__init__.py` (пустой)
- Create: `worker/tests/conftest.py`
- Create: `worker/tests/test_chain_progress.py`

- [ ] **Step 1: Написать падающий тест в `worker/tests/test_chain_progress.py`**

```python
import uuid

from shared.stages import PIPELINE_ORDER, Stage
from worker.tasks.chain import build_pipeline


def test_pipeline_walks_through_all_stages(db_session, capture_progress, fake_job):
    build_pipeline.apply(args=[str(fake_job.id)]).get()

    db_session.refresh(fake_job)
    assert fake_job.current_stage == Stage.DONE
    assert fake_job.status.value == "succeeded"

    stages = [event["stage"] for event in capture_progress]
    assert stages == [s.value for s in PIPELINE_ORDER] + ["done"]
```

- [ ] **Step 2: Создать `worker/tests/conftest.py`**

```python
import json
import threading
import uuid

import pytest
import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from worker.celery_app import app as celery_app
from worker.db import Base
from worker.models import Job, JobStatus, SourceType
from worker.progress import CHANNEL
from shared.stages import Stage


@pytest.fixture(scope="session")
def postgres():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def redis_container():
    with RedisContainer("redis:7-alpine") as r:
        yield r


@pytest.fixture(scope="session", autouse=True)
def _celery_eager(redis_container):
    celery_app.conf.task_always_eager = True
    celery_app.conf.broker_url = redis_container.get_connection_url()
    celery_app.conf.result_backend = redis_container.get_connection_url()


@pytest.fixture(scope="session")
def engine(postgres):
    eng = create_engine(postgres.get_connection_url().replace("psycopg2", "psycopg"))
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def db_session(engine, monkeypatch):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    monkeypatch.setattr("worker.db.SessionLocal", SessionLocal)
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def fake_job(db_session):
    job = Job(
        source_type=SourceType.URL,
        source_url="https://example.com",
        status=JobStatus.QUEUED,
        current_stage=Stage.FETCH,
        created_by="t@e.st",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


@pytest.fixture
def capture_progress(redis_container):
    events: list[dict] = []
    client = redis.from_url(redis_container.get_connection_url())
    pubsub = client.pubsub()
    pubsub.subscribe(CHANNEL)

    def _reader():
        for msg in pubsub.listen():
            if msg["type"] == "message":
                events.append(json.loads(msg["data"]))

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    yield events
    pubsub.close()
```

- [ ] **Step 3: Запустить — упадёт на отсутствии `build_pipeline`**

Run: `docker compose run --rm worker pytest tests/ -v`
Expected: FAIL `ImportError: cannot import name 'build_pipeline'`.

- [ ] **Step 4: Реализовать `worker/worker/tasks/fetch.py`**

```python
from worker.celery_app import app
from worker.db import session_scope
from worker.models import Job, JobStatus
from worker.progress import publish_progress
from shared.stages import Stage


@app.task(name="worker.tasks.fetch.run")
def run(job_id: str) -> str:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if not job:
            raise RuntimeError(f"job {job_id} not found")
        job.status = JobStatus.RUNNING
        job.current_stage = Stage.FETCH
        db.commit()
    publish_progress(job_id, Stage.FETCH)
    # TODO Phase 2: реальное скачивание / приём файла.
    return job_id
```

- [ ] **Step 5: Реализовать остальные стадии по тому же шаблону**

Для каждой стадии создать файл `worker/worker/tasks/<stage>.py`. Содержимое одинаковое, отличаются только имя задачи и значение `Stage`:

```python
from worker.celery_app import app
from worker.db import session_scope
from worker.models import Job
from worker.progress import publish_progress
from shared.stages import Stage


@app.task(name="worker.tasks.<stage>.run")
def run(job_id: str) -> str:
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.current_stage = Stage.<UPPER>
        db.commit()
    publish_progress(job_id, Stage.<UPPER>)
    return job_id
```

Создать в этом виде для: `transcribe`, `segment`, `cut`, `thumbnail`, `metadata`, `upload`. Подставить соответствующие `Stage` и имя задачи.

- [ ] **Step 6: Реализовать `worker/worker/tasks/chain.py`**

```python
from celery import chain

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Job, JobStatus
from worker.progress import publish_progress
from worker.tasks import fetch, transcribe, segment, cut, thumbnail, metadata, upload
from shared.stages import Stage


@app.task(name="worker.tasks.chain.build_pipeline")
def build_pipeline(job_id: str) -> str:
    pipeline = chain(
        fetch.run.si(job_id),
        transcribe.run.si(job_id),
        segment.run.si(job_id),
        cut.run.si(job_id),
        thumbnail.run.si(job_id),
        metadata.run.si(job_id),
        upload.run.si(job_id),
        finalize.si(job_id),
    )
    pipeline.apply_async()
    return job_id


@app.task(name="worker.tasks.chain.finalize")
def finalize(job_id: str) -> str:
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.current_stage = Stage.DONE
        job.status = JobStatus.SUCCEEDED
        db.commit()
    publish_progress(job_id, Stage.DONE, status="succeeded")
    return job_id
```

- [ ] **Step 7: Запустить тесты — должны пройти**

Run: `docker compose run --rm worker pytest tests/ -v`
Expected: 1 passed.

- [ ] **Step 8: Запустить интеграционный сценарий в живом docker-compose**

```bash
docker compose up -d
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -H "X-User: alice@example.com" \
  -d '{"source_type":"url","source_url":"https://example.com"}' | tee /tmp/job.json
JOB_ID=$(jq -r .id /tmp/job.json)
sleep 2
curl -s http://localhost:8000/jobs/$JOB_ID
```

Expected: после задержки `status: "succeeded"`, `current_stage: "done"`.

- [ ] **Step 9: Commit**

```bash
git add worker/
git commit -m "feat(worker): pipeline chain with stage stubs and progress publishing"
```

---

## Task 10: Next.js — каркас проекта

**Files:**
- Create: `web/package.json`
- Create: `web/tsconfig.json`
- Create: `web/next.config.ts`
- Create: `web/tailwind.config.ts`
- Create: `web/postcss.config.mjs`
- Create: `web/app/layout.tsx`
- Create: `web/app/globals.css`
- Create: `web/app/page.tsx`
- Create: `web/lib/api.ts`
- Create: `web/Dockerfile`

- [ ] **Step 1: Создать `web/package.json`**

```json
{
  "name": "video-slicer-web",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev -H 0.0.0.0",
    "build": "next build",
    "start": "next start",
    "lint": "next lint"
  },
  "dependencies": {
    "next": "15.0.3",
    "react": "19.0.0",
    "react-dom": "19.0.0"
  },
  "devDependencies": {
    "@types/node": "^22",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "typescript": "^5.6",
    "tailwindcss": "^3.4",
    "postcss": "^8.4",
    "autoprefixer": "^10.4"
  }
}
```

- [ ] **Step 2: Создать `web/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 3: Создать `web/next.config.ts`**

```ts
import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
};

export default config;
```

- [ ] **Step 4: Создать `web/tailwind.config.ts`**

```ts
import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
};

export default config;
```

- [ ] **Step 5: Создать `web/postcss.config.mjs`**

```mjs
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
};
```

- [ ] **Step 6: Создать `web/app/globals.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 7: Создать `web/app/layout.tsx`**

```tsx
import "./globals.css";

export const metadata = {
  title: "Video Slicer",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="min-h-screen bg-neutral-50 text-neutral-900">
        <div className="mx-auto max-w-5xl p-6">{children}</div>
      </body>
    </html>
  );
}
```

- [ ] **Step 8: Создать `web/lib/api.ts`**

```ts
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Stage =
  | "fetch" | "transcribe" | "segment" | "cut"
  | "thumbnail" | "metadata" | "upload" | "done";

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface Job {
  id: string;
  source_type: "file" | "url";
  source_url: string | null;
  status: JobStatus;
  current_stage: Stage;
  created_by: string;
  created_at: string;
  error: string | null;
}

export async function listJobs(): Promise<{ items: Job[] }> {
  const r = await fetch(`${BASE}/jobs`, { cache: "no-store" });
  if (!r.ok) throw new Error(`listJobs ${r.status}`);
  return r.json();
}

export async function getJob(id: string): Promise<Job> {
  const r = await fetch(`${BASE}/jobs/${id}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`getJob ${r.status}`);
  return r.json();
}

export async function createJobFromUrl(url: string, user: string): Promise<Job> {
  const r = await fetch(`${BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User": user },
    body: JSON.stringify({ source_type: "url", source_url: url }),
  });
  if (!r.ok) throw new Error(`createJobFromUrl ${r.status}`);
  return r.json();
}

export async function createJobFromFile(file: File, user: string): Promise<Job> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${BASE}/jobs/upload`, {
    method: "POST",
    headers: { "X-User": user },
    body: fd,
  });
  if (!r.ok) throw new Error(`createJobFromFile ${r.status}`);
  return r.json();
}
```

- [ ] **Step 9: Создать `web/app/page.tsx`** — список задач

```tsx
import Link from "next/link";

import { listJobs } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const { items } = await listJobs();
  return (
    <main className="space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Задачи</h1>
        <Link href="/jobs/new" className="rounded bg-black px-4 py-2 text-white">
          Новая задача
        </Link>
      </header>
      {items.length === 0 ? (
        <p className="text-neutral-500">Пока нет задач.</p>
      ) : (
        <ul className="divide-y rounded border bg-white">
          {items.map((job) => (
            <li key={job.id} className="flex items-center justify-between p-4">
              <div>
                <p className="font-mono text-sm">{job.id.slice(0, 8)}</p>
                <p className="text-sm text-neutral-500">
                  {job.source_url ?? "файл"} · {job.created_by}
                </p>
              </div>
              <div className="flex items-center gap-4">
                <span className="text-sm">{job.current_stage}</span>
                <span
                  className={`rounded px-2 py-1 text-xs ${
                    job.status === "succeeded"
                      ? "bg-green-100 text-green-700"
                      : job.status === "failed"
                      ? "bg-red-100 text-red-700"
                      : "bg-amber-100 text-amber-700"
                  }`}
                >
                  {job.status}
                </span>
                <Link href={`/jobs/${job.id}`} className="text-sm underline">
                  открыть
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
```

- [ ] **Step 10: Создать `web/Dockerfile`**

```dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
EXPOSE 3000
```

- [ ] **Step 11: Собрать и поднять веб**

Run:
```bash
docker compose up -d --build web
curl -s -I http://localhost:3000
```
Expected: HTTP 200, страница со списком (возможно пустым).

- [ ] **Step 12: Commit**

```bash
git add web/
git commit -m "feat(web): next.js skeleton with jobs list page"
```

---

## Task 11: Страницы `/jobs/new` и `/jobs/[id]`

**Files:**
- Create: `web/app/jobs/new/page.tsx`
- Create: `web/app/jobs/[id]/page.tsx`
- Create: `web/components/NewJobForm.tsx`
- Create: `web/components/JobProgress.tsx`

- [ ] **Step 1: Создать `web/components/NewJobForm.tsx`**

```tsx
"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { createJobFromFile, createJobFromUrl } from "@/lib/api";

const USER = "anon@local"; // в Phase 1 — захардкожено; в будущем заполним через reverse-proxy header.

export function NewJobForm() {
  const router = useRouter();
  const [mode, setMode] = useState<"url" | "file">("url");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const job =
        mode === "url"
          ? await createJobFromUrl(url, USER)
          : file
          ? await createJobFromFile(file, USER)
          : null;
      if (!job) throw new Error("Файл не выбран");
      router.push(`/jobs/${job.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setMode("url")}
          className={`rounded px-3 py-1 ${mode === "url" ? "bg-black text-white" : "bg-neutral-200"}`}
        >
          URL
        </button>
        <button
          type="button"
          onClick={() => setMode("file")}
          className={`rounded px-3 py-1 ${mode === "file" ? "bg-black text-white" : "bg-neutral-200"}`}
        >
          Файл
        </button>
      </div>

      {mode === "url" ? (
        <input
          type="url"
          required
          placeholder="https://youtube.com/..."
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="w-full rounded border px-3 py-2"
        />
      ) : (
        <input
          type="file"
          accept="video/*"
          required
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="w-full"
        />
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}

      <button
        type="submit"
        disabled={submitting}
        className="rounded bg-black px-4 py-2 text-white disabled:opacity-50"
      >
        {submitting ? "Создаю..." : "Создать"}
      </button>
    </form>
  );
}
```

- [ ] **Step 2: Создать `web/app/jobs/new/page.tsx`**

```tsx
import { NewJobForm } from "@/components/NewJobForm";

export default function NewJobPage() {
  return (
    <main className="space-y-6">
      <h1 className="text-2xl font-semibold">Новая задача</h1>
      <NewJobForm />
    </main>
  );
}
```

- [ ] **Step 3: Создать `web/components/JobProgress.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";

import { Job, Stage, getJob } from "@/lib/api";

const STAGES: Stage[] = [
  "fetch", "transcribe", "segment", "cut", "thumbnail", "metadata", "upload", "done",
];

export function JobProgress({ initial }: { initial: Job }) {
  const [job, setJob] = useState(initial);

  useEffect(() => {
    if (job.status === "succeeded" || job.status === "failed") return;
    const t = setInterval(async () => {
      try {
        const next = await getJob(job.id);
        setJob(next);
      } catch {
        /* ignore transient errors */
      }
    }, 1500);
    return () => clearInterval(t);
  }, [job.id, job.status]);

  const currentIndex = STAGES.indexOf(job.current_stage);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-semibold">Статус:</span>
        <span>{job.status}</span>
        {job.error && <span className="text-red-600">{job.error}</span>}
      </div>
      <ol className="flex flex-wrap gap-2">
        {STAGES.map((stage, i) => (
          <li
            key={stage}
            className={`rounded px-3 py-1 text-xs ${
              i < currentIndex
                ? "bg-green-100 text-green-700"
                : i === currentIndex
                ? "bg-amber-100 text-amber-700"
                : "bg-neutral-100 text-neutral-500"
            }`}
          >
            {stage}
          </li>
        ))}
      </ol>
    </div>
  );
}
```

- [ ] **Step 4: Создать `web/app/jobs/[id]/page.tsx`**

```tsx
import { JobProgress } from "@/components/JobProgress";
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
    </main>
  );
}
```

- [ ] **Step 5: Пересобрать веб и проверить вручную**

Run:
```bash
docker compose up -d --build web
```
Open: <http://localhost:3000/jobs/new> → создать URL-задачу → перейти на детали → убедиться, что прогресс через ~2 сек добегает до `done`.

- [ ] **Step 6: Commit**

```bash
git add web/
git commit -m "feat(web): new job form and details page with polling progress"
```

---

## Task 12: Полная smoke-проверка пайплайна

- [ ] **Step 1: Поднять весь стек заново**

Run:
```bash
docker compose down -v
docker compose up -d --build
docker compose run --rm api alembic upgrade head
```
Expected: все 5 сервисов поднимаются, миграция применена.

- [ ] **Step 2: Создать задачу через UI и убедиться, что она доходит до `done`**

Открыть <http://localhost:3000/jobs/new>, ввести любой URL, нажать «Создать». На странице деталей дождаться `status: succeeded`.

- [ ] **Step 3: Прогнать все автотесты API и воркера**

Run:
```bash
docker compose run --rm api pytest tests/ -v
docker compose run --rm worker pytest tests/ -v
```
Expected: всё зелёное.

- [ ] **Step 4: Commit (если были мелкие правки) и пометить веху**

```bash
git tag phase-1-skeleton
```

---

## Self-Review

**Spec coverage:**
- Все компоненты архитектуры (`web/`, `api/`, `worker/`, Postgres, MinIO, Redis) подняты в docker-compose — Task 1.
- Структура репозитория из spec реализована — Tasks 2/3/8/10.
- Модель данных (`Job`, `Asset`, `Segment`, `Upload`, `YouTubeAccount`) с полями из spec — Task 4.
- Стадии пайплайна как заглушки + `chain` — Task 9. Идемпотентность реального пайплайна вне Phase 1 (стадии-заглушки не создают артефактов, нечего проверять).
- UI: список, форма создания, детали с прогрессом — Tasks 10/11.
- Smoke-сценарий, описанный в spec, — Task 12.

**Что осознанно НЕ покрыто в Phase 1 (будет в следующих планах):**
- Реальные стадии (`fetch` с yt-dlp / multipart-uploads → MinIO, `transcribe`, `segment`, `cut`, `thumbnail`, `metadata`, `upload`).
- WebSocket-прогресс (сейчас polling, что приемлемо для skeleton). WebSocket-трансляция Redis pub/sub — план Phase 5.
- Retry-эндпоинт `POST /jobs/{id}/retry?from=...`.
- Управление YouTube-аккаунтом (страница `/settings`).
- E2E на коротком тестовом видео.

**Placeholder scan:** в коде есть один `TODO Phase 2` в `worker/worker/tasks/fetch.py` — он не «плейсхолдер плана», а явная отметка перехода между планами; реализация заглушки сама по себе валидна и используется в smoke-тестах. Других TBD/TODO/«similar to» в плане нет.

**Type consistency:** `Stage` определён один раз в `shared/shared/stages.py`, импортируется и в API, и в worker. `Job.status`, `Job.current_stage`, `Segment.status`, `Upload.status` — одни и те же enum'ы во всех слоях. Имена эндпоинтов (`POST /jobs`, `POST /jobs/upload`, `GET /jobs`, `GET /jobs/{id}`) совпадают между Python и TypeScript-клиентом.
