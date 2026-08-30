const BASE =
  process.env.API_BASE ??
  process.env.NEXT_PUBLIC_API_BASE ??
  "http://localhost:8000";

const PUBLIC_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Stage =
  | "fetch" | "transcribe" | "segment" | "cut"
  | "thumbnail" | "metadata" | "upload" | "done";

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface Chapter {
  start_sec: number;
  title: string;
}

export interface Job {
  id: string;
  source_type: "file" | "url";
  source_url: string | null;
  status: JobStatus;
  current_stage: Stage;
  created_by: string;
  created_at: string;
  error: string | null;
  chapters: Chapter[];
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

export interface Thumbnail {
  asset_id: string;
  position_idx: number;
  url: string;
}

export interface Segment {
  id: string;
  job_id: string;
  index: number;
  start_sec: number;
  end_sec: number;
  title: string | null;
  summary: string | null;
  yt_title: string | null;
  yt_description: string | null;
  yt_tags: string[] | null;
  selected_thumbnail_id: string | null;
  thumbnails: Thumbnail[];
  video_download_url: string | null;
  status: string;
  relevance: number;
  pain: number;
  hook: number;
  value: number;
  decision: "publish" | "skip";
}

export interface SegmentsList {
  items: Segment[];
}

export interface SegmentPatch {
  yt_title?: string;
  yt_description?: string;
  yt_tags?: string[];
  selected_thumbnail_id?: string;
}

function absUrl(u: string | null): string | null {
  if (!u) return u;
  return u.startsWith("http") ? u : `${PUBLIC_BASE}${u}`;
}

function normalizeSegment(s: Segment): Segment {
  return { ...s, video_download_url: absUrl(s.video_download_url) };
}

export async function listSegments(jobId: string): Promise<SegmentsList> {
  const r = await fetch(`${BASE}/jobs/${jobId}/segments`, { cache: "no-store" });
  if (!r.ok) throw new Error(`listSegments ${r.status}`);
  const data: SegmentsList = await r.json();
  return { items: data.items.map(normalizeSegment) };
}

export async function patchSegment(id: string, body: SegmentPatch): Promise<Segment> {
  const r = await fetch(`${BASE}/segments/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`patchSegment ${r.status}`);
  return normalizeSegment(await r.json());
}
