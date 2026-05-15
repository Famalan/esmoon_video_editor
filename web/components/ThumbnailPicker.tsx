"use client";

import { useState } from "react";

import { Thumbnail, patchSegment } from "@/lib/api";

export function ThumbnailPicker({
  segmentId,
  thumbnails,
  initialSelected,
}: {
  segmentId: string;
  thumbnails: Thumbnail[];
  initialSelected: string | null;
}) {
  const [selected, setSelected] = useState(initialSelected);

  async function pick(assetId: string) {
    setSelected(assetId);
    try {
      await patchSegment(segmentId, { selected_thumbnail_id: assetId });
    } catch (e) {
      console.error(e);
    }
  }

  return (
    <div className="flex gap-2">
      {thumbnails.map((t) => (
        <button
          key={t.asset_id}
          type="button"
          onClick={() => pick(t.asset_id)}
          className={`relative h-20 w-32 overflow-hidden rounded border-2 ${
            selected === t.asset_id ? "border-black" : "border-transparent opacity-60"
          }`}
        >
          <img src={t.url} alt={`thumb ${t.position_idx}`} className="h-full w-full object-cover" />
        </button>
      ))}
    </div>
  );
}
