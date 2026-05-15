# Video Slicer — Phase 2: Real Pipeline (без YouTube upload)

**Дата:** 2026-05-15
**Базовый коммит:** `phase-1-skeleton`
**Цель:** Заменить 6 заглушек стадий из Phase 1 на работающую реализацию, чтобы из YouTube-URL получить готовые к публикации горизонтальные клипы 5–15 минут с превью и черновиком метаданных. Загрузка в YouTube вынесена в Phase 3.

---

## 1. Scope

**Входит:**
- `fetch` — скачивание видео и субтитров (`yt-dlp`) с сохранением в MinIO.
- `transcribe` — VTT-парсер для авто-субтитров YouTube; локальный `faster-whisper` (модель `base`) как fallback.
- `segment` — вызов `polza.ai` (`google/gemini-3.1-flash-lite`, JSON-schema) для разбиения транскрипта на 5–15-минутные смысловые сегменты.
- `cut` — `ffmpeg` stream-copy (`-c copy`), быстрая нарезка по ключевым кадрам.
- `thumbnail` — 3 кадра на сегмент (5% / 50% / 95% от длительности).
- `metadata` — второй LLM-вызов на сегмент: `{yt_title, yt_description, yt_tags}` пишется в существующую таблицу `uploads`.
- UI: на странице `/jobs/[id]` после `succeeded` — список сегментов с превью-пикером, редактированием `yt_title`/`yt_description`/`yt_tags`, кнопкой «скачать клип».

**Не входит:**
- YouTube OAuth и реальный upload (Phase 3).
- WebSocket-прогресс (остаётся polling из Phase 1).
- Retry-эндпоинт `/jobs/{id}/retry?from=<stage>` (Phase 3 или позже).
- Загрузка через UI пользовательских mp4-файлов (используем только URL-источник — `source_type="url"`).

---

## 2. Архитектура изменений

| Слой | Что меняется |
|------|---------------|
| **worker** | Каждая стадия из стабов превращается в реальный модуль. Добавляются: `services/storage.py` (MinIO через `boto3`), `services/llm.py` (OpenAI-compatible клиент к polza.ai), `services/vtt.py` (парсер субтитров), `services/whisper.py` (faster-whisper обёртка), `services/ffmpeg.py` (cut/thumbnail/audio_extract). |
| **api** | Новый роутер `segments`: `GET /segments/{id}`, `PATCH /segments/{id}` (правка метаданных и выбор превью), `GET /segments/{id}/download`, `GET /segments/{id}/thumbnails`. Используем уже существующий `boto3` для presigned URL. |
| **web** | Новые компоненты на `/jobs/[id]`: `SegmentList`, `SegmentCard`, `ThumbnailPicker`, `MetadataEditor`. Расширяем `lib/api.ts` соответствующими функциями. |
| **infra** | `worker` Dockerfile: `apt-get install -y ffmpeg`; pip: `yt-dlp`, `faster-whisper`, `openai`, `boto3`. `api` Dockerfile: pip `boto3`. Новый volume `whisper_models` для кэша моделей. |

---

## 3. Стадии (детально)

### 3.1 `fetch`

**Вход:** `Job` с `source_type="url"`, `source_url=<youtube_url>`.
**Шаги:**
1. Скачать через `yt-dlp -f "bestvideo[height<=1080]+bestaudio/best" --merge-output-format mp4 -o tmp/source.%(ext)s <url>`.
2. Отдельно: `yt-dlp --skip-download --write-auto-subs --write-subs --sub-lang ru --sub-format vtt -o tmp/subs <url>`.
3. Загрузить в MinIO:
   - `{job_id}/source.mp4` → `Asset(kind=source_video, mime="video/mp4")`
   - `{job_id}/subs.vtt` (если файл создан) → `Asset(kind=source_subs, mime="text/vtt")`
4. Удалить `tmp/`.

**Обработка ошибок:**
- yt-dlp ненулевой код → `Job.status=failed`, `Job.error=<stderr_tail>`, прерываем chain.
- Сабов нет → НЕ ошибка (просто не создаём `source_subs` asset).

**Прогресс:** `publish_progress(job_id, Stage.FETCH, "running")` в начале, после загрузки в MinIO — `"done"`.

### 3.2 `transcribe`

**Вход:** Job с уже скачанным `source_video` (и опционально `source_subs`).
**Логика:**
1. Если `Asset(kind=source_subs)` существует → загружаем VTT из MinIO, парсим в нормализованный `transcript.json`:
   ```json
   [{"start": 0.0, "end": 3.4, "text": "..."}, ...]
   ```
2. Иначе:
   - `ffmpeg -i source.mp4 -vn -acodec libmp3lame -ar 16000 audio.mp3` → MinIO как `Asset(kind=source_audio)`.
   - `faster-whisper` (модель `base`, language=`ru`, beam=1) на `audio.mp3` → тот же формат `transcript.json`.
3. Сохранить `transcript.json` в MinIO → `Asset(kind=transcript, mime="application/json")`.

**Whisper в Docker:** модель загружается лениво при первом запуске в volume `/whisper-cache`. На worker image добавляется `WHISPER_CACHE_DIR=/whisper-cache`.

**Прогресс:** `running` → `done`. Внутри Whisper можно публиковать процент по сегментам, но это nice-to-have, не обязательно для MVP.

### 3.3 `segment`

**Вход:** `Asset(kind=transcript)`, длительность видео (из `ffprobe` или метаданных yt-dlp).
**Шаги:**
1. Сжать транскрипт в текст с таймкодами:
   ```
   [00:00] Тут начало...
   [00:14] Дальше речь о...
   ...
   ```
   Каждая строка — реплика длительностью до 30 сек (склеиваем мелкие).
2. Системный промпт:
   > Ты — редактор YouTube-канала. Разбей транскрипт длинного видео на 4–10 смысловых сегментов длительностью 5–15 минут каждый. Каждый сегмент — самостоятельная мысль или часть аргументации. Соседние сегменты не пересекаются и идут по порядку. Верни строго JSON по схеме.
3. JSON-schema (`response_format=json_schema`):
   ```json
   {
     "segments": [
       {"start": <seconds>, "end": <seconds>, "title": "...", "summary": "..."}
     ]
   }
   ```
4. Валидация:
   - Каждый `end - start ∈ [240, 1200]` (4–20 мин, с допуском).
   - `segments[i].end <= segments[i+1].start + 1.0`.
   - Общая длительность ≤ длине видео + 5.0.
   - Если валидация падает — один retry с уточняющим промптом. После второго провала — `Job.failed`.
5. Создать `Segment` записи (`index`, `start_sec`, `end_sec`, `title`, `summary`, `transcript_excerpt`=первые 500 символов соответствующей части транскрипта).

**Модель:** `google/gemini-3.1-flash-lite` (1M контекст, `structured_outputs=true`, ~22₽/M вход / 137/M выход). Переменная `POLZA_MODEL` в `.env`, легко поменять.

### 3.4 `cut`

**Шаги для каждого `Segment`:**
1. Скачать `source.mp4` из MinIO в `/tmp/{seg_id}/source.mp4` (один раз на job, кэшируем между сегментами в worker process).
2. `ffmpeg -ss <start> -to <end> -i source.mp4 -c copy -avoid_negative_ts make_zero -movflags +faststart /tmp/{seg_id}/segment.mp4`.
3. Загрузить в MinIO как `Asset(kind=segment_video, segment_id=<seg_id>, mime="video/mp4")`, ключ `{job_id}/segments/{seg_id}.mp4`.
4. `Segment.status = cut`.

**После всех сегментов:** удалить `/tmp/{seg_id}/source.mp4` (не нужен дальше — есть в MinIO).

### 3.5 `thumbnail`

**Шаги для каждого `Segment`:**
1. Вычислить 3 смещения: `t_i = start + (end - start) * [0.05, 0.50, 0.95]`.
2. Для каждого: `ffmpeg -ss <t_i> -i source.mp4 -frames:v 1 -q:v 2 /tmp/{seg_id}/thumb_{i}.jpg`.
3. Загрузить каждое в MinIO `{job_id}/thumbnails/{seg_id}/{i}.jpg` → `Asset(kind=thumbnail, segment_id=<seg_id>, position_idx=i, mime="image/jpeg")`.
4. Если у `Segment` ещё нет `Upload` записи — создать с `selected_thumbnail_id = asset_id` для `position_idx=1` (середина по умолчанию). Иначе обновить, если был `null`.
5. `Segment.status = thumbnail_ready`.

### 3.6 `metadata`

**Шаги для каждого `Segment`:**
1. Собрать контекст: `segment.title`, `segment.summary`, +часть `transcript.json` в окне `[start, end]` (склеенный текст, без таймкодов).
2. LLM-вызов (та же модель):
   - System: «Ты пишешь YouTube-метаданные для горизонтального клипа. Заголовок ≤60 символов, цепляющий, без кликбейта. Описание 200–500 слов: что в видео, ключевые мысли, призыв к действию. 5–10 тегов на русском.»
   - Schema: `{title: string, description: string, tags: string[]}`.
3. Создать или обновить `Upload` для этого сегмента: `youtube_title`, `youtube_description`, `tags`. `status=pending` (запись существует ради черновика; реальная загрузка в Phase 3).
4. `Segment.status = metadata_ready`.

После всех сегментов: `finalize` (как в Phase 1) → `Job.status=succeeded`, `current_stage=done`.

---

## 4. Изменения в модели данных

**Что уже есть в Phase 1 и используется как есть:**
- `Asset.kind` — расширяем enum, см. ниже.
- `Asset.segment_id` — используется для `segment_video` и `thumbnail`.
- `Upload.youtube_title/description/tags` — используем как черновик метаданных Phase 2.
- `Segment.title/summary/transcript_excerpt` — заполняются в `segment`.

**Что нужно изменить (новая Alembic-миграция `0002`):**

1. `Asset.kind` enum расширяется. Было: `source`, `transcript`, `segment`, `thumbnail`. Становится:
   - `source_video`, `source_subs`, `source_audio`, `transcript`, `segment_video`, `thumbnail`.
   - Миграция: `ALTER TYPE asset_kind ADD VALUE ...` (несколько ADD VALUE подряд) + backfill отсутствующих (но в БД пока тестовые job-ы — `DROP TYPE CASCADE` и пересоздание тоже допустимо для dev-окружения).
2. `Asset.position_idx` — новое поле `INTEGER NULL` (для `thumbnail` хранит 0/1/2).
3. `Segment.selected_thumbnail_id` — новое поле `UUID NULL`, FK на `assets.id`, `ON DELETE SET NULL`.

**Что НЕ меняется:**
- `YouTubeAccount` таблица не используется в Phase 2.
- `Upload.status` остаётся `pending` после `metadata`-стадии (загрузки не было).

---

## 5. API endpoints (новые)

| Метод | Путь | Назначение |
|-------|------|------------|
| `GET` | `/jobs/{id}/segments` | Список сегментов с превью URL и метаданными для UI. |
| `GET` | `/segments/{id}` | Детали одного сегмента (включая `Upload` запись). |
| `PATCH` | `/segments/{id}` | Правка `yt_title`/`yt_description`/`yt_tags` или смена `selected_thumbnail_id`. |
| `GET` | `/segments/{id}/download` | 302-редирект на presigned URL `segment_video` (TTL 1 час). |
| `GET` | `/assets/{id}/url` | Presigned URL для любого asset (для thumbnail-картинок в UI). |

**Аутентификация:** в Phase 2 остаётся header `X-User: <email>` (как в Phase 1). Полноценный auth — Phase 3+.

---

## 6. UI изменения

**`/jobs/[id]` после `status: succeeded`:**

```
Задача abc12345
Источник: youtube.com/watch?v=...
Статус: succeeded · 7 сегментов

┌────────────────────────────────────────────┐
│ Сегмент 1 · 00:00 – 07:32                  │
│  [thumb0]  [thumb1✓]  [thumb2]             │
│  Заголовок: [_________________________]    │
│  Описание:  [_________________________]    │
│              [_________________________]    │
│  Теги:      [#тег1] [#тег2] [+]            │
│  [скачать клип]                            │
└────────────────────────────────────────────┘
```

- Превью-пикер: клик по миниатюре → `PATCH /segments/{id}` с `selected_thumbnail_id`.
- Поля метаданных: blur/Enter → debounced `PATCH`.
- «Скачать клип» → `<a href="/segments/{id}/download">`.

Список выше можно прокрутить, навигации по сегментам нет — всё на одной странице.

**Новые компоненты:** `SegmentList`, `SegmentCard`, `ThumbnailPicker`, `MetadataEditor` (отдельные файлы в `web/components/`).

---

## 7. Зависимости и Docker

**worker Dockerfile:**
- `apt-get install -y ffmpeg`
- Python: `yt-dlp`, `faster-whisper`, `openai>=1.50`, `boto3`
- ENV: `WHISPER_CACHE_DIR=/whisper-cache`, `WHISPER_MODEL=base`

**api Dockerfile:**
- Python: `boto3` (для presigned URL).

**docker-compose:**
- Volume `whisper_models:/whisper-cache` смонтирован в worker.
- Никаких новых сервисов.

**Переменные окружения (новые):**
- `POLZA_API_KEY`, `POLZA_BASE_URL`, `POLZA_MODEL` — уже в `.env`.
- `MINIO_BUCKET` — уже в `.env`, проверить что бакет создаётся при старте worker (idempotent `create_bucket` через boto3).

---

## 8. LLM-промпты (полный текст)

### 8.1 segment
**System:**
```
Ты — редактор YouTube-канала с многолетним опытом. Тебе дан транскрипт длинного видео
с таймкодами. Разбей его на 4–10 смысловых сегментов длительностью 5–15 минут каждый.

Принципы:
1. Каждый сегмент — самостоятельная мысль, история или часть аргументации,
   которую можно опубликовать как отдельный YouTube-ролик.
2. Соседние сегменты идут по порядку, не пересекаются, покрывают всё видео целиком.
3. Границы — на естественных паузах (смена темы, риторический вопрос, переход).
4. Если видео короче 5 минут — верни один сегмент на всё видео.

Верни строго JSON по схеме. Никакого другого текста.
```

**User template:**
```
Транскрипт (длительность {duration_sec:.1f} сек):

{transcript_lines}
```

**Schema:**
```json
{
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
          "summary": {"type": "string", "maxLength": 500}
        }
      }
    }
  },
  "required": ["segments"]
}
```

### 8.2 metadata
**System:**
```
Ты пишешь метаданные для YouTube-клипа. Видео — горизонтальное (16:9),
длительность 5–15 минут, целевая аудитория — русскоязычная.

Требования:
- Заголовок ≤60 символов, цепляет, не кликбейт.
- Описание 200–500 слов: о чём ролик, ключевые мысли, кому будет полезно.
  Без призывов «подписаться/лайк» — это шаблон.
- Теги: 5–10 шт, на русском, через массив.

Верни строго JSON по схеме.
```

**User template:**
```
Заголовок сегмента: {segment_title}
Краткое содержание: {segment_summary}

Полный текст сегмента:
{segment_transcript}
```

**Schema:**
```json
{
  "type": "object",
  "required": ["title", "description", "tags"],
  "properties": {
    "title": {"type": "string", "maxLength": 60},
    "description": {"type": "string", "maxLength": 2000},
    "tags": {
      "type": "array",
      "minItems": 5,
      "maxItems": 10,
      "items": {"type": "string", "maxLength": 30}
    }
  }
}
```

---

## 9. Обработка ошибок

| Ситуация | Поведение |
|----------|-----------|
| `yt-dlp` не смог скачать видео | `Job.status=failed`, `error=<краткое сообщение>`, пайплайн прерывается. |
| Скачались, но субтитров нет | НЕ ошибка → переходим к Whisper в `transcribe`. |
| Whisper упал (OOM, повреждённое аудио) | `Job.status=failed`. |
| polza.ai вернул не-JSON / валидация провалена | один retry с уточняющим суффиксом в user-промпте; после второго провала — `Job.status=failed`. |
| ffmpeg cut вернул ненулевой код для одного сегмента | `Segment.status=failed`, `error=<stderr>`. Остальные сегменты продолжают. После стадии — если хотя бы один сегмент жив, идём дальше; иначе `Job.status=failed`. |
| metadata-стадия упала для одного сегмента | то же: пометить сегмент, не валить весь Job. |

**Логирование:** транскрипты не пишем в лог. Только `len(text)`, `len(segments)`, первые 200 символов LLM-ответа в DEBUG. API-ключи никогда не логируем.

---

## 10. Тестирование

**Юнит-тесты (worker/tests):**
- `test_vtt_parser.py` — парсер на фикстуре VTT (русский, английский, многострочные cue).
- `test_segment_validator.py` — валидация LLM-ответа (ок / overlap / out-of-range / пустой массив).
- `test_thumbnail_offsets.py` — расчёт смещений (граничные случаи: 0с, 1с, час).
- `test_llm_client.py` — мокаем `httpx` и проверяем структуру запроса к polza.ai.

**Интеграционные:**
- `test_ffmpeg_cut.py` — на фикстуре 30-сек mp4 (генерируется через ffmpeg `testsrc`) режем на 2 куска, проверяем длительность через `ffprobe`.
- `test_whisper_fallback.py` — на коротком (5-сек) синтетическом аудио, мокаем faster-whisper.

**E2E smoke (Task в конце Phase 2):**
- На видео `https://www.youtube.com/watch?v=7MaCttnXc4g` — полный прогон, проверка что `succeeded` и есть ≥3 сегмента с заполненными метаданными.

API-тесты не растут сильно — добавляем 4 теста (`GET /jobs/{id}/segments`, `PATCH /segments/{id}` x2 (метаданные и thumbnail-выбор), `GET /segments/{id}/download`).

---

## 11. Безопасность и расходы

**API-ключ polza.ai:**
- Только в `.env` (gitignored) и worker env.
- В коде читаем через `pydantic-settings`.
- В логах — никогда. В тестах — мокаем клиент.

**MinIO:**
- В Phase 2 бакет приватный. Доступ к клипам/превью только через presigned URL с TTL 1 час.
- Локальная разработка: дефолтный логин `minioadmin/minioadmin` — для prod-фаз заменим.

**Бюджет на одно видео (~часовое):**
- `segment`: вход ~15K токенов, выход ~2K → ~22*0.015 + 137*0.002 ≈ 0.6₽
- `metadata`: 7 сегментов × (вход ~3K, выход ~0.7K) → 7 × (22*0.003 + 137*0.0007) ≈ 1.1₽
- **Итого ≤ 2₽ на видео.**

---

## 12. Open questions (откладываются до Phase 3+)

- YouTube OAuth flow (refresh-token storage, redirect URI, expiry handling).
- Реальная стадия `upload` через YouTube Data API v3 + retries на 429/5xx.
- Шифрование `youtube_accounts.refresh_token` (сейчас просто `LargeBinary`).
- WebSocket-прогресс вместо polling.
- Retry-эндпоинт `/jobs/{id}/retry?from=<stage>` для повторного прогона с конкретной стадии.
- Поддержка `source_type="file"` (multipart-загрузка пользовательского mp4 в MinIO в стадии fetch).
