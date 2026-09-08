from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Asset, AssetKind, Export, Job, Segment, SegmentRevision, Source, Upload
from app.schemas import ExportOut, JobOut, SegmentOut, SourceOut, ThumbnailOut
from shared.policy import is_selected, validate_actual_duration, PolicyError


def technical_ready(revision):
    if revision is None or not revision.video_key or not revision.validation.get('technical', {}).get('ok'):
        return False
    try:
        validate_actual_duration(revision.actual_duration_sec)
    except PolicyError:
        return False
    return True


def source_out(source):
    out = SourceOut.model_validate(source)
    out.preview_url = f'/sources/{source.id}/preview' if source.preview_key and source.preview_status == 'ready' else None
    return out


def job_out(job: Job, db: Session):
    out = JobOut.model_validate(job)
    source = db.get(Source, job.source_id) if job.source_id else None
    out.source = source_out(source) if source else None
    out.title = source.title if source else (job.source_url or 'Локальное видео')
    counts = dict(total=0, selected=0, ready=0, analyzed=0, failed=0, processing=0, excluded=0)
    pairs = db.execute(select(Segment, SegmentRevision).outerjoin(SegmentRevision, Segment.current_revision_id == SegmentRevision.id).where(Segment.job_id == job.id)).all()
    counts['total'] = len(pairs)
    for segment, revision in pairs:
        if not is_selected(segment):
            counts['excluded'] += 1
            continue
        counts['selected'] += 1
        if revision and revision.validation.get('narrative',{}).get('ok') is True:
            counts['analyzed'] += 1
        if revision and revision.status == 'ready' and technical_ready(revision):
            counts['ready'] += 1
        elif (revision and revision.status == 'failed') or segment.status == 'failed':
            counts['failed'] += 1
        elif revision and revision.status in ('queued','processing'):
            counts['processing'] += 1
    out.counts = counts
    return out


def segment_out(seg: Segment, db: Session):
    rev = db.get(SegmentRevision, seg.current_revision_id) if seg.current_revision_id else None
    asset_query = select(Asset).where(Asset.segment_id == seg.id)
    asset_query = asset_query.where(Asset.revision_id == rev.id) if rev else asset_query.where(Asset.revision_id.is_(None))
    assets = db.scalars(asset_query.order_by(Asset.position_idx, Asset.id)).all()
    thumbs = [asset for asset in assets if asset.kind == AssetKind.THUMBNAIL]
    legacy_video = next((asset for asset in assets if asset.kind == AssetKind.SEGMENT_VIDEO), None)
    upload = db.scalar(select(Upload).where(Upload.segment_id == seg.id)) if not rev else None
    playable = technical_ready(rev) if rev else legacy_video is not None
    suffix = f'?revision_id={rev.id}' if rev else ''
    return SegmentOut(
        id=seg.id,job_id=seg.job_id,index=seg.index,start_sec=seg.start_sec,end_sec=seg.end_sec,
        title=seg.title,summary=seg.summary,transcript_excerpt=seg.transcript_excerpt,
        relevance=seg.relevance,pain=seg.pain,hook=seg.hook,value=seg.value,decision=seg.decision,
        yt_title=rev.yt_title if rev else (upload.youtube_title if upload else None),
        yt_description=rev.yt_description if rev else (upload.youtube_description if upload else None),
        yt_tags=rev.yt_tags if rev else (upload.tags if upload else None),
        selected_thumbnail_id=seg.selected_thumbnail_id,
        thumbnails=[ThumbnailOut(asset_id=a.id,position_idx=a.position_idx or 0,url=f'/assets/{a.id}/content') for a in thumbs],
        video_download_url=f'/segments/{seg.id}/download{suffix}' if playable else None,
        playback_url=f'/segments/{seg.id}/playback{suffix}' if playable else None,
        status=rev.status if rev else seg.status.value, revision=seg.revision,
        current_revision_id=seg.current_revision_id,media_revision=rev.number if rev else None,
        selection=seg.selection,selected=is_selected(seg),review_state=seg.review_state,
        rejection_reason=seg.rejection_reason,error=(rev.error if rev else seg.error),
        stages=rev.stages if rev else {},validation=rev.validation if rev else {},
        actual_duration_sec=rev.actual_duration_sec if rev else None,
        metadata_needs_review=rev.metadata_needs_review if rev else False,manual_fields=rev.manual_fields if rev else [],
    )


def export_out(export: Export):
    ready = export.status == 'succeeded'
    return ExportOut(id=export.id,job_id=export.job_id,status=export.status,error=export.error,
        created_at=export.created_at,completed_at=export.completed_at,clip_count=len(export.snapshot.get('clips', [])),
        download_url=f'/exports/{export.id}/download?format=zip' if ready else None,
        html_url=f'/exports/{export.id}/download?format=html' if ready else None,
        pdf_url=f'/exports/{export.id}/download?format=pdf' if ready else None,
        manifest_url=f'/exports/{export.id}/download?format=json' if ready else None)
