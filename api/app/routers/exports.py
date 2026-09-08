from datetime import datetime, timezone
import uuid
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_session
from app.celery_client import enqueue_export
from app.models import Asset, AssetKind, Export, Job, Segment, SegmentRevision, Source
from app.schemas import ExportOut
from app.services.serialization import export_out, technical_ready
from app.services.ingestion import lock_key
from app.services import storage
from shared.policy import is_selected, validate_selected, PolicyError

router = APIRouter()


@router.post('/jobs/{job_id}/exports',response_model=ExportOut,status_code=201)
def create_export(job_id: uuid.UUID,idempotency_key: Annotated[str | None,Header(alias='Idempotency-Key',max_length=200)] = None,db: Session = Depends(get_session)):
    if idempotency_key:
        lock_key(db,'export:'+idempotency_key)
        prior = db.scalar(select(Export).where(Export.idempotency_key == idempotency_key))
        if prior:
            if prior.job_id != job_id:
                raise HTTPException(409,'Этот ключ экспорта уже использован для другого задания.')
            return export_out(prior)
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job:
        raise HTTPException(404,'Задание не найдено.')
    if not job.policy_snapshot or not job.source_id:
        raise HTTPException(409,'Исторические ролики не проверены по текущим правилам. Запустите новый анализ.')
    if job.progress.get('media_deferred'):
        raise HTTPException(409,'Разметка готова, но MP4 ещё не созданы. Можно скачать разметку JSON.')
    source = db.get(Source,job.source_id)
    segments = db.scalars(select(Segment).where(Segment.job_id == job_id).order_by(Segment.index).with_for_update()).all()
    selected = [s for s in segments if is_selected(s)]
    if not selected:
        raise HTTPException(409,'Нет выбранных эпизодов для экспорта.')
    try:
        validate_selected([{'start_sec':s.start_sec,'end_sec':s.end_sec} for s in selected],source.duration_sec)
    except PolicyError as exc:
        raise HTTPException(409,str(exc)) from None
    clips, blocked = [], []
    for seg in selected:
        rev = db.scalar(select(SegmentRevision).where(SegmentRevision.id == seg.current_revision_id).with_for_update()) if seg.current_revision_id else None
        if not rev or rev.status != 'ready' or not technical_ready(rev) or rev.validation.get('narrative',{}).get('ok') is not True:
            blocked.append(f'№{seg.index+1}: '+(rev.error or rev.status if rev else 'нет проверенной версии'))
            continue
        thumbs = db.scalars(select(Asset).where(Asset.revision_id == rev.id,Asset.kind == AssetKind.THUMBNAIL).order_by(Asset.position_idx)).all()
        chosen = next((t.s3_key for t in thumbs if t.id == seg.selected_thumbnail_id),None)
        clips.append(dict(segment_id=str(seg.id),revision_id=str(rev.id),revision=rev.number,index=seg.index,
            start_sec=rev.start_sec,end_sec=rev.end_sec,actual_duration_sec=rev.actual_duration_sec,title=seg.title,
            summary=seg.summary,yt_title=rev.yt_title,yt_description=rev.yt_description,yt_tags=rev.yt_tags or [],
            video_key=rev.video_key,thumbnail_keys=[t.s3_key for t in thumbs],selected_thumbnail_key=chosen,
            validation=rev.validation,metadata_needs_review=rev.metadata_needs_review))
    if blocked:
        raise HTTPException(409,'Экспорт не создан: выбранные версии ещё не готовы. '+ '; '.join(blocked))
    snapshot = dict(schema_version=1,source=dict(id=str(source.id),title=source.title,source_url=source.source_url,duration_sec=source.duration_sec),
        job=dict(id=str(job.id),analysis_version=job.analysis_version,policy_snapshot=job.policy_snapshot,transcript_snapshot=job.transcript_snapshot,created_at=job.created_at.isoformat()),clips=clips)
    export = Export(job_id=job.id,snapshot=snapshot,attempt_id=uuid.uuid4(),idempotency_key=idempotency_key)
    db.add(export)
    db.commit()
    try:
        enqueue_export(export.id,export.attempt_id)
    except Exception:
        export.status = 'failed'
        export.error = 'Обработчик недоступен. Создайте экспорт повторно после восстановления служб.'
        db.commit()
    return export_out(export)


@router.get('/jobs/{job_id}/exports')
def list_exports(job_id: uuid.UUID,db: Session = Depends(get_session)):
    return {'items':[export_out(e) for e in db.scalars(select(Export).where(Export.job_id == job_id).order_by(Export.created_at.desc()))]}


@router.get('/exports/{export_id}',response_model=ExportOut)
def get_export(export_id: uuid.UUID,db: Session = Depends(get_session)):
    export = db.get(Export,export_id)
    if not export:
        raise HTTPException(404,'Экспорт не найден.')
    return export_out(export)


@router.api_route('/exports/{export_id}/download',methods=['GET','HEAD'])
def download_export(export_id: uuid.UUID,request: Request,format: Literal['zip','html','pdf','json']='zip',db: Session = Depends(get_session)):
    export = db.get(Export,export_id)
    if not export:
        raise HTTPException(404,'Экспорт не найден.')
    if export.status != 'succeeded':
        raise HTTPException(409,'Экспорт ещё не готов.')
    key = getattr(export, {'zip':'zip_key','html':'html_key','pdf':'pdf_key','json':'manifest_key'}[format])
    if not key:
        raise HTTPException(409,'Файл экспорта отсутствует.')
    filename = {'zip':f'video-slicer-{export.id}.zip','html':'index.html','pdf':'report.pdf','json':'manifest.json'}[format]
    return storage.media_response(key,request,filename)
