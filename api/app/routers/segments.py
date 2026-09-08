from __future__ import annotations

from datetime import datetime, timezone
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.celery_client import enqueue_revision
from app.db import get_session
from app.models import Asset, AssetKind, Job, JobStatus, Segment, SegmentRevision, SegmentStatus, Source, Upload
from app.schemas import SegmentOut, SegmentPatch, SegmentsList
from app.services import storage
from app.services.serialization import segment_out, technical_ready
from shared.policy import PolicyError, is_selected, validate_interval, validate_selected, analysis_duration

router = APIRouter()
# Retained for import compatibility with existing integrations.
_segment_to_out = segment_out


def _get_segment(segment_id, db):
    segment = db.get(Segment,segment_id)
    if not segment:
        raise HTTPException(404,'Эпизод не найден.')
    return segment


def _new_revision(seg, old, db):
    number = (db.scalar(select(func.max(SegmentRevision.number)).where(SegmentRevision.segment_id == seg.id)) or 0)+1
    manual = list(old.manual_fields) if old else []
    rev = SegmentRevision(segment_id=seg.id,number=number,start_sec=seg.start_sec,end_sec=seg.end_sec,
        attempt_id=uuid.uuid4(),manual_fields=manual,metadata_needs_review=bool(manual),
        stages={name:'pending' for name in ('render','verify','thumbnail','metadata')})
    for field in manual:
        if field in ('yt_title','yt_description','yt_tags'):
            setattr(rev,field,getattr(old,field))
    db.add(rev)
    db.flush()
    seg.current_revision_id = rev.id
    seg.selected_thumbnail_id = None
    seg.status = SegmentStatus.PENDING
    seg.error = None
    seg.review_state = 'unreviewed'
    return rev


@router.get('/jobs/{job_id}/segments', response_model=SegmentsList)
def list_segments(job_id: uuid.UUID,db: Session = Depends(get_session)):
    if not db.get(Job,job_id):
        raise HTTPException(404,'Задание не найдено.')
    rows = db.scalars(select(Segment).where(Segment.job_id == job_id).order_by(Segment.index)).all()
    return SegmentsList(items=[segment_out(s,db) for s in rows])


@router.get('/segments/{segment_id}', response_model=SegmentOut)
def get_segment(segment_id: uuid.UUID,db: Session = Depends(get_session)):
    return segment_out(_get_segment(segment_id,db),db)


@router.patch('/segments/{segment_id}',response_model=SegmentOut)
def patch_segment(segment_id: uuid.UUID,body: SegmentPatch,db: Session = Depends(get_session)):
    seg = _get_segment(segment_id,db)
    # Job lock serializes overlapping edits on different candidates as well.
    job = db.scalar(select(Job).where(Job.id == seg.job_id).with_for_update())
    db.refresh(seg,with_for_update=True)
    if body.expected_revision != seg.revision:
        raise HTTPException(409,'Эпизод уже изменён. Обновите карточку и повторите правку.')
    fields = body.model_fields_set-{'expected_revision'}
    if not fields:
        return segment_out(seg,db)
    bounds = bool(fields & {'start_sec','end_sec'})
    if any(getattr(body,field) is None for field in fields & {'start_sec','end_sec','selection','review_state','yt_title','yt_description','yt_tags'}):
        raise HTTPException(422,'Значение правки не может быть null.')
    if (bounds or 'selection' in fields) and not job.policy_snapshot:
        raise HTTPException(409,'Границы старого анализа проверены по неизвестным правилам. Сначала запустите новый анализ.')
    source = db.get(Source,job.source_id) if job.source_id else None
    previous_bounds = (seg.start_sec,seg.end_sec)
    previously_selected = is_selected(seg)
    if 'start_sec' in fields:
        seg.start_sec = body.start_sec
    if 'end_sec' in fields:
        seg.end_sec = body.end_sec
    if body.selection is not None:
        seg.selection = body.selection
    if bounds or 'selection' in fields:
        try:
            duration = analysis_duration(job,source)
            if bounds:
                validate_interval(seg.start_sec,seg.end_sec,duration)
            candidates = db.scalars(select(Segment).where(Segment.job_id == job.id)).all()
            validate_selected([{'start_sec':s.start_sec,'end_sec':s.end_sec} for s in candidates if is_selected(s)],duration)
        except PolicyError as exc:
            raise HTTPException(422,str(exc)) from None
    old = db.scalar(select(SegmentRevision).where(SegmentRevision.id == seg.current_revision_id).with_for_update()) if seg.current_revision_id else None
    changed_bounds = previous_bounds != (seg.start_sec,seg.end_sec)
    new_media = changed_bounds or (is_selected(seg) and old is None and job.policy_snapshot is not None)
    rev = _new_revision(seg,old,db) if new_media else old
    render = bool(new_media or (not previously_selected and is_selected(seg) and rev and rev.status in ('queued','failed','rejected')))
    if render and not new_media:
        rev.attempt_id = uuid.uuid4()
        rev.status = 'queued'
        rev.error = None
    if 'selected_thumbnail_id' in fields:
        if body.selected_thumbnail_id is None:
            seg.selected_thumbnail_id = None
        else:
            thumb = db.get(Asset,body.selected_thumbnail_id)
            if not thumb or thumb.segment_id != seg.id or thumb.kind != AssetKind.THUMBNAIL or thumb.revision_id != seg.current_revision_id:
                raise HTTPException(422,'Превью не принадлежит текущей версии ролика.')
            seg.selected_thumbnail_id = thumb.id
    metadata = fields & {'yt_title','yt_description','yt_tags'}
    if metadata:
        if rev:
            for field in metadata:
                setattr(rev,field,getattr(body,field))
            rev.manual_fields = sorted(set(rev.manual_fields)|metadata)
        else:
            upload = db.scalar(select(Upload).where(Upload.segment_id == seg.id))
            if not upload:
                upload = Upload(segment_id=seg.id)
                db.add(upload)
            mapping = {'yt_title':'youtube_title','yt_description':'youtube_description','yt_tags':'tags'}
            for field in metadata:
                setattr(upload,mapping[field],getattr(body,field))
    if body.metadata_needs_review is False and rev:
        rev.metadata_needs_review = False
    if body.review_state:
        seg.review_state = body.review_state
    seg.revision += 1
    if render:
        job.status = JobStatus.RUNNING
        job.last_activity_at = datetime.now(timezone.utc)
        label = 'Проверка границ' if job.progress.get('media_deferred') else 'Пересоздание'
        job.progress = {**job.progress,'detail':f'{label} эпизода {seg.index+1}'}
    db.commit()
    if render:
        try:
            enqueue_revision(seg.id,rev.id,rev.attempt_id)
        except Exception:
            rev.status = 'failed'
            rev.error = 'Обработчик недоступен. Повторите неудавшиеся этапы.'
            job.status = JobStatus.PARTIAL
            db.commit()
    return segment_out(seg,db)


def _video(seg,revision_id,db):
    target = revision_id or seg.current_revision_id
    if target:
        rev = db.get(SegmentRevision,target)
        if not rev or rev.segment_id != seg.id:
            raise HTTPException(404,'Версия эпизода не найдена.')
        if not technical_ready(rev):
            raise HTTPException(409,'Эта версия ещё не прошла техническую проверку.')
        return rev.video_key
    # Explicit legacy path; no claim of v1 validation.
    asset = db.scalar(select(Asset).where(Asset.segment_id == seg.id,Asset.kind == AssetKind.SEGMENT_VIDEO,Asset.revision_id.is_(None)).order_by(Asset.id).limit(1))
    if not asset:
        raise HTTPException(404,'Видеофайл ещё не готов.')
    return asset.s3_key


@router.api_route('/segments/{segment_id}/download',methods=['GET','HEAD'])
def download_segment(segment_id: uuid.UUID,request: Request,revision_id: uuid.UUID | None = None,db: Session = Depends(get_session)):
    seg = _get_segment(segment_id,db)
    key = _video(seg,revision_id,db)
    title = (seg.title or f'Эпизод {seg.index+1}').strip()[:80]
    return storage.media_response(key,request,f'{seg.index+1:02d}_{title}.mp4')


@router.api_route('/segments/{segment_id}/playback',methods=['GET','HEAD'])
def playback_segment(segment_id: uuid.UUID,request: Request,revision_id: uuid.UUID | None = None,db: Session = Depends(get_session)):
    return storage.media_response(_video(_get_segment(segment_id,db),revision_id,db),request)


@router.get('/segments/{segment_id}/revisions')
def list_revisions(segment_id: uuid.UUID,db: Session = Depends(get_session)):
    _get_segment(segment_id,db)
    rows = db.scalars(select(SegmentRevision).where(SegmentRevision.segment_id == segment_id).order_by(SegmentRevision.number.desc())).all()
    return {'items':[dict(id=r.id,number=r.number,start_sec=r.start_sec,end_sec=r.end_sec,status=r.status,
        actual_duration_sec=r.actual_duration_sec,validation=r.validation,created_at=r.created_at,
        video_download_url=f'/segments/{segment_id}/download?revision_id={r.id}' if technical_ready(r) else None,
        playback_url=f'/segments/{segment_id}/playback?revision_id={r.id}' if technical_ready(r) else None) for r in rows]}
