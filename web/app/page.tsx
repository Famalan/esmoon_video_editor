import Link from "next/link";

import { listJobs } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const { items } = await listJobs();
  return (
    <main className="space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Задачи</h1>
        <Link href="/jobs/new" className="rounded bg-black px-4 py-2 text-white">
          Новая задача
        </Link>
      </header>
      {items.length === 0 ? (
        <p className="text-neutral-500">Пока нет задач.</p>
      ) : (
        <ul className="divide-y rounded border bg-white">
          {items.map((job) => (
            <li key={job.id} className="flex items-center justify-between p-4">
              <div>
                <p className="font-mono text-sm">{job.id.slice(0, 8)}</p>
                <p className="text-sm text-neutral-500">
                  {job.source_url ?? "файл"} · {job.created_by}
                </p>
              </div>
              <div className="flex items-center gap-4">
                <span className="text-sm">{job.current_stage}</span>
                <span
                  className={`rounded px-2 py-1 text-xs ${
                    job.status === "succeeded"
                      ? "bg-green-100 text-green-700"
                      : job.status === "failed"
                      ? "bg-red-100 text-red-700"
                      : "bg-amber-100 text-amber-700"
                  }`}
                >
                  {job.status}
                </span>
                <Link href={`/jobs/${job.id}`} className="text-sm underline">
                  открыть
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
