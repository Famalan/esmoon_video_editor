from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.celery_client import enqueue_pipeline
from app.db import get_session
from app.models import Job, JobStatus, Segment, SegmentRevision, Source, SourceType
from app.schemas import JobCreate, RerunCreate, JobOut, JobsList
from app.services.ingestion import lock_key, url_source, upload_source
from app.services.serialization import job_out
from shared.policy import is_selected, policy_snapshot
from shared.stages import Stage

router = APIRouter(prefix='/jobs', tags=['jobs'])
Key = Annotated[str | None, Header(alias='Idempotency-Key', max_length=200)]
User = Annotated[str, Header(alias='X-User', max_length=100)]


def _fingerprint(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()


def _existing(db, key, fingerprint):
    if not key:
        return None
    lock_key(db,'request:'+key)
    existing = db.scalar(select(Job).where(Job.idempotency_key == key))
    if existing and existing.request_hash != fingerprint:
        raise HTTPException(409, 'Этот ключ запуска уже использован для другого запроса.')
    return existing


def _dispatch(job, db):
    try:
        enqueue_pipeline(str(job.id),str(job.attempt_id))
    except Exception:
        job.status = JobStatus.FAILED
        job.error = 'Не удалось передать задание обработчику. Проверьте службы и нажмите «Повторить».'
        db.commit()


def _persist_job(db, source, user, topic=None, audience=None, parent=None, key=None, fingerprint=None):
    # All creators lock the source: run numbers remain unique across simultaneous requests.
    source = db.scalar(select(Source).where(Source.id == source.id).with_for_update())
    version = (db.scalar(select(func.max(Job.analysis_version)).where(Job.source_id == source.id)) or 0)+1
    job = Job(source_id=source.id,source_type=SourceType(source.source_type),source_url=source.source_url,
        analysis_version=version,parent_job_id=parent,policy_snapshot=policy_snapshot(),topic=topic,audience=audience,
        attempt_id=uuid.uuid4(),attempt_no=1,idempotency_key=key,request_hash=fingerprint,
        status=JobStatus.QUEUED,current_stage=Stage.FETCH,created_by=user,last_activity_at=datetime.now(timezone.utc),
        progress={'stage':'fetch','detail':'Ожидает обработчика','completed':0,'total':0,'percent':0})
    db.add(job)
    db.commit()
    _dispatch(job,db)
    return job_out(job,db)


@router.post('', response_model=JobOut, status_code=201)
def create_job_json(payload: JobCreate, idempotency_key: Key = None, user: User = 'anonymous', db: Session = Depends(get_session)):
    fingerprint = _fingerprint(payload.model_dump(mode='json'))
    key = _fingerprint({'user':user,'key':idempotency_key}) if idempotency_key else None
    existing = _existing(db,key,fingerprint)
    if existing:
        return job_out(existing,db)
    if payload.source_id:
        if payload.source_url:
            raise HTTPException(422,'Передайте source_id или ссылку, но не оба значения.')
        source = db.get(Source,payload.source_id)
        if not source:
            raise HTTPException(404,'Исходник не найден.')
        if payload.source_type and payload.source_type.value != source.source_type:
            raise HTTPException(422,'Тип исходника не совпадает.')
    else:
        if payload.source_type == SourceType.FILE or not payload.source_url:
            raise HTTPException(422,'Для файла используйте загрузку. Для YouTube укажите ссылку.')
        source = url_source(db,payload.source_url)
    return _persist_job(db,source,user,payload.topic,payload.audience,key=key,fingerprint=fingerprint)


@router.post('/upload', response_model=JobOut, status_code=201)
def create_job_upload(file: Annotated[UploadFile,File()], topic: Annotated[str | None,Form(max_length=4000)] = None,
    audience: Annotated[str | None,Form(max_length=4000)] = None, idempotency_key: Key = None,
    user: User = 'anonymous', db: Session = Depends(get_session)):
    # Hash the actual complete file; a reused key with different contents must conflict.
    key = _fingerprint({'user':user,'key':idempotency_key}) if idempotency_key else None
    if key:
        lock_key(db,'request:'+key)
    source = upload_source(db,file)
    fingerprint = _fingerprint({'source_id':str(source.id),'topic':topic,'audience':audience})
    existing = _existing(db,key,fingerprint)
    if existing:
        db.rollback()
        return job_out(existing,db)
    return _persist_job(db,source,user,topic,audience,key=key,fingerprint=fingerprint)


@router.get('', response_model=JobsList)
def list_jobs(db: Session = Depends(get_session)):
    rows = db.scalars(select(Job).order_by(Job.created_at.desc())).all()
    return JobsList(items=[job_out(j,db) for j in rows])


@router.get('/{job_id}', response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(Job,job_id)
    if not job:
        raise HTTPException(404,'Задание не найдено.')
    return job_out(job,db)


@router.post('/{job_id}/rerun', response_model=JobOut, status_code=201)
def rerun(job_id: uuid.UUID,payload: RerunCreate = RerunCreate(),idempotency_key: Key = None,
    user: User = 'anonymous',db: Session = Depends(get_session)):
    original = db.get(Job,job_id)
    if not original:
        raise HTTPException(404,'Задание не найдено.')
    fingerprint = _fingerprint({'parent':str(job_id),**payload.model_dump(mode='json')})
    key = _fingerprint({'user':user,'key':idempotency_key}) if idempotency_key else None
    existing = _existing(db,key,fingerprint)
    if existing:
        return job_out(existing,db)
    source = db.get(Source,original.source_id) if original.source_id else None
    if not source:
        if not original.source_url:
            raise HTTPException(422,'В старом задании исходный файл не сохранён. Загрузите его заново.')
        source = url_source(db,original.source_url)
    topic = payload.topic if 'topic' in payload.model_fields_set else original.topic
    audience = payload.audience if 'audience' in payload.model_fields_set else original.audience
    return _persist_job(db,source,user,topic,audience,parent=original.id,key=key,fingerprint=fingerprint)


@router.post('/{job_id}/retry', response_model=JobOut)
def retry_job(job_id: uuid.UUID,db: Session = Depends(get_session)):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job:
        raise HTTPException(404,'Задание не найдено.')
    if not job.policy_snapshot:
        raise HTTPException(409,'Для исторического задания запустите новый анализ по текущим правилам.')
    now = datetime.now(timezone.utc)
    live = job.last_activity_at and job.last_activity_at > now-timedelta(seconds=120)
    if job.status in (JobStatus.QUEUED,JobStatus.RUNNING) and live:
        return job_out(job,db)
    if job.status == JobStatus.SUCCEEDED:
        return job_out(job,db)
    job.attempt_id = uuid.uuid4()
    job.attempt_no += 1
    job.status = JobStatus.QUEUED
    job.error = None
    job.last_activity_at = now
    job.progress = {**job.progress,'detail':'Повтор неудавшихся этапов','retry':True}
    for seg in db.scalars(select(Segment).where(Segment.job_id == job.id)):
        rev = db.get(SegmentRevision,seg.current_revision_id) if seg.current_revision_id else None
        if is_selected(seg) and rev and rev.status not in ('ready','analyzed'):
            rev.attempt_id = uuid.uuid4()
            rev.status = 'queued'
            rev.error = None
    db.commit()
    _dispatch(job,db)
    return job_out(job,db)


@router.get('/{job_id}/analysis/download')
def download_analysis(job_id: uuid.UUID,db: Session = Depends(get_session)):
    from fastapi.responses import JSONResponse
    from app.services.serialization import segment_out
    job = db.get(Job,job_id)
    if not job:
        raise HTTPException(404,'Задание не найдено.')
    rows = db.scalars(select(Segment).where(Segment.job_id == job.id).order_by(Segment.index)).all()
    if not job.progress.get('analysis_complete'):
        raise HTTPException(409,'Разметка ещё не завершена.')
    payload = {'job':job_out(job,db).model_dump(mode='json'),
        'kind':'transcript_analysis','actual_mp4_duration_verified':False if job.progress.get('media_deferred') else None,
        'segments':[segment_out(s,db).model_dump(mode='json') for s in rows if is_selected(s)]}
    return JSONResponse(payload,headers={'Content-Disposition':f'attachment; filename="analysis-{job.id}.json"'})
