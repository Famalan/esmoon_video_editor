"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { createJobFromFile, createJobFromUrl } from "@/lib/api";

const USER = "anon@local"; // в Phase 1 — захардкожено; в будущем заполним через reverse-proxy header.

export function NewJobForm() {
  const router = useRouter();
  const [mode, setMode] = useState<"url" | "file">("url");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const job =
        mode === "url"
          ? await createJobFromUrl(url, USER)
          : file
          ? await createJobFromFile(file, USER)
          : null;
      if (!job) throw new Error("Файл не выбран");
      router.push(`/jobs/${job.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setMode("url")}
          className={`rounded px-3 py-1 ${mode === "url" ? "bg-black text-white" : "bg-neutral-200"}`}
        >
          URL
        </button>
        <button
          type="button"
          onClick={() => setMode("file")}
          className={`rounded px-3 py-1 ${mode === "file" ? "bg-black text-white" : "bg-neutral-200"}`}
        >
          Файл
        </button>
      </div>

      {mode === "url" ? (
        <input
          type="url"
          required
          placeholder="https://youtube.com/..."
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="w-full rounded border px-3 py-2"
        />
      ) : (
        <input
          type="file"
          accept="video/*"
          required
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="w-full"
        />
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}

      <button
        type="submit"
        disabled={submitting}
        className="rounded bg-black px-4 py-2 text-white disabled:opacity-50"
      >
        {submitting ? "Создаю..." : "Создать"}
      </button>
    </form>
  );
}
