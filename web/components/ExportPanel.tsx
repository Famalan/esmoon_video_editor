"use client";
import { useRef, useState } from "react";
import {
  analysisDownloadUrl,
  createExport,
  ExportRecord,
  Job,
} from "@/lib/api";
import { formatDate, newIdempotencyKey } from "@/lib/format";
import { StatusBadge } from "./StatusBadge";
export function ExportPanel({
  job,
  items,
  onItemsChange,
}: {
  job: Job;
  items: ExportRecord[];
  onItemsChange: (items: ExportRecord[]) => void;
}) {
  const mediaDeferred = job.progress?.media_deferred === true;
  const [busy, setBusy] = useState(false);
  const requestKey = useRef<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function build() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      requestKey.current ??= newIdempotencyKey();
      const item = await createExport(job.id, requestKey.current);
      onItemsChange([
        item,
        ...items.filter((previous) => previous.id !== item.id),
      ]);
      requestKey.current = null;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось собрать комплект");
    } finally {
      setBusy(false);
    }
  }
  if (mediaDeferred) {
    return (
      <section className="panel-pad">
        <p className="eyebrow">Результат анализа</p>
        <h2 className="mt-1 text-xl font-semibold">Скачать разметку</h2>
        <p className="mt-2 max-w-2xl text-sm text-neutral-600">
          JSON содержит выбранные эпизоды, границы, контекст и снимок правил.
          Видео пока не скачивалось, поэтому MP4, превью, HTML и PDF в этот
          результат не входят.
        </p>
        <a
          className="button-primary mt-4"
          href={analysisDownloadUrl(job.id)}
          download
        >
          Скачать JSON
        </a>
      </section>
    );
  }
  return (
    <section className="panel-pad">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="eyebrow">Экспорт</p>
          <h2 className="mt-1 text-xl font-semibold">
            Зафиксированный комплект
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-neutral-600">
            Архив сохраняет выбранные ревизии: MP4, офлайн HTML, PDF и
            JSON-манифест. Поздние правки его не изменят.
          </p>
        </div>
        <button
          type="button"
          onClick={build}
          disabled={busy || job.counts.selected === 0}
          className="button-primary"
          aria-busy={busy}
        >
          {busy ? "Собирается…" : "Собрать комплект"}
        </button>
      </div>
      {job.counts.selected === 0 && (
        <p className="mt-3 text-sm text-neutral-500">
          Сначала включите хотя бы один клип.
        </p>
      )}
      {error && (
        <p role="alert" className="inline-error mt-4">
          {error}
        </p>
      )}
      {items.length > 0 && (
        <ol className="mt-5 divide-y border-t">
          {items.map((item) => (
            <li key={item.id} className="py-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold">
                    Комплект из {item.clip_count} клипов
                  </p>
                  <p className="text-xs text-neutral-500">
                    {formatDate(item.created_at)}
                  </p>
                </div>
                <StatusBadge status={item.status} />
              </div>
              {item.error && (
                <p className="mt-2 text-sm text-red-700">{item.error}</p>
              )}
              {item.status === "succeeded" && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <a
                    className="button-primary"
                    href={item.download_url ?? undefined}
                  >
                    Скачать ZIP
                  </a>
                  <a
                    className="button-secondary"
                    href={item.html_url ?? undefined}
                  >
                    HTML
                  </a>
                  <a
                    className="button-secondary"
                    href={item.pdf_url ?? undefined}
                  >
                    PDF
                  </a>
                  <a
                    className="button-secondary"
                    href={item.manifest_url ?? undefined}
                  >
                    JSON
                  </a>
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
