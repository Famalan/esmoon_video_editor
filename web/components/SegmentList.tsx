import { Chapter, listSegments, Segment } from "@/lib/api";

import { SegmentCard } from "./SegmentCard";

function fmt(sec: number): string {
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  return `${m}:${String(ss).padStart(2, "0")}`;
}

const scoreNames: Array<keyof Pick<Segment, "relevance" | "pain" | "hook" | "value">> = [
  "relevance",
  "pain",
  "hook",
  "value",
];

export async function SegmentList({
  jobId,
  chapters,
}: {
  jobId: string;
  chapters: Chapter[];
}) {
  const { items } = await listSegments(jobId);
  const orderedItems = [...items].sort(
    (a, b) => a.index - b.index || a.start_sec - b.start_sec,
  );
  const publishableItems = orderedItems.filter((segment) => segment.decision === "publish");

  return (
    <div className="space-y-8">
      <section className="space-y-4">
        <div>
          <h2 className="text-xl font-semibold">Таймкоды всего видео</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Главы описывают исходное видео целиком и не зависят от отбора роликов.
          </p>
        </div>

        {chapters.length > 0 ? (
          <ol className="max-h-[36rem] divide-y overflow-y-auto rounded border bg-white">
            {chapters.map((chapter) => (
              <li key={`${chapter.start_sec}-${chapter.title}`} className="flex gap-4 p-3">
                <span className="w-20 shrink-0 font-mono text-sm text-neutral-500">
                  {fmt(chapter.start_sec)}
                </span>
                <span className="font-medium text-neutral-900">{chapter.title}</span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="rounded border border-dashed bg-neutral-50 p-4 text-sm text-neutral-600">
            Для этой старой задачи главы ещё не сформированы.
          </p>
        )}
      </section>

      <section className="space-y-4">
        <div>
          <h2 className="text-xl font-semibold">Оценка найденных фрагментов</h2>
          <p className="mt-1 text-sm text-neutral-500">
            publish ставится при pain ≥ 70 и value ≥ 70. Границы определяются полнотой темы, а не заданной длительностью.
          </p>
        </div>

        {orderedItems.length > 0 ? (
          <ol className="divide-y rounded border bg-white">
            {orderedItems.map((segment) => (
              <li key={segment.id} className="space-y-2 p-4 sm:flex sm:items-start sm:justify-between sm:gap-4 sm:space-y-0">
                <div className="min-w-0">
                  <p className="font-mono text-sm text-neutral-500">
                    {fmt(segment.start_sec)}–{fmt(segment.end_sec)}
                  </p>
                  <p className="mt-1 font-medium text-neutral-900">
                    {segment.title ?? segment.yt_title ?? "Без названия"}
                  </p>
                  {segment.summary && (
                    <p className="mt-2 max-w-3xl text-sm leading-6 text-neutral-600">
                      {segment.summary}
                    </p>
                  )}
                </div>

                <div className="flex shrink-0 flex-wrap items-center gap-1.5 sm:max-w-md sm:justify-end">
                  <span
                    className={`rounded px-2 py-1 text-xs font-medium ${
                      segment.decision === "publish"
                        ? "bg-emerald-100 text-emerald-800"
                        : "bg-neutral-100 text-neutral-600"
                    }`}
                  >
                    {segment.decision}
                  </span>
                  {scoreNames.map((key) => (
                    <span key={key} className="rounded bg-neutral-100 px-2 py-1 text-xs text-neutral-700">
                      {key}: {segment[key]}
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="rounded border border-dashed bg-neutral-50 p-4 text-sm text-neutral-600">
            Самостоятельных фрагментов для оценки не найдено.
          </p>
        )}
      </section>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold">Ролики к публикации</h2>
        {publishableItems.length > 0 ? (
          <div className="space-y-4">
            {publishableItems.map((segment) => (
              <SegmentCard key={segment.id} segment={segment} />
            ))}
          </div>
        ) : (
          <p className="rounded border border-dashed bg-neutral-50 p-4 text-sm text-neutral-600">
            Нет роликов с решением publish. Все найденные фрагменты отмечены как skip.
          </p>
        )}
      </section>
    </div>
  );
}
