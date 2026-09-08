"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  createJobFromFile,
  createJobFromUrl,
  getReadiness,
  Readiness,
} from "@/lib/api";
import { newIdempotencyKey } from "@/lib/format";
const MAX_BYTES = 10_000_000_000;
export function NewJobForm() {
  const router = useRouter();
  const fileInput = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const logicalRequest = useRef<{ fingerprint: string; key: string } | null>(
    null,
  );
  const [readinessAttempt, setReadinessAttempt] = useState(0);
  const [mode, setMode] = useState<"url" | "file">("url");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [topic, setTopic] = useState("");
  const [audience, setAudience] = useState("");
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [readinessError, setReadinessError] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    getReadiness(ctrl.signal)
      .then((state) => {
        setReadiness(state);
        setReadinessError(false);
      })
      .catch((e) => {
        if (e.name !== "AbortError") setReadinessError(true);
      });
    return () => ctrl.abort();
  }, [readinessAttempt]);
  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    if (mode === "url" && !/^https?:\/\//i.test(url)) {
      setError("Укажите полную ссылку YouTube, начиная с https://");
      return;
    }
    if (mode === "file" && !file) {
      setError("Выберите видеофайл");
      fileInput.current?.focus();
      return;
    }
    if (file && file.size > MAX_BYTES) {
      setError("Файл больше 10 ГБ. Выберите файл меньшего размера.");
      return;
    }
    setSubmitting(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const fingerprint = JSON.stringify([
        mode,
        url.trim(),
        file?.name,
        file?.size,
        file?.lastModified,
        topic.trim(),
        audience.trim(),
      ]);
      if (logicalRequest.current?.fingerprint !== fingerprint) {
        logicalRequest.current = { fingerprint, key: newIdempotencyKey() };
      }
      const key = logicalRequest.current.key;
      const job =
        mode === "url"
          ? await createJobFromUrl(
              url.trim(),
              topic.trim(),
              audience.trim(),
              key,
            )
          : await createJobFromFile(
              file!,
              topic.trim(),
              audience.trim(),
              key,
              setUploadProgress,
              ctrl.signal,
            );
      router.push(`/jobs/${job.id}`);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError")
        setError("Загрузка отменена. При повторе она начнётся заново.");
      else
        setError(e instanceof Error ? e.message : "Не удалось создать анализ");
    } finally {
      setSubmitting(false);
      abortRef.current = null;
    }
  }
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <form noValidate onSubmit={onSubmit} className="panel-pad space-y-5">
        <fieldset disabled={submitting}>
          <legend className="text-sm font-semibold">Источник</legend>
          <div className="mt-2 flex flex-wrap gap-2">
            <label
              className={`button-secondary ${mode === "url" ? "border-blue-600 bg-blue-50" : ""}`}
            >
              <input
                type="radio"
                name="source-mode"
                value="url"
                checked={mode === "url"}
                onChange={() => {
                  setMode("url");
                  setFile(null);
                  setUploadProgress(null);
                  setError(null);
                }}
                className="mr-2"
              />
              Ссылка YouTube
            </label>
            <label
              className={`button-secondary ${mode === "file" ? "border-blue-600 bg-blue-50" : ""}`}
            >
              <input
                type="radio"
                name="source-mode"
                value="file"
                checked={mode === "file"}
                onChange={() => setMode("file")}
                className="mr-2"
              />
              Файл
            </label>
          </div>
        </fieldset>
        {mode === "url" ? (
          <div>
            <label htmlFor="source-url" className="text-sm font-semibold">
              Ссылка на видео
            </label>
            <input
              key="source-url"
              id="source-url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.youtube.com/watch?v=…"
              className="field mt-2"
              aria-invalid={!!error && !url}
              aria-describedby="source-help"
              disabled={submitting}
            />
            <p id="source-help" className="mt-1.5 text-xs text-neutral-500">
              Сначала приложение анализирует расшифровку. Если видео ещё не
              сохранено, оно не скачивается автоматически — вы сразу получите
              эпизоды и таймкоды.
            </p>
          </div>
        ) : (
          <div>
            <label htmlFor="source-file" className="text-sm font-semibold">
              Видеофайл до 10 ГБ
            </label>
            <input
              key="source-file"
              ref={fileInput}
              id="source-file"
              type="file"
              accept="video/*,.mkv,.mov,.avi,.webm"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setUploadProgress(null);
                setError(null);
              }}
              className="field mt-2 cursor-pointer file:mr-3 file:rounded file:border-0 file:bg-blue-50 file:px-3 file:py-1 file:font-semibold file:text-blue-800"
              disabled={submitting}
            />
            {file && (
              <p className="mt-1.5 text-xs text-neutral-600">
                {file.name} ·{" "}
                {(file.size / 1_000_000).toLocaleString("ru-RU", {
                  maximumFractionDigits: 1,
                })}{" "}
                МБ
              </p>
            )}
          </div>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="topic" className="text-sm font-semibold">
              Тема{" "}
              <span className="font-normal text-neutral-500">
                необязательно
              </span>
            </label>
            <textarea
              id="topic"
              maxLength={4000}
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              rows={3}
              className="field mt-2 resize-none"
              placeholder="Что особенно важно найти"
              disabled={submitting}
            />
          </div>
          <div>
            <label htmlFor="audience" className="text-sm font-semibold">
              Аудитория{" "}
              <span className="font-normal text-neutral-500">
                необязательно
              </span>
            </label>
            <textarea
              id="audience"
              maxLength={4000}
              value={audience}
              onChange={(e) => setAudience(e.target.value)}
              rows={3}
              className="field mt-2 resize-none"
              placeholder="Для кого готовятся клипы"
              disabled={submitting}
            />
          </div>
        </div>
        {uploadProgress !== null && (
          <div aria-live="polite">
            <div className="flex justify-between text-sm">
              <span>Загрузка файла</span>
              <span>{uploadProgress}%</span>
            </div>
            <progress
              value={uploadProgress}
              max={100}
              className="mt-2 h-2 w-full accent-blue-700"
            >
              {uploadProgress}%
            </progress>
            <p className="mt-1 text-xs text-neutral-500">
              После обрыва загрузка начинается заново.
            </p>
          </div>
        )}
        {error && (
          <p role="alert" className="inline-error">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <button
            type="submit"
            className="button-primary min-w-40"
            disabled={submitting || readiness?.ready !== true}
            aria-busy={submitting}
          >
            {submitting
              ? mode === "file"
                ? uploadProgress === 100
                  ? "Проверяется файл…"
                  : "Загружается…"
                : "Создаётся…"
              : "Запустить анализ"}
          </button>
          {submitting && mode === "file" && uploadProgress !== 100 && (
            <button
              type="button"
              className="button-secondary"
              onClick={() => abortRef.current?.abort()}
            >
              Отменить загрузку
            </button>
          )}
        </div>
      </form>
      <aside className="panel-pad h-fit">
        <p className="eyebrow">Правила запуска</p>
        <h2 className="mt-2 font-semibold">Цельные эпизоды</h2>
        <ul className="mt-3 space-y-2 text-sm text-neutral-600">
          <li>Один непрерывный диапазон</li>
          <li>От 1:30 до 25:00 включительно</li>
          <li>Сначала смысловая разметка по расшифровке</li>
          <li>MP4 проверяется только после создания файла</li>
        </ul>
        <dl className="mt-5 space-y-2 border-t pt-4 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="text-neutral-500">Модель</dt>
            <dd className="font-semibold">GPT-6 Astra</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-neutral-500">Reasoning</dt>
            <dd className="font-semibold">
              {readiness?.reasoning ?? "medium"}
            </dd>
          </div>
        </dl>
        <div
          aria-live="polite"
          className={`mt-4 rounded-lg p-3 text-sm ${readiness?.ready ? "bg-emerald-50 text-emerald-800" : readinessError || readiness?.ready === false ? "bg-red-50 text-red-800" : "bg-neutral-100 text-neutral-600"}`}
        >
          {readiness?.ready
            ? "Все службы готовы"
            : readinessError
              ? "Не удалось проверить службы"
              : readiness
                ? "Запуск временно недоступен"
                : "Проверяем готовность…"}
        </div>
        {readiness && readiness.ready === false && (
          <ul className="mt-2 space-y-1 text-xs text-neutral-600">
            {Object.entries(readiness.services)
              .filter(([, value]) => !value.ready)
              .map(([name, value]) => (
                <li key={name}>
                  {name}: {value.detail}
                </li>
              ))}
          </ul>
        )}
        {(readinessError || readiness?.ready === false) && (
          <button
            type="button"
            onClick={() => setReadinessAttempt((value) => value + 1)}
            className="button-secondary mt-3"
          >
            Проверить ещё раз
          </button>
        )}
      </aside>
    </div>
  );
}
