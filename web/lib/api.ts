const SERVER_BASE =
  process.env.API_BASE ??
  process.env.NEXT_PUBLIC_API_BASE ??
  "http://localhost:8000";
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Stage =
  | "fetch"
  | "transcribe"
  | "segment"
  | "cut"
  | "thumbnail"
  | "metadata"
  | "upload"
  | "done";
export type JobStatus =
  "queued" | "running" | "succeeded" | "partial" | "failed";
export type StageState = "pending" | "running" | "succeeded" | "failed";
export type Selection = "auto" | "include" | "exclude";
export type ReviewState = "unreviewed" | "accepted" | "rejected";
export interface Chapter {
  start_sec: number;
  title: string;
}
export interface Cue {
  start: number;
  end: number;
  text: string;
}
export interface Source {
  id: string;
  source_type: "file" | "url";
  source_url: string | null;
  title: string | null;
  filename: string | null;
  duration_sec: number | null;
  status: string;
  preview_status: string;
  preview_url: string | null;
  transcript_version: string | null;
  created_at: string;
}
export interface JobCounts {
  total: number;
  selected: number;
  analyzed: number;
  ready: number;
  failed: number;
  processing: number;
  excluded: number;
}
export interface JobProgressData {
  stage?: string;
  detail?: string;
  completed?: number;
  total?: number;
  percent?: number;
  [key: string]: unknown;
}
export interface Job {
  id: string;
  source_type: "file" | "url";
  source_url: string | null;
  source_id: string | null;
  source: Source | null;
  title: string | null;
  status: JobStatus;
  current_stage: Stage;
  created_by: string;
  created_at: string;
  error: string | null;
  chapters: Chapter[];
  analysis_version: number | null;
  policy_snapshot: Record<string, unknown> | null;
  transcript_snapshot: Record<string, unknown> | null;
  topic: string | null;
  audience: string | null;
  attempt_no: number;
  progress: JobProgressData | null;
  last_activity_at: string | null;
  counts: JobCounts;
}
export interface Thumbnail {
  asset_id: string;
  position_idx: number;
  url: string;
}
export interface MediaRevision {
  id: string;
  number: number;
  start_sec: number;
  end_sec: number;
  status: string;
  actual_duration_sec: number | null;
  validation: Record<string, unknown> | null;
  created_at?: string;
  video_download_url?: string | null;
  playback_url?: string | null;
}
export interface Segment {
  id: string;
  job_id: string;
  index: number;
  start_sec: number;
  end_sec: number;
  title: string | null;
  summary: string | null;
  transcript_excerpt: string | null;
  yt_title: string | null;
  yt_description: string | null;
  yt_tags: string[] | null;
  selected_thumbnail_id: string | null;
  thumbnails: Thumbnail[];
  video_download_url: string | null;
  playback_url: string | null;
  status: string;
  relevance: number;
  pain: number;
  hook: number;
  value: number;
  decision: "publish" | "skip";
  revision: number;
  current_revision_id: string | null;
  media_revision: number | null;
  selection: Selection;
  selected: boolean;
  review_state: ReviewState;
  rejection_reason: string | null;
  error: string | null;
  stages: Record<string, StageState> | null;
  validation: Record<string, unknown> | null;
  actual_duration_sec: number | null;
  metadata_needs_review: boolean;
  manual_fields: string[];
}
export interface SegmentPatch {
  expected_revision: number;
  start_sec?: number;
  end_sec?: number;
  selection?: Selection;
  review_state?: ReviewState;
  yt_title?: string;
  yt_description?: string;
  yt_tags?: string[];
  selected_thumbnail_id?: string;
  metadata_needs_review?: false;
}
export interface ReadinessService {
  ready: boolean;
  detail: string;
}
export interface Readiness {
  ready: boolean;
  services: Record<string, ReadinessService>;
  model: string;
  reasoning: string;
  policy: Record<string, unknown>;
  max_upload_bytes: number;
}
export interface ExportRecord {
  id: string;
  job_id: string;
  status: "queued" | "running" | "processing" | "succeeded" | "failed";
  error: string | null;
  created_at: string;
  completed_at: string | null;
  clip_count: number;
  download_url: string | null;
  html_url: string | null;
  pdf_url: string | null;
  manifest_url: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
function absolute(url: string | null | undefined): string | null {
  if (!url) return null;
  if (/^https?:\/\//.test(url)) return url;
  return `${API_BASE}${url.startsWith("/") ? "" : "/"}${url}`;
}
async function errorFrom(response: Response): Promise<ApiError> {
  let message = `Сервер вернул ${response.status}`;
  try {
    const payload = await response.json();
    const detail = payload?.detail;
    if (typeof detail === "string") message = detail;
    else if (Array.isArray(detail))
      message = detail.map((item) => item.msg ?? String(item)).join("; ");
  } catch {
    /* no JSON body */
  }
  return new ApiError(response.status, message);
}
async function request<T>(
  path: string,
  init?: RequestInit,
  server = false,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${server ? SERVER_BASE : API_BASE}${path}`, {
      cache: "no-store",
      ...init,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError(
      0,
      "Нет связи с сервером. Правки сохранены в карточке; повторите попытку.",
    );
  }
  if (!response.ok) throw await errorFrom(response);
  return response.json() as Promise<T>;
}
function normalizeSource(source: Source | null): Source | null {
  return source
    ? { ...source, preview_url: absolute(source.preview_url) }
    : null;
}
function normalizeJob(job: Job): Job {
  const counts = job.counts ?? {
    total: 0,
    selected: 0,
    analyzed: 0,
    ready: 0,
    failed: 0,
    processing: 0,
    excluded: 0,
  };
  return {
    ...job,
    source: normalizeSource(job.source),
    counts: { ...counts, analyzed: counts.analyzed ?? 0 },
  };
}

export function analysisDownloadUrl(jobId: string): string {
  return `${API_BASE}/jobs/${jobId}/analysis/download`;
}
function normalizeSegment(segment: Segment): Segment {
  return {
    ...segment,
    playback_url: absolute(segment.playback_url),
    video_download_url: absolute(segment.video_download_url),
    thumbnails: (segment.thumbnails ?? []).map((thumb) => ({
      ...thumb,
      url: absolute(thumb.url) ?? thumb.url,
    })),
    revision: segment.revision ?? 0,
    selection: segment.selection ?? "auto",
    selected: segment.selected ?? segment.decision === "publish",
    review_state: segment.review_state ?? "unreviewed",
    metadata_needs_review: segment.metadata_needs_review ?? false,
    manual_fields: segment.manual_fields ?? [],
  };
}
function normalizeExport(record: ExportRecord): ExportRecord {
  return {
    ...record,
    download_url: absolute(record.download_url),
    html_url: absolute(record.html_url),
    pdf_url: absolute(record.pdf_url),
    manifest_url: absolute(record.manifest_url),
  };
}

export async function listJobs(): Promise<{ items: Job[] }> {
  const data = await request<{ items: Job[] }>(
    "/jobs",
    undefined,
    typeof window === "undefined",
  );
  return { items: data.items.map(normalizeJob) };
}
export async function getJob(id: string, signal?: AbortSignal): Promise<Job> {
  return normalizeJob(
    await request<Job>(
      `/jobs/${id}`,
      { signal },
      typeof window === "undefined",
    ),
  );
}
export async function listSegments(
  jobId: string,
  signal?: AbortSignal,
): Promise<{ items: Segment[] }> {
  const data = await request<{ items: Segment[] }>(
    `/jobs/${jobId}/segments`,
    { signal },
    typeof window === "undefined",
  );
  return { items: data.items.map(normalizeSegment) };
}
export async function getTranscript(
  sourceId: string,
  signal?: AbortSignal,
): Promise<{ version: string | null; cues: Cue[] }> {
  return request(`/sources/${sourceId}/transcript`, { signal });
}
export async function getJobTranscript(
  jobId: string,
  signal?: AbortSignal,
): Promise<{ version: string | null; cues: Cue[] }> {
  return request(`/jobs/${jobId}/transcript`, { signal });
}
export async function getReadiness(signal?: AbortSignal): Promise<Readiness> {
  return request("/health/ready", { signal });
}
export async function createJobFromUrl(
  url: string,
  topic: string,
  audience: string,
  idempotencyKey: string,
): Promise<Job> {
  return normalizeJob(
    await request<Job>("/jobs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({
        source_type: "url",
        source_url: url,
        topic: topic || null,
        audience: audience || null,
      }),
    }),
  );
}
export function createJobFromFile(
  file: File,
  topic: string,
  audience: string,
  idempotencyKey: string,
  onProgress: (percent: number) => void,
  signal?: AbortSignal,
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const data = new FormData();
    data.append("file", file);
    data.append("topic", topic);
    data.append("audience", audience);
    xhr.open("POST", `${API_BASE}/jobs/upload`);
    xhr.setRequestHeader("Idempotency-Key", idempotencyKey);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable)
        onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onerror = () =>
      reject(
        new Error("Загрузка прервалась. Выберите файл и запустите её заново."),
      );
    xhr.onabort = () =>
      reject(new DOMException("Загрузка отменена", "AbortError"));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300)
        resolve(normalizeJob(JSON.parse(xhr.responseText)));
      else {
        let detail = `Сервер вернул ${xhr.status}`;
        try {
          detail = JSON.parse(xhr.responseText)?.detail ?? detail;
        } catch {
          /* no JSON */
        }
        reject(
          new ApiError(
            xhr.status,
            typeof detail === "string" ? detail : JSON.stringify(detail),
          ),
        );
      }
    };
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(data);
  });
}
export async function patchSegment(
  id: string,
  body: SegmentPatch,
): Promise<Segment> {
  return normalizeSegment(
    await request<Segment>(`/segments/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}
export async function listRevisions(
  id: string,
): Promise<{ items: MediaRevision[] }> {
  const data = await request<{ items: MediaRevision[] }>(
    `/segments/${id}/revisions`,
  );
  return {
    items: data.items.map((item) => ({
      ...item,
      playback_url: absolute(item.playback_url),
      video_download_url: absolute(item.video_download_url),
    })),
  };
}
export async function rerunJob(
  id: string,
  topic: string | null,
  audience: string | null,
  key: string,
): Promise<Job> {
  return normalizeJob(
    await request<Job>(`/jobs/${id}/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify({ topic, audience }),
    }),
  );
}
export async function retryJob(id: string): Promise<Job> {
  return normalizeJob(
    await request<Job>(`/jobs/${id}/retry`, { method: "POST" }),
  );
}
export async function createExport(
  jobId: string,
  key: string,
): Promise<ExportRecord> {
  return normalizeExport(
    await request<ExportRecord>(`/jobs/${jobId}/exports`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
    }),
  );
}
export async function listExports(
  jobId: string,
  signal?: AbortSignal,
): Promise<{ items: ExportRecord[] }> {
  const data = await request<{ items: ExportRecord[] }>(
    `/jobs/${jobId}/exports`,
    { signal },
  );
  return { items: data.items.map(normalizeExport) };
}
