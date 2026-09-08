"use client";
import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  Cue,
  listRevisions,
  MediaRevision,
  patchSegment,
  ReviewState,
  Segment,
  Selection,
} from "@/lib/api";
import { formatDate, formatTime, timedSourceUrl } from "@/lib/format";
import { StatusBadge } from "./StatusBadge";

type SaveState = "idle" | "saving" | "saved" | "error";
function SaveLabel({ state }: { state: SaveState }) {
  return (
    <span
      aria-live="polite"
      className={`text-xs ${state === "error" ? "text-red-700" : state === "saved" ? "text-emerald-700" : "text-neutral-500"}`}
    >
      {state === "saving"
        ? "Сохраняется…"
        : state === "saved"
          ? "Сохранено"
          : state === "error"
            ? "Ошибка сохранения"
            : ""}
    </span>
  );
}
function validationReason(segment: Segment): string | null {
  const technical = segment.validation?.technical as
    { ok?: boolean; reason?: string } | undefined;
  const narrative = segment.validation?.narrative as
    { ok?: boolean; reason?: string } | undefined;
  return technical?.ok === false
    ? (technical.reason ?? "Техническая проверка не пройдена")
    : narrative?.ok === false
      ? (narrative.reason ?? "Повествование требует проверки")
      : segment.rejection_reason;
}

export function SegmentCard({
  segment,
  cues,
  sourceDuration,
  sourceUrl,
  mediaDeferred,
  onSeek,
  onChange,
}: {
  segment: Segment;
  cues: Cue[];
  sourceDuration: number | null;
  sourceUrl: string | null;
  mediaDeferred: boolean;
  onSeek: (time: number) => void;
  onChange: (segment: Segment) => void;
}) {
  const serverTitle = segment.yt_title ?? segment.title ?? "";
  const serverDescription = segment.yt_description ?? segment.summary ?? "";
  const serverTags = (segment.yt_tags ?? []).join(", ");
  const [start, setStart] = useState(String(segment.start_sec));
  const [end, setEnd] = useState(String(segment.end_sec));
  const [title, setTitle] = useState(serverTitle);
  const [description, setDescription] = useState(serverDescription);
  const [tags, setTags] = useState(serverTags);
  const [titleDirty, setTitleDirty] = useState(false);
  const [descriptionDirty, setDescriptionDirty] = useState(false);
  const [tagsDirty, setTagsDirty] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [mutation, setMutation] = useState(false);
  const [revisions, setRevisions] = useState<MediaRevision[] | null>(null);
  const [revisionError, setRevisionError] = useState<string | null>(null);
  useEffect(() => {
    setStart(String(segment.start_sec));
    setEnd(String(segment.end_sec));
    setTitle(serverTitle);
    setDescription(serverDescription);
    setTags(serverTags);
    setTitleDirty(false);
    setDescriptionDirty(false);
    setTagsDirty(false);
  }, [segment.current_revision_id]);
  useEffect(() => {
    if (!titleDirty) setTitle(serverTitle);
  }, [serverTitle, titleDirty]);
  useEffect(() => {
    if (!descriptionDirty) setDescription(serverDescription);
  }, [serverDescription, descriptionDirty]);
  useEffect(() => {
    if (!tagsDirty) setTags(serverTags);
  }, [serverTags, tagsDirty]);
  const nearby = useMemo(() => {
    if (!cues.length) return [];
    const hits = cues
      .map((cue, index) => ({ cue, index }))
      .filter(
        ({ cue }) =>
          cue.end >= segment.start_sec && cue.start <= segment.end_sec,
      );
    if (!hits.length) return [];
    const lo = Math.max(0, hits[0].index - 2),
      hi = Math.min(cues.length, hits[hits.length - 1].index + 3);
    return cues.slice(lo, hi);
  }, [cues, segment.start_sec, segment.end_sec]);
  const duration = Number(end) - Number(start);
  const boundsValid =
    Number.isFinite(duration) &&
    duration >= 90 &&
    duration <= 1500 &&
    Number(start) >= 0 &&
    (sourceDuration == null || Number(end) <= sourceDuration);
  async function mutate(
    body: Parameters<typeof patchSegment>[1],
    kind: "save" | "action" = "action",
  ): Promise<Segment | null> {
    if (mutation) return null;
    setMutation(true);
    setError(null);
    if (kind === "save") setSaveState("saving");
    try {
      const updated = await patchSegment(segment.id, body);
      onChange(updated);
      if (kind === "save") setSaveState("saved");
      return updated;
    } catch (e) {
      if (kind === "save") setSaveState("error");
      setError(
        e instanceof ApiError && e.status === 409
          ? "Клип уже изменён в другой вкладке. Данные обновятся автоматически; повторите правку на новой версии."
          : e instanceof Error
            ? e.message
            : "Не удалось сохранить изменения",
      );
      return null;
    } finally {
      setMutation(false);
    }
  }
  async function saveBounds() {
    if (!boundsValid) {
      setError(
        "Диапазон должен находиться внутри исходника и длиться от 1:30 до 25:00.",
      );
      setSaveState("error");
      return;
    }
    await mutate(
      {
        expected_revision: segment.revision,
        start_sec: Number(start),
        end_sec: Number(end),
      },
      "save",
    );
  }
  async function saveMetadata() {
    const updated = await mutate(
      {
        expected_revision: segment.revision,
        yt_title: title.trim(),
        yt_description: description.trim(),
        yt_tags: tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        metadata_needs_review: false,
      },
      "save",
    );
    if (updated) {
      setTitleDirty(false);
      setDescriptionDirty(false);
      setTagsDirty(false);
    }
  }
  async function showRevisions() {
    if (revisions) {
      setRevisions(null);
      return;
    }
    setRevisionError(null);
    try {
      setRevisions((await listRevisions(segment.id)).items);
    } catch (e) {
      setRevisionError(
        e instanceof Error ? e.message : "Не удалось загрузить историю",
      );
    }
  }
  const reason = validationReason(segment);
  const episodeSourceUrl = timedSourceUrl(sourceUrl, segment.start_sec);
  return (
    <article
      id={`segment-${segment.id}`}
      tabIndex={-1}
      aria-labelledby={`segment-heading-${segment.id}`}
      className="panel overflow-hidden"
    >
      <header className="timeline-rule flex flex-col gap-3 border-b p-4 pb-5 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-blue-700">
            Эпизод {segment.index + 1} · ревизия {segment.media_revision ?? "—"}
          </p>
          <h3
            id={`segment-heading-${segment.id}`}
            className="mt-1 text-lg font-semibold"
          >
            {segment.title ?? segment.yt_title ?? "Без названия"}
          </h3>
          <p className="mt-1 font-mono text-sm text-neutral-500">
            {formatTime(segment.start_sec)}–{formatTime(segment.end_sec)} ·{" "}
            {mediaDeferred ? "план " : ""}
            {formatTime(segment.actual_duration_sec ?? segment.end_sec - segment.start_sec)}
          </p>
          {mediaDeferred && (
            <p className="mt-1 text-xs text-neutral-500">
              По таймкодам расшифровки; фактическая длительность MP4 не измерена.
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge status={segment.status} />
          <StatusBadge status={segment.selection} />
          <StatusBadge status={segment.review_state} />
        </div>
      </header>
      <div className="grid gap-0 xl:grid-cols-[minmax(0,1.55fr)_minmax(21rem,.85fr)]">
        <div className="space-y-5 p-4 sm:p-6 xl:border-r">
          <dl
            className="grid grid-cols-2 gap-3 rounded-lg border bg-neutral-50 p-3 text-xs"
            aria-label="Проверки эпизода"
          >
            <div>
              <dt className="text-neutral-500">Оценка ИИ</dt>
              <dd className="mt-1 font-semibold">
                {segment.decision === "publish" ? "Рекомендован" : "Отклонён"}
              </dd>
            </div>
            {mediaDeferred ? (
              <div>
                <dt className="text-neutral-500">Цельность повествования</dt>
                <dd className="mt-1 font-semibold">
                  {(segment.validation?.narrative as { ok?: boolean } | undefined)?.ok === false
                    ? "Требует правки"
                    : "Проверена моделью"}
                </dd>
              </div>
            ) : (
              <>
                <div>
                  <dt className="text-neutral-500">Создание MP4</dt>
                  <dd className="mt-1 font-semibold">
                    {segment.stages?.render === "succeeded"
                      ? "Файл создан"
                      : segment.stages?.render === "failed"
                        ? "Ошибка"
                        : segment.stages?.render === "running"
                          ? "Создаётся…"
                          : "Ожидает"}
                  </dd>
                </div>
                <div>
                  <dt className="text-neutral-500">Техническая проверка</dt>
                  <dd className="mt-1 font-semibold">
                    {segment.stages?.verify === "succeeded"
                      ? "Пройдена"
                      : segment.stages?.verify === "failed"
                        ? "Ошибка"
                        : "Ожидает"}
                  </dd>
                </div>
              </>
            )}
            <div>
              <dt className="text-neutral-500">Проверка пользователем</dt>
              <dd className="mt-1 font-semibold">
                {segment.review_state === "accepted"
                  ? "Подтверждено"
                  : segment.review_state === "rejected"
                    ? "Отклонено"
                    : "Не проверено"}
              </dd>
            </div>
          </dl>
          {mediaDeferred ? null : segment.playback_url ? (
            <video
              controls
              preload="metadata"
              src={segment.playback_url}
              className="aspect-video w-full rounded-lg bg-black"
            />
          ) : (
            <div className="flex aspect-video items-center justify-center rounded-lg bg-neutral-900 px-5 text-center text-sm text-neutral-300">
              Клип появится после точного рендера и технической проверки.
            </div>
          )}
          <div>
            <h4 className="text-sm font-semibold">О чём эпизод</h4>
            <p className="mt-2 text-sm leading-6 text-neutral-600">
              {segment.summary ?? "Описание ещё готовится."}
            </p>
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <span className="status-badge status-neutral">
                Релевантность {segment.relevance}
              </span>
              <span className="status-badge status-neutral">
                Боль {segment.pain}
              </span>
              <span className="status-badge status-neutral">
                Хук {segment.hook}
              </span>
              <span className="status-badge status-neutral">
                Ценность {segment.value}
              </span>
            </div>
          </div>
          {reason && <p className="inline-error">{reason}</p>}
          {segment.error && <p className="inline-error">{segment.error}</p>}
          <div>
            <div className="flex items-center justify-between gap-3">
              <h4 className="text-sm font-semibold">Контекст расшифровки</h4>
              {mediaDeferred && episodeSourceUrl ? (
                <a
                  className="button-ghost min-h-0 px-2 py-1 text-xs"
                  href={episodeSourceUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  Открыть на YouTube
                </a>
              ) : (
                <button
                  type="button"
                  className="button-ghost min-h-0 px-2 py-1 text-xs"
                  onClick={() => onSeek(segment.start_sec)}
                >
                  Смотреть с начала
                </button>
              )}
            </div>
            {nearby.length ? (
              <ol className="mt-2 max-h-72 space-y-1 overflow-y-auto rounded-lg border p-2">
                {nearby.map((cue) => (
                  <li
                    key={`${cue.start}-${cue.end}`}
                    className={
                      cue.end >= segment.start_sec &&
                      cue.start <= segment.end_sec
                        ? "bg-blue-50"
                        : "text-neutral-500"
                    }
                  >
                    {mediaDeferred && timedSourceUrl(sourceUrl, cue.start) ? (
                    <a
                      href={timedSourceUrl(sourceUrl, cue.start) ?? undefined}
                      target="_blank"
                      rel="noreferrer"
                      className="block w-full cursor-pointer rounded p-2 text-left text-sm hover:bg-blue-100"
                    >
                      <span className="mr-2 font-mono text-xs text-blue-700">
                        {formatTime(cue.start)}
                      </span>
                      {cue.text}
                    </a>
                    ) : (
                    <button
                      type="button"
                      onClick={() => onSeek(cue.start)}
                      className="w-full cursor-pointer rounded p-2 text-left text-sm hover:bg-blue-100"
                    >
                      <span className="mr-2 font-mono text-xs text-blue-700">
                        {formatTime(cue.start)}
                      </span>
                      {cue.text}
                    </button>
                    )}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mt-2 text-sm text-neutral-500">
                Расшифровка для этой версии пока недоступна.
              </p>
            )}
          </div>
        </div>
        <aside className="space-y-6 bg-neutral-50/60 p-4 sm:p-6">
          <section>
            <h4 className="text-sm font-semibold">Границы эпизода</h4>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <div>
                <label
                  htmlFor={`start-${segment.id}`}
                  className="text-xs font-semibold text-neutral-600"
                >
                  Начало, сек
                </label>
                <input
                  id={`start-${segment.id}`}
                  type="number"
                  min={0}
                  step="0.001"
                  value={start}
                  onChange={(e) => {
                    setStart(e.target.value);
                    setSaveState("idle");
                  }}
                  className="field mt-1 font-mono"
                  aria-invalid={!boundsValid}
                />
                {mediaDeferred ? (
                  <a
                    className="mt-1 inline-block text-xs font-semibold text-blue-700 hover:underline"
                    href={timedSourceUrl(sourceUrl, Number(start) || 0) ?? undefined}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Открыть таймкод
                  </a>
                ) : (
                  <button
                    type="button"
                    className="mt-1 text-xs font-semibold text-blue-700 hover:underline"
                    onClick={() => onSeek(Number(start) || 0)}
                  >
                    Проверить
                  </button>
                )}
              </div>
              <div>
                <label
                  htmlFor={`end-${segment.id}`}
                  className="text-xs font-semibold text-neutral-600"
                >
                  Конец, сек
                </label>
                <input
                  id={`end-${segment.id}`}
                  type="number"
                  min={0}
                  step="0.001"
                  value={end}
                  onChange={(e) => {
                    setEnd(e.target.value);
                    setSaveState("idle");
                  }}
                  className="field mt-1 font-mono"
                  aria-invalid={!boundsValid}
                />
                {mediaDeferred ? (
                  <a
                    className="mt-1 inline-block text-xs font-semibold text-blue-700 hover:underline"
                    href={timedSourceUrl(sourceUrl, Number(end) || 0) ?? undefined}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Открыть таймкод
                  </a>
                ) : (
                  <button
                    type="button"
                    className="mt-1 text-xs font-semibold text-blue-700 hover:underline"
                    onClick={() => onSeek(Number(end) || 0)}
                  >
                    Проверить
                  </button>
                )}
              </div>
            </div>
            <p
              className={`mt-2 text-xs ${boundsValid ? "text-neutral-500" : "text-red-700"}`}
            >
              Длительность: {formatTime(duration)} · допустимо 1:30–25:00
            </p>
            <div className="mt-3 flex items-center gap-3">
              <button
                type="button"
                onClick={saveBounds}
                className="button-secondary"
                disabled={mutation || !boundsValid}
              >
                Сохранить границы
              </button>
              <SaveLabel state={saveState} />
            </div>
          </section>
          <section className="border-t pt-5">
            <h4 className="text-sm font-semibold">Выбор и проверка</h4>
            <div
              className="mt-3 grid grid-cols-3 gap-2"
              role="group"
              aria-label="Включение клипа"
            >
              {(["auto", "include", "exclude"] as Selection[]).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`min-h-11 rounded-lg border px-2 text-xs font-semibold ${segment.selection === value ? "border-blue-600 bg-blue-50 text-blue-800" : "bg-white hover:bg-neutral-100"}`}
                  aria-pressed={segment.selection === value}
                  disabled={mutation}
                  onClick={() =>
                    mutate({
                      expected_revision: segment.revision,
                      selection: value,
                    })
                  }
                >
                  {value === "auto"
                    ? "Решение ИИ"
                    : value === "include"
                      ? "Включить"
                      : "Исключить"}
                </button>
              ))}
            </div>
            <div
              className="mt-2 grid grid-cols-3 gap-2"
              role="group"
              aria-label="Пользовательская проверка"
            >
              {(["unreviewed", "accepted", "rejected"] as ReviewState[]).map(
                (value) => (
                  <button
                    key={value}
                    type="button"
                    className={`min-h-11 rounded-lg border px-2 text-xs font-semibold ${segment.review_state === value ? "border-blue-600 bg-blue-50 text-blue-800" : "bg-white hover:bg-neutral-100"}`}
                    aria-pressed={segment.review_state === value}
                    disabled={mutation}
                    onClick={() =>
                      mutate({
                        expected_revision: segment.revision,
                        review_state: value,
                      })
                    }
                  >
                    {value === "unreviewed"
                      ? "Не проверен"
                      : value === "accepted"
                        ? "Подтвердить"
                        : "Отклонить"}
                  </button>
                ),
              )}
            </div>
          </section>
          {!mediaDeferred && (
          <section className="border-t pt-5">
            <h4 className="text-sm font-semibold">Метаданные</h4>
            {segment.metadata_needs_review && (
              <p className="mt-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">
                После изменения границ проверьте ручные метаданные.
              </p>
            )}
            <label
              htmlFor={`title-${segment.id}`}
              className="mt-3 block text-xs font-semibold text-neutral-600"
            >
              Заголовок
            </label>
            <input
              id={`title-${segment.id}`}
              value={title}
              maxLength={100}
              onChange={(e) => {
                setTitle(e.target.value);
                setTitleDirty(true);
                setSaveState("idle");
              }}
              className="field mt-1"
            />
            <label
              htmlFor={`description-${segment.id}`}
              className="mt-3 block text-xs font-semibold text-neutral-600"
            >
              Описание
            </label>
            <textarea
              id={`description-${segment.id}`}
              value={description}
              onChange={(e) => {
                setDescription(e.target.value);
                setDescriptionDirty(true);
                setSaveState("idle");
              }}
              rows={5}
              className="field mt-1 resize-none"
            />
            <label
              htmlFor={`tags-${segment.id}`}
              className="mt-3 block text-xs font-semibold text-neutral-600"
            >
              Теги через запятую
            </label>
            <input
              id={`tags-${segment.id}`}
              value={tags}
              onChange={(e) => {
                setTags(e.target.value);
                setTagsDirty(true);
                setSaveState("idle");
              }}
              className="field mt-1"
            />
            <div className="mt-3 flex items-center gap-3">
              <button
                type="button"
                onClick={saveMetadata}
                className="button-secondary"
                disabled={mutation}
              >
                Сохранить метаданные
              </button>
              <SaveLabel state={saveState} />
            </div>
          </section>
          )}
          {!mediaDeferred && segment.thumbnails.length > 0 && (
            <section className="border-t pt-5">
              <h4 className="text-sm font-semibold">Превью</h4>
              <div className="mt-2 grid grid-cols-3 gap-2">
                {segment.thumbnails.map((thumb) => (
                  <button
                    key={thumb.asset_id}
                    type="button"
                    disabled={mutation}
                    aria-label={`Выбрать превью ${thumb.position_idx + 1}`}
                    aria-pressed={
                      segment.selected_thumbnail_id === thumb.asset_id
                    }
                    onClick={() =>
                      mutate({
                        expected_revision: segment.revision,
                        selected_thumbnail_id: thumb.asset_id,
                      })
                    }
                    className={`cursor-pointer overflow-hidden rounded-lg border-2 ${segment.selected_thumbnail_id === thumb.asset_id ? "border-blue-600" : "border-transparent opacity-70 hover:opacity-100"}`}
                  >
                    <img
                      src={thumb.url}
                      alt=""
                      className="aspect-video w-full object-cover"
                    />
                  </button>
                ))}
              </div>
            </section>
          )}
          <section className="border-t pt-5">
            <div className="flex flex-wrap gap-2">
              {!mediaDeferred && segment.video_download_url && (
                <a href={segment.video_download_url} className="button-primary">
                  Скачать MP4
                </a>
              )}
              <button
                type="button"
                onClick={showRevisions}
                className="button-secondary"
              >
                {revisions ? "Скрыть версии" : "Предыдущие версии"}
              </button>
            </div>
            {revisionError && (
              <p role="alert" className="mt-2 text-xs text-red-700">
                {revisionError}
              </p>
            )}
            {revisions && (
              <ol className="mt-3 space-y-2">
                {revisions.map((revision) => (
                  <li
                    key={revision.id}
                    className="rounded-lg border bg-white p-3 text-xs"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold">
                        Ревизия {revision.number}
                      </span>
                      <StatusBadge status={revision.status} />
                    </div>
                    <p className="mt-1 text-neutral-500">
                      {formatTime(revision.start_sec)}–
                      {formatTime(revision.end_sec)} ·{" "}
                      {formatDate(revision.created_at)}
                    </p>
                    {revision.video_download_url && (
                      <a
                        className="mt-2 inline-block font-semibold text-blue-700 hover:underline"
                        href={revision.video_download_url}
                      >
                        Скачать эту версию
                      </a>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </section>
          {error && (
            <p role="alert" className="inline-error">
              {error}
            </p>
          )}
        </aside>
      </div>
    </article>
  );
}
