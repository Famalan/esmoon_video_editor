# Автоматический нарезчик видео с публикацией на YouTube

**Дата:** 2026-05-15
**Статус:** Draft (ожидает ревью)

## Назначение

Внутренний инструмент команды. Принимает длинное видео (загруженный файл или ссылка), автоматически делит его на смысловые горизонтальные отрывки 5–15 минут на основе транскрипта, генерирует превью и метаданные, загружает каждый отрывок на YouTube как приватный черновик. Команда дорабатывает черновики и публикует вручную в YouTube Studio.

## Цели и не-цели

**Цели:**
- Один веб-интерфейс, через который команда заводит задачи и видит прогресс.
- Полный автопайплайн от исходника до черновиков на YouTube без ручных шагов внутри приложения.
- Прозрачный прогресс с возможностью перезапустить с любой стадии при сбое.
- Идемпотентность стадий: повторный запуск не дублирует артефакты.

**Не-цели (вне MVP):**
- Вертикальный формат / Shorts, reframing.
- Ручное редактирование сегментов в UI до нарезки.
- Мультитенантность, биллинг, регистрация пользователей.
- Полноценный CDN/публичная раздача исходников.
- Автоматическая публикация (только черновики).

## Решённые вопросы

| Вопрос | Решение |
|---|---|
| Назначение | Внутренний инструмент команды |
| Источник видео | Загрузка файла + URL (yt-dlp) |
| Субтитры | YouTube если есть → иначе Whisper |
| Формат нарезки | Длинные → горизонтальные отрывки 5–15 мин |
| Алгоритм границ | LLM анализирует транскрипт |
| Превью | Автокадр из видео (середина сегмента) |
| Контроль публикации | Заливаем как private черновики, публикация — вручную |
| Бэкенд | Python, FastAPI + Celery |
| Фронтенд | Next.js + TypeScript |
| Инфраструктура MVP | docker-compose |
| Архитектурный стиль | Линейный Celery-пайплайн с разделяемыми ресурсами |

## Архитектура

```
┌──────────────┐         ┌──────────────────┐
│  Next.js UI  │ ──HTTP─▶│   FastAPI (API)  │
└──────────────┘ ◀─WS────└────────┬─────────┘
                                  │ enqueue
                                  ▼
                          ┌──────────────────┐
                          │   Redis (broker  │
                          │   + pub/sub)     │
                          └────────┬─────────┘
                                   ▼
                          ┌──────────────────┐
                          │  Celery worker   │
                          └────┬─────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ┌──────────┐    ┌──────────┐    ┌────────────┐
        │ Postgres │    │  MinIO   │    │  External  │
        └──────────┘    └──────────┘    │  APIs      │
                                        └────────────┘
```

### Компоненты

- **`web/`** — Next.js (App Router) + TypeScript. Минимальный набор страниц (см. UI).
- **`api/`** — FastAPI. REST + WebSocket для прогресса. Тонкий слой: валидация, постановка в очередь, чтение статуса.
- **`worker/`** — Celery-приложение. Граф задач: `fetch → transcribe → segment → cut → thumbnail → metadata → upload`.
- **`worker/pipeline/`** — доменная логика стадий, без знания о Celery. Чистые функции, тестируется изолированно.
- **`shared/`** — общие Pydantic-модели и интерфейсы (`Protocol`-классы для внешних адаптеров).
- **Postgres** — состояние задач, сегментов, аплоадов.
- **MinIO** (S3-совместимое) — видео, транскрипты, превью.
- **Redis** — Celery broker + результаты + pub/sub для WebSocket-прогресса.

### Внешние интеграции

- **yt-dlp** — скачивание исходного видео и YouTube-субтитров по URL.
- **Whisper** — fallback-транскрипция, когда субтитров с YouTube нет. Реализация (`faster-whisper` локально vs OpenAI Whisper API) — открытый вопрос.
- **Anthropic API (Claude)** — семантическое сегментирование транскрипта и генерация title/description/tags для каждого сегмента. Используем prompt caching на длинном транскрипте (один раз кешируем, переиспользуем для генерации метаданных по сегментам).
- **YouTube Data API v3** — загрузка видео как `privacyStatus: private`. OAuth 2.0, один общий командный аккаунт (открытый вопрос).
- **ffmpeg** — нарезка (`-c copy` где допустимо) и извлечение превью-кадров.

### Структура репозитория

```
esmoon_video_editor/
├── docker-compose.yml
├── web/                       # Next.js
├── api/                       # FastAPI приложение
├── worker/
│   ├── celery_app.py
│   ├── tasks/                 # стадии Celery (тонкие обёртки)
│   └── pipeline/              # доменные функции стадий
├── shared/                    # Pydantic-модели, Protocol-интерфейсы адаптеров
├── docs/superpowers/specs/
└── tests/
```

## Пайплайн: стадии и контракты

Каждая стадия — отдельная Celery-таска, идемпотентна, читает/пишет состояние в Postgres и артефакты в MinIO. Связаны через Celery `chain`. Прогресс публикуется в Redis pub/sub, FastAPI ретранслирует через WebSocket.

| # | Стадия | Вход | Выход | Зависимости |
|---|---|---|---|---|
| 1 | `fetch` | Job: URL или загруженный файл | `source.mp4` в MinIO + базовые метаданные (длительность, разрешение) | yt-dlp для URL |
| 2 | `transcribe` | `source.mp4` (+ URL для попытки субтитров) | `transcript.json` (сегменты `{start, end, text}`) | YouTube subtitles → Whisper |
| 3 | `segment` | `transcript.json` | `Segment[]` в БД (`start_sec`, `end_sec`, `title`, `summary`, `transcript_excerpt`) | Anthropic API |
| 4 | `cut` | `source.mp4` + `Segment[]` | `segment_<n>.mp4` в MinIO | ffmpeg |
| 5 | `thumbnail` | каждый `segment_<n>.mp4` | `thumb_<n>.jpg` в MinIO | ffmpeg |
| 6 | `metadata` | сегмент + его `transcript_excerpt` | `youtube_title`, `youtube_description`, `tags[]` (в `Segment` или `Upload`) | Anthropic API |
| 7 | `upload` | сегмент + метаданные + thumbnail | `youtube_video_id`, `youtube_url` (в `Upload`) | YouTube Data API |

### Контракт идемпотентности

Каждая стадия в начале проверяет: «выходные артефакты уже существуют?» (по `s3_key` в `Asset` или по `Segment.status` / `Upload.status`). Если да — пропускает работу и возвращает существующие ссылки. Это позволяет безопасно перезапускать цепочку.

## Модель данных (Postgres)

```
Job
  id (uuid, pk)
  source_type        enum(file, url)
  source_url         text nullable
  status             enum(queued, running, succeeded, failed)
  current_stage      enum(fetch, transcribe, segment, cut, thumbnail, metadata, upload, done)
  created_by         text                   -- email/имя, без полноценной аутентификации в MVP
  created_at         timestamptz
  error              text nullable

Asset                                       -- физические файлы в MinIO
  id                 uuid pk
  job_id             uuid fk Job
  kind               enum(source, transcript, segment, thumbnail)
  s3_key             text
  mime               text
  size_bytes         bigint
  segment_id         uuid fk Segment nullable

Segment
  id                 uuid pk
  job_id             uuid fk Job
  index              int                    -- порядковый номер
  start_sec          float
  end_sec            float
  title              text
  summary            text
  transcript_excerpt text
  status             enum(pending, cut, thumbnail_ready, metadata_ready, uploaded, failed)
  error              text nullable

Upload
  id                 uuid pk
  segment_id         uuid fk Segment
  youtube_video_id   text
  youtube_url        text
  youtube_title      text
  youtube_description text
  tags               text[]
  status             enum(pending, uploading, uploaded, failed)
  uploaded_at        timestamptz nullable
  error              text nullable

YouTubeAccount
  id                 uuid pk
  channel_id         text
  refresh_token      bytea                  -- зашифрован Fernet, ключ из env
  scopes             text[]
  added_at           timestamptz
```

## Обработка ошибок

- **Retry-политика Celery**:
  - Сетевые таски (`fetch`, `metadata`, `upload`) — экспоненциальный backoff (5s → 25s → 125s), 3 попытки.
  - CPU-таски (`transcribe`, `cut`, `thumbnail`) — без авторетраев. При фейле `Job.status=failed`, `current_stage` хранит, где встали.
- **Идемпотентность** — на уровне каждой стадии (см. выше).
- **Перезапуск с шага**: `POST /jobs/{id}/retry?from=<stage>` — удаляет артефакты от указанной стадии и ниже, заново ставит цепочку.
- **Частичные сбои**: если один сегмент упал на `upload` — остальные продолжают. UI показывает per-segment статус.
- **Логирование**: structlog в JSON, поле `job_id` пробрасывается через context. Корреляционные id во всех вызовах внешних API.

## UI (Next.js)

Минимальный набор страниц:

1. **`/`** — список задач: создатель, статус, текущая стадия, прогресс, ссылка на детали.
2. **`/jobs/new`** — форма: переключатель «URL» / «загрузить файл», поле/drop-zone, кнопка «Создать».
3. **`/jobs/[id]`** — детали:
   - Шапка с прогрессом текущей стадии (WebSocket).
   - После стадии `segment` — таблица сегментов: индекс, тайминги, превью (как только готово), title/description (как только готовы), статус загрузки, ссылка на YouTube (когда загружен).
   - Кнопка «Перезапустить с шага …».
4. **`/settings`** — подключение YouTube-аккаунта (OAuth flow).

Без логина в MVP — внутренняя сеть, доверенная команда. Поле `created_by` заполняется из cookie/header, который команда конфигурирует на reverse-proxy (или просто хардкодит).

## Тестирование

- **Unit** (`pipeline/` функции, без внешних вызовов):
  - Парсинг ответа LLM в `Segment[]`.
  - Парсинг SRT/VTT.
  - Логика выбора thumbnail-кадра.
  - Логика идемпотентности (стадия видит существующий артефакт → ранний возврат).
- **Интеграционные** (стадии с реальной БД и MinIO через `testcontainers`):
  - yt-dlp / Whisper / Anthropic / YouTube — фейковые адаптеры за `Protocol`-интерфейсами.
  - Проверка записи и чтения `Job`/`Segment`/`Upload`.
- **End-to-end (smoke)**:
  - Один прогон на коротком (~2 мин) тестовом видео из репозитория, прогоняется локально перед релизом.
  - Реальный YouTube upload — только ручная проверка на dev-канале команды, не в CI.

## План доставки (для будущего imp-плана)

Скоуп сознательно широкий — пайплайн целиком. В implementation plan он разбивается на инкременты:
1. Каркас: docker-compose, FastAPI, Celery, Postgres, MinIO, заглушка Job.
2. Стадии `fetch` + `transcribe` со списком субтитров и Whisper-фолбэком.
3. Стадия `segment` через LLM (с тестами на детерминированности парсинга).
4. Стадии `cut` + `thumbnail` через ffmpeg.
5. Стадия `metadata` через LLM.
6. Стадия `upload` через YouTube API + OAuth.
7. UI: список + создание + детали + WebSocket-прогресс.
8. Retry с шага, обработка частичных сбоев.
9. E2E smoke-тест.

## Открытые вопросы (требуют решения до писания implementation plan)

1. **Whisper**: `faster-whisper` локально (ресурсы CPU/GPU, бесплатно) vs OpenAI Whisper API ($0.006/мин, проще)?
2. **LLM-провайдер**: Anthropic Claude (предпочтителен из-за prompt caching на длинных транскриптах) vs OpenAI GPT-4o?
3. **YouTube OAuth**: один общий командный аккаунт vs per-user?
4. **Хранение OAuth refresh-токена**: Fernet-шифрование в БД с ключом из env (предлагается по умолчанию) vs внешний secret manager?

## Риски

- **YouTube Data API квоты** — загрузка стоит 1600 единиц/видео из 10 000/сутки по умолчанию. На 6 загрузок в день уже потолок. Нужен запрос на повышение квоты или повременная очередь загрузок.
- **Whisper-стоимость/скорость** — на длинном видео локальный Whisper медленный без GPU, API — заметные расходы при потоке.
- **LLM-стоимость сегментирования** — длинные транскрипты дорого. Prompt caching обязателен.
- **ffmpeg `-c copy` и keyframe-границы** — без перекодирования резать по точным секундам нельзя; сегмент может начаться/закончиться на keyframe. Решается либо лёгким перекодированием границ, либо округлением границ к ближайшему keyframe (точность ±2 сек).
