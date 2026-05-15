"use client";

import { useEffect, useState } from "react";

import { Job, Stage, getJob } from "@/lib/api";

const STAGES: Stage[] = [
  "fetch", "transcribe", "segment", "cut", "thumbnail", "metadata", "upload", "done",
];

export function JobProgress({ initial }: { initial: Job }) {
  const [job, setJob] = useState(initial);

  useEffect(() => {
    if (job.status === "succeeded" || job.status === "failed") return;
    const t = setInterval(async () => {
      try {
        const next = await getJob(job.id);
        setJob(next);
      } catch {
        /* ignore transient errors */
      }
    }, 1500);
    return () => clearInterval(t);
  }, [job.id, job.status]);

  const currentIndex = STAGES.indexOf(job.current_stage);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-semibold">Статус:</span>
        <span>{job.status}</span>
        {job.error && <span className="text-red-600">{job.error}</span>}
      </div>
      <ol className="flex flex-wrap gap-2">
        {STAGES.map((stage, i) => (
          <li
            key={stage}
            className={`rounded px-3 py-1 text-xs ${
              i < currentIndex
                ? "bg-green-100 text-green-700"
                : i === currentIndex
                ? "bg-amber-100 text-amber-700"
                : "bg-neutral-100 text-neutral-500"
            }`}
          >
            {stage}
          </li>
        ))}
      </ol>
    </div>
  );
}
