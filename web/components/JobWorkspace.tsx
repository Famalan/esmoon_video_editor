"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Cue,
  ExportRecord,
  getJob,
  getJobTranscript,
  Job,
  listExports,
  listSegments,
  rerunJob,
  retryJob,
  Segment,
} from "@/lib/api";
import {
  formatDate,
  formatTime,
  newIdempotencyKey,
  timedSourceUrl,
} from "@/lib/format";
import { ExportPanel } from "./ExportPanel";
import { JobProgress } from "./JobProgress";
import { SegmentList } from "./SegmentList";
import { StatusBadge } from "./StatusBadge";

export function JobWorkspace({
  initialJob,
  initialSegments,
}: {
  initialJob: Job;
  initialSegments: Segment[];
}) {
  const [job, setJob] = useState(initialJob);
  const [segments, setSegments] = useState(initialSegments);
  const [exports, setExports] = useState<ExportRecord[]>([]);
  const [cues, setCues] = useState<Cue[]>([]);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [transcriptRetry, setTranscriptRetry] = useState(0);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [action, setAction] = useState<"retry" | "rerun" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [now, setNow] = useState(0);
  const mediaDeferred = job.progress?.media_deferred === true;
  useEffect(() => {
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(timer);
  }, []);
  const stalled =
    job.status === "running" &&
    !!job.last_activity_at &&
    now - Date.parse(job.last_activity_at) > 120000;
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const [nextJob, nextSegments, nextExports] = await Promise.all([
          getJob(initialJob.id, signal),
          listSegments(initialJob.id, signal),
          listExports(initialJob.id, signal),
        ]);
        setJob(nextJob);
        setSegments((current) =>
          nextSegments.items.map((incoming) => {
            const local = current.find((item) => item.id === incoming.id);
            return local && local.revision > incoming.revision
              ? local
              : incoming;
          }),
        );
        setExports(nextExports.items);
        setRefreshError(null);
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setRefreshError(
          "Связь с сервером прервалась. Показаны последние полученные данные.",
        );
      }
    },
    [initialJob.id],
  );
  const transcriptVersion =
    typeof job.transcript_snapshot?.version === "string"
      ? job.transcript_snapshot.version
      : null;
  useEffect(() => {
    if (
      !job.source_id ||
      (!transcriptVersion &&
        (job.status === "queued" || job.status === "running"))
    )
      return;
    const ctrl = new AbortController();
    setTranscriptError(null);
    getJobTranscript(job.id, ctrl.signal)
      .then((data) => {
        setCues(data.cues);
        if (data.cues.length === 0)
          setTranscriptError("Расшифровка этой версии пока недоступна.");
      })
      .catch((e) => {
        if (!(e instanceof DOMException && e.name === "AbortError"))
          setTranscriptError(
            e instanceof Error ? e.message : "Не удалось загрузить расшифровку",
          );
      });
    return () => ctrl.abort();
  }, [job.id, job.source_id, job.status, transcriptRetry, transcriptVersion]);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const tick = async () => {
      const ctrl = new AbortController();
      await refresh(ctrl.signal);
      if (!stopped)
        timer = setTimeout(
          tick,
          ["queued", "running"].includes(job.status) ||
            segments.some((s) =>
              ["queued", "processing", "pending"].includes(s.status),
            )
            ? 2000
            : 7000,
        );
    };
    timer = setTimeout(tick, 1800);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [job.status, refresh, segments]);
  function seekSource(time: number) {
    if (mediaDeferred) {
      const sourceUrl = job.source?.source_url ?? job.source_url;
      const target = timedSourceUrl(sourceUrl, time);
      if (target) window.open(target, "_blank", "noopener,noreferrer");
      return;
    }
    const player = videoRef.current;
    if (!player) return;
    player.currentTime = Math.max(0, time);
    player.play().catch(() => undefined);
    player.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  async function runAction(kind: "retry" | "rerun") {
    setAction(kind);
    setActionError(null);
    try {
      const next =
        kind === "retry"
          ? await retryJob(job.id)
          : await rerunJob(
              job.id,
              job.topic,
              job.audience,
              newIdempotencyKey(),
            );
      if (kind === "rerun") {
        window.location.assign(`/jobs/${next.id}`);
        return;
      }
      setJob(next);
      await refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Действие не выполнено");
    } finally {
      setAction(null);
    }
  }
  const title =
    job.title ??
    job.source?.title ??
    job.source?.filename ??
    `Анализ ${job.id.slice(0, 8)}`;
  const transcriptDuration = Number(job.transcript_snapshot?.duration_limit_sec);
  const sourceDuration =
    job.source?.duration_sec ??
    (Number.isFinite(transcriptDuration) ? transcriptDuration : null);
  const timingNote =
    typeof job.transcript_snapshot?.timing_note === "string"
      ? job.transcript_snapshot.timing_note
      : null;
  const sourceUrl = job.source?.source_url ?? job.source_url;
  const summaryItems: [string, number][] = mediaDeferred
    ? [
        ["Найдено", job.counts.total],
        ["Выбрано", job.counts.selected],
        ["Размечено", job.counts.analyzed],
        ["Ошибки", job.counts.failed],
        ["Исключено", job.counts.excluded],
      ]
    : [
        ["Найдено", job.counts.total],
        ["Выбрано", job.counts.selected],
        ["Проверено", job.counts.ready],
        ["В работе", job.counts.processing],
        ["Ошибки", job.counts.failed],
        ["Исключено", job.counts.excluded],
      ];
  return (
    <main className="space-y-6">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <Link
            href="/"
            className="text-sm font-semibold text-blue-700 hover:underline"
          >
            ← История
          </Link>
          <p className="eyebrow mt-5">
            Версия анализа {job.analysis_version ?? "неизвестна"}
          </p>
          <h1 className="mt-2 max-w-4xl text-3xl font-bold tracking-tight">
            {title}
          </h1>
          <p className="mt-2 text-sm text-neutral-500">
            Создан {formatDate(job.created_at)} ·{" "}
            {mediaDeferred ? "разметка по расшифровке" : "источник"} {formatTime(sourceDuration)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="button-secondary"
            onClick={() => runAction("rerun")}
            disabled={action !== null}
            aria-busy={action === "rerun"}
          >
            {action === "rerun" ? "Создаётся версия…" : "Повторить анализ"}
          </button>
          {(stalled ||
            job.status === "failed" ||
            job.status === "partial" ||
            job.counts.failed > 0) && (
            <button
              type="button"
              className="button-primary"
              onClick={() => runAction("retry")}
              disabled={action !== null}
              aria-busy={action === "retry"}
            >
              {action === "retry"
                ? "Повторяем…"
                : stalled
                  ? "Возобновить обработку"
                  : "Повторить ошибки"}
            </button>
          )}
        </div>
      </header>
      {refreshError && (
        <p role="status" className="inline-info">
          {refreshError}
        </p>
      )}
      {actionError && (
        <p role="alert" className="inline-error">
          {actionError}
        </p>
      )}
      <JobProgress job={job} />
      <section
        className={`grid gap-4 sm:grid-cols-3 ${mediaDeferred ? "lg:grid-cols-5" : "lg:grid-cols-6"}`}
        aria-label="Сводка анализа"
      >
        {summaryItems.map(([label, value]) => (
          <div key={String(label)} className="panel p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
              {label}
            </p>
            <p className="mt-1 text-2xl font-bold tabular-nums">{value}</p>
          </div>
        ))}
      </section>
      <section className="panel-pad">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="eyebrow">{mediaDeferred ? "Расшифровка" : "Исходник"}</p>
            <h2 className="mt-1 text-lg font-semibold">
              {mediaDeferred
                ? "Проверка границ по тексту и таймкодам"
                : "Проверка границ по контексту"}
            </h2>
          </div>
          <StatusBadge
            status={
              mediaDeferred
                ? "analyzed"
                : job.source?.preview_status === "ready"
                  ? "succeeded"
                  : job.source?.preview_status ?? job.status
            }
          />
        </div>
        {mediaDeferred ? (
          <div className="inline-info mt-4">
            <p>
              Видео не скачивалось. Таймкоды рассчитаны по расшифровке и
              откроются на YouTube только после вашего нажатия.
            </p>
            {timingNote && (
              <p className="mt-2 text-xs text-neutral-600">{timingNote}</p>
            )}
            {sourceUrl && (
              <a
                className="button-secondary mt-3"
                href={timedSourceUrl(sourceUrl, 0) ?? sourceUrl}
                target="_blank"
                rel="noreferrer"
              >
                Открыть видео на YouTube
              </a>
            )}
          </div>
        ) : job.source?.preview_url ? (
          <video
            ref={videoRef}
            controls
            preload="metadata"
            src={job.source.preview_url}
            className="mt-4 aspect-video w-full rounded-lg bg-black"
          />
        ) : (
          <p className="mt-4 rounded-lg bg-neutral-100 p-4 text-sm text-neutral-600">
            Браузерное превью ещё готовится. К таймкодам можно вернуться после
            обработки источника.
          </p>
        )}
      </section>
      {transcriptError && (
        <div
          className="inline-info flex flex-wrap items-center justify-between gap-3"
          role="status"
        >
          <span>{transcriptError}</span>
          <button
            type="button"
            className="button-secondary min-h-0 py-1.5"
            onClick={() => setTranscriptRetry((value) => value + 1)}
          >
            Повторить загрузку
          </button>
        </div>
      )}
      <SegmentList
        segments={segments}
        cues={cues}
        sourceDuration={sourceDuration}
        sourceUrl={sourceUrl}
        mediaDeferred={mediaDeferred}
        onSeek={seekSource}
        onSegmentChange={(updated) => {
          const before = segments.find((item) => item.id === updated.id);
          setSegments((current) =>
            current.map((item) => (item.id === updated.id ? updated : item)),
          );
          if (before && before.selected !== updated.selected) {
            const remaining = segments.filter(
              (item) => item.selected && item.id !== updated.id,
            );
            const next = updated.selected
              ? updated
              : (remaining.find((item) => item.index > updated.index) ??
                remaining.at(-1));
            requestAnimationFrame(() => {
              const target = document.getElementById(
                next ? `segment-${next.id}` : "excluded-candidates",
              );
              target?.focus({ preventScroll: true });
              target?.scrollIntoView({ block: "nearest" });
            });
          }
        }}
      />
      <ExportPanel job={job} items={exports} onItemsChange={setExports} />
    </main>
  );
}
