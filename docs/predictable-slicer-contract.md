# Video Slicer v1 implementation contract

The approved profile is `shared.policy.POLICY`: one contiguous source interval,
90–1500 seconds actual MP4, GPT-6 Astra medium. No Telegram or manual new episodes.
Keep legacy rows/assets readable, but never label their unknown settings as v1.

## Ownership and sequence

Root: shared ORM/policy, migration, API, integration, startup, API tests.
Worker writer: worker tasks/services/config/progress/tests, excluding exports.py/exporter.py.
UI writer: web/** and DESIGN.md. Export writer: worker/tasks/exports.py,
worker/services/exporter.py, worker/tests/test_exporter.py. No commits or broad staging.

## Persistence and worker interface (frozen)

`shared/shared/models.py` is authoritative and imported by API/worker model modules.
Source: unique source_key youtube:ID or sha256:HASH, original_key, subs_key,
transcript_key/version, preview_key/status, duration_sec, title, ingest_attempt_id/lease.
Workers must serialize source ingestion with a Postgres session advisory lock held
for source preparation (release on connection loss); only publish complete validated
objects, immutable attempt keys. Cached originals/subs can be adopted from historical
DB Assets by normalized video ID without attributing historical policy. External
experimental exports must not be imported. Transcript snapshot `{key,version}` is
assigned to Job exactly once, before any model call.

Job: source_id, analysis_version, parent_job_id, policy_snapshot, transcript_snapshot,
attempt_id, attempt_no, progress JSON, last_activity_at, topic, audience.
Job statuses queued/running/succeeded/partial/failed. Existing Stage enum is retained;
substage goes in progress `{stage,detail,completed,total,percent}`. Heartbeat persists
last_activity during long subprocess/LLM work.

Segment.revision is the optimistic edit version (increments on every user mutation).
Segment.current_revision_id points to SegmentRevision. Segment.selection is
auto/include/exclude; selected iff include or (auto and decision publish).
Segment.review_state: unreviewed/accepted/rejected, independent of selection.
SegmentRevision.number increments on bounds change only; fixed start/end, attempt_id,
stages `{render,verify,thumbnail,metadata}` with pending/running/succeeded/failed values,
validation `{technical:{ok,...},narrative:{ok,reason,...}}`, actual_duration_sec,
video_key, transcript_text (full range), yt_title/yt_description/yt_tags, manual_fields
(list of those field names), metadata_needs_review. Status queued/processing/ready/failed.
Worker never overwrites fields in manual_fields; bounds edits copy manual values and
flag metadata_needs_review. Assets point to revision_id and immutable attempt keys;
query current revision explicitly. A technical pass enables playback/download even
when metadata failed. Ready requires all stages succeeded.

Celery entry points:
- `worker.tasks.chain.build_pipeline(job_id, attempt_id)`; all descendant writes fence
  the captured attempt. Repeated deliveries are serialized by advisory job lock and
  skip completed stages. Job progress flags preserve successful analysis on retry.
- `worker.tasks.chain.render_revision(segment_id, revision_id, attempt_id)`;
  verify current revision and attempt on every publication; only missing/failed stages.
- `worker.tasks.exports.build_export(export_id, attempt_id)`.

API retry rotates Job attempt; it rotates failed current revision attempts and sets
them queued, retaining successful stage outputs. Ordinary bounds PATCH creates a
new revision and calls render_revision. Acquire job row lock before selection/bounds
overlap validation. Worker must recheck selected intervals before rendering and
aggregate job state after single-revision work as well as pipelines.

Readiness worker JSON in Redis `video-slicer:worker-ready`, refreshed <=30s with TTL90:
`{ready:bool, codex:bool, ffmpeg:bool, model:"gpt-6-astra", reasoning:"medium", detail:str}`.
API /health/ready checks DB/Redis/MinIO and this worker heartbeat. No secrets returned.

## HTTP contract (JSON snake_case)

Existing URLs retained. All relative media URLs resolve against API base, not web.
- GET /jobs -> `{items:JobOut[]}`
- POST /jobs body `{source_type?:"url"|"file",source_url?:string,source_id?:UUID,topic?:string,audience?:string}`
  Header Idempotency-Key per logical click. Missing file source_id rejected; file upload below.
- POST /jobs/upload multipart file + topic + audience; <=10,000,000,000 bytes.
  XHR progress; response JobOut after durable complete upload and ffprobe container
  validation. No Job/analysis on partial upload. Stream bounded chunks to MinIO multipart.
- POST /jobs/{id}/rerun (same optional topic/audience body) -> new JobOut, Idempotency-Key.
- POST /jobs/{id}/retry -> JobOut, idempotent while queued/running.
- GET /jobs/{id} -> JobOut. GET /jobs/{id}/segments -> `{items:SegmentOut[]}`.
- GET /sources/{id} -> SourceOut; GET /sources/{id}/transcript ->
  `{version:string|null,cues:[{start:number,end:number,text:string}]}`.
- GET /sources/{id}/preview and /segments/{id}/playback support HTTP Range.
- PATCH /segments/{id}: `{expected_revision:number,start_sec?:number,end_sec?:number,
  selection?:"auto"|"include"|"exclude",review_state?:"unreviewed"|"accepted"|"rejected",
  yt_title?:string,yt_description?:string,yt_tags?:string[],selected_thumbnail_id?:UUID,
  metadata_needs_review?:false}` -> SegmentOut. 409 conflict; 422 policy.
  Unknown keys (including ranges/parts) rejected. No save on mount/open.
- GET /segments/{id}/revisions -> `{items:[{id,number,start_sec,end_sec,status,
  actual_duration_sec,validation,created_at,video_download_url,playback_url}]}`.
- GET /segments/{id}/download?revision_id=UUID -> stable attachment route.
- GET /health retains `{status:"ok"}`. GET /health/ready ->
  `{ready,services:{database:{ready,detail},redis:{ready,detail},storage:{ready,detail},
  worker:{ready,detail},codex:{ready,detail}},model,reasoning,policy,max_upload_bytes}`.
- POST /jobs/{id}/exports -> ExportOut, Idempotency-Key. Reject selected pending or
  failed revisions (409 with explicit detail); no silent subset. An empty selection
  is rejected. GET /jobs/{id}/exports -> `{items:ExportOut[]}`.
- GET /exports/{id} -> ExportOut; GET /exports/{id}/download?format=zip|html|pdf|json.

SourceOut: id,source_type,source_url,title,filename,duration_sec,status,preview_status,
preview_url (null until ready),transcript_version,created_at.
JobOut extends old fields with source_id,source (SourceOut|null),title,
analysis_version,policy_snapshot,transcript_snapshot,topic,audience,attempt_no,
progress,last_activity_at,counts `{total,selected,ready,failed,processing,excluded}`.
SegmentOut retains old fields with revision,current_revision_id,media_revision,
selection,selected,review_state,rejection_reason,error,stages,validation,
actual_duration_sec,playback_url,transcript_excerpt,metadata_needs_review,manual_fields.
`status` uses current revision status for new clips, legacy status otherwise.
Thumbnails `{asset_id,position_idx,url}` include current revision only.
ExportOut: id,job_id,status,error,created_at,completed_at,clip_count,
download_url,html_url,pdf_url,manifest_url (null until succeeded).

## Immutable export snapshot

Export.snapshot is fixed at creation, never reads current selection/bounds/metadata again:
`{schema_version:1,source:{id,title,source_url,duration_sec},
job:{id,analysis_version,policy_snapshot,transcript_snapshot,created_at},
clips:[{segment_id,revision_id,revision,index,start_sec,end_sec,actual_duration_sec,
title,summary,yt_title,yt_description,yt_tags,video_key,thumbnail_keys,
selected_thumbnail_key,validation,metadata_needs_review}]}`.
Exporter outputs ZIP of MP4s, thumbnails, index.html with relative media links,
report.pdf (embedded Cyrillic font), manifest.json matching exact revision IDs and
relative filenames + SHA256. Publish under export/ID/attempt/ immutable keys; update
Export.zip_key/html_key/pdf_key/manifest_key only if attempt still matches.
For separately downloaded HTML, clearly explain it is the package index and requires
the ZIP media alongside it; ZIP must work fully offline. HTML escape all user/model text.

## Acceptance / tracking

1. Backup verified; no active jobs before schema migration.
2. Shared exact Decimal seconds validation (no floating tolerance for 90/1500).
3. Migration preserves legacy rows and null unknown policy.
4. File/cached URL, corrupt/oversize/interruption, stale edit/attempt/double-click tests.
5. Sparse-keyframe actual MP4 regression; stage-specific recovery.
6. UI narrow panel/player/transcript/saving and automatic refresh.
7. Export offline media/manifest/hash/Cyrillic PDF render.
8. Full oDWCfXxrmpk analysis+renders+context review with no clip-count target.
