import { listSegments } from "@/lib/api";

import { SegmentCard } from "./SegmentCard";

export async function SegmentList({ jobId }: { jobId: string }) {
  const { items } = await listSegments(jobId);
  if (items.length === 0) {
    return <p className="text-sm text-neutral-500">Сегменты ещё не созданы.</p>;
  }
  return (
    <section className="space-y-4">
      <h2 className="text-xl font-semibold">Сегменты</h2>
      <div className="space-y-4">
        {items.map((s) => (
          <SegmentCard key={s.id} segment={s} />
        ))}
      </div>
    </section>
  );
}
