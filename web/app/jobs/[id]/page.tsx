import { JobProgress } from "@/components/JobProgress";
import { SegmentList } from "@/components/SegmentList";
import { getJob } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function JobDetailsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const job = await getJob(id);
  return (
    <main className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Задача {id.slice(0, 8)}</h1>
        <p className="text-sm text-neutral-500">{job.source_url ?? "файл"}</p>
      </header>
      <JobProgress initial={job} />
      {job.status === "succeeded" && <SegmentList jobId={id} />}
    </main>
  );
}
