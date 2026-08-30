# esmoon_video_editor — авто-нарезка YouTube-видео на смысловые сегменты

Сервис принимает ссылку на YouTube-видео и автоматически:
1. Скачивает видео и автосубтитры (yt-dlp).
2. Транскрибирует (YouTube VTT, если ≤ 30 мин; иначе faster-whisper).
3. Делит на 7–15-минутные смысловые сегменты через LLM (google/gemini-3.5-flash).
4. Режет видео ffmpeg-ом (stream copy).
5. Делает 3 превью на сегмент.
6. Генерирует YouTube-метаданные (title, description, tags) через LLM.
7. Отдаёт результат в веб-UI с возможностью править метаданные и выбирать превью.

Стек: FastAPI + Celery + Postgres + Redis + MinIO + Next.js 15.

---

## 1. Требования

- Docker Desktop (или Docker Engine + compose v2)
- Свободные порты: `5432`, `6379`, `9000`, `9001`, `8000`, `3000`
- API-ключ [polza.ai](https://polza.ai) (OpenAI-совместимый gateway)
- ~20 ГБ свободного места (модели Whisper кэшируются в volume)

## 2. Первый запуск

```bash
# 1. Скопировать .env
cp .env.example .env

# 2. Вписать в .env свой POLZA_API_KEY
#    POLZA_API_KEY=pza_xxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 3. Поднять весь стек
docker compose up -d --build

# 4. Применить миграции Postgres
docker compose exec api alembic upgrade head
```

Bucket в MinIO создаётся автоматически при первой загрузке файла воркером (`ensure_bucket()` в `worker/worker/services/storage.py`).

После этого:
- API: http://localhost:8000 (docs: http://localhost:8000/docs)
- Web UI: http://localhost:3000
- MinIO console: http://localhost:9001 (логин/пароль из `.env`, по умолчанию `minioadmin`/`minioadmin`)

## 3. Запуск нарезки

### Через UI
1. Открыть http://localhost:3000
2. Вставить ссылку на YouTube-видео → «Запустить»
3. Дождаться, пока job перейдёт в `succeeded`
4. На странице джоба появятся карточки сегментов: выбрать превью, поправить title/description/tags, скачать клип

### Через API
```bash
# Создать джоб (created_by берётся из заголовка X-User; по умолчанию "anonymous")
curl -X POST http://localhost:8000/jobs \
  -H 'Content-Type: application/json' \
  -H 'X-User: you@example.com' \
  -d '{"source_type":"url", "source_url":"https://www.youtube.com/watch?v=..."}'

# Статус и текущая стадия джоба (фронт опрашивает поллингом):
curl http://localhost:8000/jobs/<job_id>

# Список сегментов после succeeded:
curl http://localhost:8000/jobs/<job_id>/segments
```

Прогресс по стадиям дополнительно публикуется в Redis pub/sub-канал `job_progress` — можно подписаться через `redis-cli SUBSCRIBE job_progress` для отладки.

## 4. Переменные окружения (`.env`)

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | креды Postgres | `videoslicer` |
| `REDIS_URL` | URL Redis (брокер Celery + pub/sub прогресса) | `redis://redis:6379/0` |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | креды MinIO | `minioadmin` |
| `MINIO_ENDPOINT` | внутренний адрес MinIO (api ↔ minio внутри сети) | `http://minio:9000` |
| `MINIO_PUBLIC_ENDPOINT` | публичный адрес для presigned-URL (браузер ↔ minio) | `http://localhost:9000` |
| `MINIO_BUCKET` | имя bucket-а | `video-slicer` |
| `POLZA_API_KEY` | ключ polza.ai | — (обязательно) |
| `POLZA_MODEL_SEGMENT` | модель для нарезки на сегменты | `google/gemini-3.5-flash` |
| `POLZA_MODEL_METADATA` | модель для метаданных | `google/gemini-3.5-flash` |
| `WHISPER_MODEL` | размер модели faster-whisper | `base` |
| `API_PORT` / `WEB_PORT` | проброшенные порты | `8000` / `3000` |
| `NEXT_PUBLIC_API_BASE` | URL API для фронта | `http://localhost:8000` |

## 5. Сервисы и порты

| Сервис | Порт | Что делает |
|---|---|---|
| `postgres` | 5432 | основная БД + `videoslicer_test` для тестов |
| `redis` | 6379 | брокер Celery + pub/sub прогресса |
| `minio` | 9000 (API), 9001 (console) | хранилище видео/превью/транскриптов |
| `api` | 8000 | FastAPI + Alembic |
| `worker` | — | Celery worker (concurrency=2), все стадии пайплайна |
| `web` | 3000 | Next.js 15 App Router |

## 6. Тесты

```bash
# Worker (юниты)
docker compose exec worker pytest tests/ -v

# API
docker compose exec api pytest tests/ -v
```

Тесты используют изолированную БД `videoslicer_test` (создаётся init-скриптом `db/init/01_create_test_db.sql` при первом старте volume-а Postgres) — dev-БД не затрагивается.

## 7. Типичные операции

**Перечитать `.env` (после правки переменных):**
```bash
docker compose up -d --force-recreate worker api
```
`docker compose restart` НЕ перечитывает `env_file`, нужен именно `--force-recreate`.

**Посмотреть прогресс конкретного джоба в логах воркера:**
```bash
docker compose logs -f worker | grep <job_id>
```

**Сбросить состояние БД и пересоздать миграции с нуля:**
```bash
docker compose down -v          # удаляет volume pg_data → видео и тесты тоже улетят
docker compose up -d
docker compose exec api alembic upgrade head
```

**Очистить MinIO (если bucket мусорный):**

Зайти в MinIO console (http://localhost:9001), удалить bucket `video-slicer`. При следующей загрузке воркер пересоздаст его автоматически.

## 8. Стадии пайплайна (для отладки)

`fetch` → `transcribe` → `segment` → `cut` → `thumbnail` → `metadata`

Каждая стадия публикует прогресс в Redis-канал `job_progress`. Падение любой → `job.status = FAILED` с текстом ошибки в `job.error`.

Для длинных видео (> 30 мин) автосабы YouTube игнорируются и используется Whisper — это даёт чистый транскрипт без rolling-captions.

## 9. Безопасность

- `.env` в `.gitignore` — никогда не коммитьте реальные ключи.
- Presigned URL-ы MinIO живут 1 час.
- Полные транскрипты и API-ключи нигде не логируются (только длина + первые 200 символов).

## 10. Структура репо

```
api/                FastAPI + Alembic
  app/
    routers/        /jobs, /segments, /assets
    services/       storage (boto3), celery_client
    models.py       SQLAlchemy 2
  alembic/versions/
worker/             Celery worker
  worker/
    tasks/          fetch, transcribe, segment, cut, thumbnail, metadata, chain
    services/       ffmpeg, vtt, whisper, storage, llm
    prompts/        segment, metadata
web/                Next.js 15 (App Router)
  app/jobs/[id]/    страница джоба + SegmentList
  components/       SegmentCard, ThumbnailPicker, MetadataEditor
shared/             общий пакет (Stage enum)
db/init/            init-скрипты Postgres (создание тестовой БД)
docs/               планы и спецификации
```
