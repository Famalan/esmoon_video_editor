import { Job, Stage } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { StatusBadge } from "./StatusBadge";
const allStages: Stage[] = [
  "fetch",
  "transcribe",
  "segment",
  "cut",
  "thumbnail",
  "metadata",
  "upload",
  "done",
];
const labels: Record<Stage, string> = {
  fetch: "Данные источника",
  transcribe: "Расшифровка",
  segment: "Разметка",
  cut: "Рендер",
  thumbnail: "Превью",
  metadata: "Метаданные",
  upload: "Публикация",
  done: "Итог",
};
export function JobProgress({ job }: { job: Job }) {
  const mediaDeferred = job.progress?.media_deferred === true;
  const stages = mediaDeferred
    ? allStages.filter((stage) =>
        ["fetch", "transcribe", "segment", "done"].includes(stage),
      )
    : job.policy_snapshot
      ? allStages.filter((stage) => stage !== "upload")
      : allStages;
  const index = stages.indexOf(job.current_stage),
    progress = job.progress;
  const interrupted = job.status === "failed" || job.status === "partial";
  return (
    <section className="panel-pad" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold">Ход обработки</p>
          <p className="mt-1 text-xs text-neutral-500">
            Последняя активность: {formatDate(job.last_activity_at)}
          </p>
        </div>
        <StatusBadge status={job.status} />
      </div>
      {job.policy_snapshot ? (
        <p className="mt-3 text-sm text-neutral-600">
          Правила «{String(job.policy_snapshot.name)}» · версия{" "}
          {String(job.policy_snapshot.version)} · GPT-6 Astra ·{" "}
          {String(job.policy_snapshot.reasoning)}. Один диапазон, 1:30–25:00.
        </p>
      ) : (
        <p className="mt-3 text-xs text-neutral-500">
          Правила исторического анализа неизвестны.
        </p>
      )}
      {mediaDeferred && (
        <p className="inline-info mt-3">
          Разметка готова по расшифровке. Исходное видео не скачивалось, поэтому
          фактическая длительность и MP4 ещё не проверены.
        </p>
      )}
      <ol className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
        {stages.map((stage, i) => (
          <li
            key={stage}
            className={`rounded-lg border px-3 py-2 text-xs font-semibold ${!interrupted && (i < index || job.status === "succeeded") ? "border-emerald-200 bg-emerald-50 text-emerald-800" : i === index ? "border-blue-300 bg-blue-50 text-blue-800" : "border-neutral-200 bg-neutral-50 text-neutral-500"}`}
          >
            {labels[stage]}
          </li>
        ))}
      </ol>
      {progress?.detail && (
        <p className="mt-3 text-sm text-neutral-600">
          {String(progress.detail)}
        </p>
      )}
      {typeof progress?.percent === "number" &&
        typeof progress.total === "number" &&
        progress.total > 0 && (
          <progress
            value={progress.percent}
            max={100}
            className="mt-2 h-2 w-full accent-blue-700"
          >
            {progress.percent}%
          </progress>
        )}
      {job.error && (
        <p role="alert" className="inline-error mt-3">
          {job.error}
        </p>
      )}
      {job.status === "partial" && (
        <p className="mt-3 text-sm text-amber-800">
          Часть файлов готова и доступна. Ошибочные этапы можно повторить
          отдельно.
        </p>
      )}
    </section>
  );
}
