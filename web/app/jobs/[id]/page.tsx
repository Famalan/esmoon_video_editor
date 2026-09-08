import type { Metadata } from "next";
import { JobWorkspace } from "@/components/JobWorkspace";
import { getJob, listSegments } from "@/lib/api";
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Результат анализа" };
export default async function JobDetailsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [job, segments] = await Promise.all([getJob(id), listSegments(id)]);
  return <JobWorkspace initialJob={job} initialSegments={segments.items} />;
}
