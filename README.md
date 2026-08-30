# esmoon_video_editor — авто-нарезка YouTube-видео на смысловые сегменты

Сервис принимает ссылку на YouTube-видео и автоматически:
1. Скачивает видео и автосубтитры (yt-dlp).
2. Транскрибирует (YouTube VTT, если ≤ 30 мин; иначе faster-whisper).
3. Создаёт таймкоды-главы для всего исходного видео.
4. Находит самостоятельные фрагменты через Codex CLI: вход по подписке ChatGPT, модель `gpt-5.6-luna`, `reasoning effort=max`. Длительность не задаётся: один ролик должен полностью раскрывать одну крупную тему, её главную боль и законченное решение. Соседние примеры и подпункты одной темы объединяются.
5. Оценивает каждый кандидат по `relevance`, `pain`, `hook`, `value`. Решение `publish` принимается при ясной боли и полноценном решении; остальные кандидаты получают `skip`.
6. Режет ffmpeg-ом только фрагменты с решением `publish`.
7. Делает 3 превью на публикуемый сегмент.
8. Генерирует YouTube-метаданные (title, description, tags) через LLM.
9. Отдаёт результат в веб-UI с возможностью править метаданные и выбирать превью.

Стек: FastAPI + Celery + Postgres + Redis + MinIO + Next.js 15.

---

## 1. Требования

- macOS с приложением ChatGPT и активной подпиской, в которой доступен Codex
- Python 3.12 и ffmpeg для локального worker
- Docker Desktop (или Docker Engine + compose v2) для Postgres, Redis, MinIO, API и Web UI
- Свободные порты: `5432`, `6379`, `9000`, `9001`, `8000`, `3000`
- ~20 ГБ свободного места (модели Whisper кэшируются в `.cache/whisper`)

## 2. Первый запуск

```bash
# 1. Скопировать .env
cp .env.example .env

# 2. Проверить локальный вход в Codex через подписку ChatGPT
"/Applications/ChatGPT.app/Contents/Resources/codex" login status

# 3. Создать отдельное Python-окружение и установить локальный worker
./scripts/setup-local-worker.sh

# 4. Поднять инфраструктуру, API и Web UI
docker compose up -d --build

# 5. Применить миграции Postgres
docker compose exec api alembic upgrade head

# 6. В отдельном окне терминала запустить локальный worker
./scripts/run-worker-local.sh
```

Worker работает прямо на Mac. Он использует вход, уже сохранённый приложением ChatGPT. Файл `~/.codex/auth.json` не копируется и не передаётся в Docker.

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
4. Вверху страницы появятся таймкоды всего исходника, ниже — оценки всех кандидатов и карточки фрагментов с решением `publish`

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
| `CODEX_CLI_PATH` | путь к локальному Codex CLI | `/Applications/ChatGPT.app/Contents/Resources/codex` |
| `CODEX_MODEL` | модель для сегментов и метаданных | `gpt-5.6-luna` |
| `CODEX_REASONING_EFFORT` | глубина рассуждений модели | `max` |
| `CODEX_TIMEOUT_SEC` | максимум времени одного вызова Codex | `1800` |
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
| `worker` | локальный процесс macOS | Celery worker (concurrency=1), все стадии пайплайна и Codex CLI |
| `web` | 3000 | Next.js 15 App Router |

## 6. Тесты

```bash
# Worker (юниты, запуск из корня репозитория)
cd worker && ../.venv-worker/bin/pytest tests/ -v

# API
docker compose exec api pytest tests/ -v
```

Тесты используют изолированную БД `videoslicer_test` (создаётся init-скриптом `db/init/01_create_test_db.sql` при первом старте volume-а Postgres) — dev-БД не затрагивается.

## 7. Типичные операции

**Перечитать `.env` (после правки переменных):**
```bash
docker compose up -d --force-recreate api
./scripts/run-worker-local.sh
```
`docker compose restart` не перечитывает `env_file`, поэтому API нужен `--force-recreate`. Локальный worker нужно остановить и запустить заново.

**Посмотреть прогресс конкретного джоба в логах воркера:**
```bash
./scripts/run-worker-local.sh 2>&1 | grep <job_id>
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

- `.env`, `auth.json`, `.cache/` и `.venv-worker/` находятся в `.gitignore`.
- Codex использует локальный вход ChatGPT. `~/.codex/auth.json` считается паролем: его нельзя копировать в репозиторий, Docker-образ или логи.
- Presigned URL-ы MinIO живут 1 час.
- Полные транскрипты и данные авторизации не логируются.

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
