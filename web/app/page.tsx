import type { Metadata } from "next";
import Link from "next/link";
import { StatusBadge } from "@/components/StatusBadge";
import { formatDate } from "@/lib/format";
import { Job, listJobs } from "@/lib/api";
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "История анализов" };
export default async function HomePage() {
  const { items } = await listJobs();
  const groups = new Map<string, Job[]>();
  for (const job of items) {
    const key = job.source_id ?? job.source_url ?? job.id;
    groups.set(key, [...(groups.get(key) ?? []), job]);
  }
  return (
    <main className="space-y-6">
      <header className="max-w-3xl">
        <p className="eyebrow">История источников</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight">
          Цельные эпизоды из длинного видео
        </h1>
        <p className="mt-3 text-neutral-600">
          Сначала приложение находит цельные эпизоды по расшифровке. Готовые
          видео дополнительно проходят техническую проверку длительности.
        </p>
      </header>
      {groups.size === 0 ? (
        <section className="panel-pad">
          <h2 className="font-semibold">История пока пуста</h2>
          <p className="mt-1 text-sm text-neutral-600">
            Добавьте ссылку YouTube или локальный видеофайл.
          </p>
          <Link href="/jobs/new" className="button-primary mt-4">
            Начать первый анализ
          </Link>
        </section>
      ) : (
        <div className="space-y-4">
          {Array.from(groups.entries()).map(([key, jobs]) => {
            const latest = jobs[0];
            const title =
              latest.title ??
              latest.source?.title ??
              latest.source?.filename ??
              "Видео без названия";
            return (
              <section key={key} className="panel overflow-hidden">
                <header className="border-b p-4 sm:flex sm:items-center sm:justify-between sm:gap-4">
                  <div className="min-w-0">
                    <h2 className="truncate text-lg font-semibold">{title}</h2>
                    <p className="mt-1 text-sm text-neutral-500">
                      Анализов: {jobs.length} · последний{" "}
                      {formatDate(latest.created_at)}
                    </p>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2 sm:mt-0">
                    <StatusBadge status={latest.status} />
                    <span className="status-badge status-neutral">
                      {latest.progress?.media_deferred === true
                        ? `${latest.counts?.analyzed ?? 0} размечено`
                        : `${latest.counts?.ready ?? 0} MP4 готово`}
                    </span>
                  </div>
                </header>
                <ol className="divide-y">
                  {jobs.map((job) => (
                    <li
                      key={job.id}
                      className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"
                    >
                      <div>
                        <p className="text-sm font-semibold">
                          Версия анализа {job.analysis_version ?? "неизвестна"}
                        </p>
                        <p className="mt-1 text-xs text-neutral-500">
                          {formatDate(job.created_at)} ·{" "}
                          {job.counts?.selected ?? 0} выбрано ·{" "}
                          {job.progress?.media_deferred === true
                            ? `${job.counts?.analyzed ?? 0} размечено`
                            : `${job.counts?.ready ?? 0} MP4 проверено`} ·{" "}
                          {job.counts?.failed ?? 0} с ошибкой
                        </p>
                      </div>
                      <Link
                        href={`/jobs/${job.id}`}
                        className="button-secondary"
                      >
                        Открыть результат
                      </Link>
                    </li>
                  ))}
                </ol>
              </section>
            );
          })}
        </div>
      )}
    </main>
  );
}
