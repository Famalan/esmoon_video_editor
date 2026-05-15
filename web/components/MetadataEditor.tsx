"use client";

import { useEffect, useRef, useState } from "react";

import { patchSegment } from "@/lib/api";

interface Props {
  segmentId: string;
  initialTitle: string;
  initialDescription: string;
  initialTags: string[];
}

export function MetadataEditor({ segmentId, initialTitle, initialDescription, initialTags }: Props) {
  const [title, setTitle] = useState(initialTitle);
  const [description, setDescription] = useState(initialDescription);
  const [tagsInput, setTagsInput] = useState(initialTags.join(", "));
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const tags = tagsInput.split(",").map((t) => t.trim()).filter(Boolean);
      patchSegment(segmentId, {
        yt_title: title,
        yt_description: description,
        yt_tags: tags,
      }).catch(console.error);
    }, 1000);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [segmentId, title, description, tagsInput]);

  return (
    <div className="space-y-2">
      <input
        type="text"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Заголовок (≤60 символов)"
        maxLength={60}
        className="w-full rounded border px-3 py-2 text-sm"
      />
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Описание"
        rows={6}
        className="w-full rounded border px-3 py-2 text-sm"
      />
      <input
        type="text"
        value={tagsInput}
        onChange={(e) => setTagsInput(e.target.value)}
        placeholder="Теги через запятую"
        className="w-full rounded border px-3 py-2 text-sm"
      />
    </div>
  );
}
