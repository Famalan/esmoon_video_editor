const BASE =
  process.env.API_BASE ??
  process.env.NEXT_PUBLIC_API_BASE ??
  "http://localhost:8000";

export type Stage =
  | "fetch" | "transcribe" | "segment" | "cut"
  | "thumbnail" | "metadata" | "upload" | "done";

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface Job {
  id: string;
  source_type: "file" | "url";
  source_url: string | null;
  status: JobStatus;
  current_stage: Stage;
  created_by: string;
  created_at: string;
  error: string | null;
}

export async function listJobs(): Promise<{ items: Job[] }> {
  const r = await fetch(`${BASE}/jobs`, { cache: "no-store" });
  if (!r.ok) throw new Error(`listJobs ${r.status}`);
  return r.json();
}

export async function getJob(id: string): Promise<Job> {
  const r = await fetch(`${BASE}/jobs/${id}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`getJob ${r.status}`);
  return r.json();
}

export async function createJobFromUrl(url: string, user: string): Promise<Job> {
  const r = await fetch(`${BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User": user },
    body: JSON.stringify({ source_type: "url", source_url: url }),
  });
  if (!r.ok) throw new Error(`createJobFromUrl ${r.status}`);
  return r.json();
}

export async function createJobFromFile(file: File, user: string): Promise<Job> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${BASE}/jobs/upload`, {
    method: "POST",
    headers: { "X-User": user },
    body: fd,
  });
  if (!r.ok) throw new Error(`createJobFromFile ${r.status}`);
  return r.json();
}
