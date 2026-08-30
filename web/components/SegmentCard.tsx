import { Segment } from "@/lib/api";

import { MetadataEditor } from "./MetadataEditor";
import { ThumbnailPicker } from "./ThumbnailPicker";

function fmt(sec: number): string {
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  return `${m}:${String(ss).padStart(2, "0")}`;
}

export function SegmentCard({ segment }: { segment: Segment }) {
  const durationSec = Math.max(0, segment.end_sec - segment.start_sec);

  return (
    <article className="space-y-3 rounded border bg-white p-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <h2 className="text-lg font-semibold">
          Сегмент {segment.index + 1} · {fmt(segment.start_sec)}–{fmt(segment.end_sec)}
        </h2>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          <span
            className={`rounded px-2 py-1 text-xs font-medium ${
              segment.decision === "publish"
                ? "bg-emerald-100 text-emerald-800"
                : "bg-neutral-100 text-neutral-600"
            }`}
          >
            {segment.decision}
          </span>
          <span className="rounded bg-neutral-100 px-2 py-1 text-xs">{segment.status}</span>
        </div>
      </header>

      <div className="flex flex-wrap gap-1.5 text-xs text-neutral-700">
        <span className="rounded bg-blue-50 px-2 py-1 font-medium text-blue-800">
          Длительность: {fmt(durationSec)}
        </span>
        <span className="rounded bg-neutral-100 px-2 py-1">Релевантность: {segment.relevance}</span>
        <span className="rounded bg-neutral-100 px-2 py-1">Боль: {segment.pain}</span>
        <span className="rounded bg-neutral-100 px-2 py-1">Хук: {segment.hook}</span>
        <span className="rounded bg-neutral-100 px-2 py-1">Ценность: {segment.value}</span>
      </div>

      {segment.thumbnails.length > 0 && (
        <ThumbnailPicker
          segmentId={segment.id}
          thumbnails={segment.thumbnails}
          initialSelected={segment.selected_thumbnail_id}
        />
      )}

      <MetadataEditor
        segmentId={segment.id}
        initialTitle={segment.yt_title ?? segment.title ?? ""}
        initialDescription={segment.yt_description ?? segment.summary ?? ""}
        initialTags={segment.yt_tags ?? []}
      />

      {segment.video_download_url && (
        <a
          href={segment.video_download_url}
          className="inline-block rounded bg-black px-4 py-2 text-sm text-white"
        >
          Скачать клип
        </a>
      )}
    </article>
  );
}
