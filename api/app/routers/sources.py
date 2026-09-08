import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Source, Job
from app.schemas import SourceOut
from app.services.serialization import source_out
from app.services import storage

router = APIRouter()


def _source(source_id,db):
    source = db.get(Source,source_id)
    if not source:
        raise HTTPException(404,'Исходник не найден.')
    return source


@router.get('/sources')
def sources(db: Session = Depends(get_session)):
    return {'items':[source_out(s) for s in db.scalars(select(Source).order_by(Source.created_at.desc()))]}


@router.get('/sources/{source_id}',response_model=SourceOut)
def get_source(source_id: uuid.UUID,db: Session = Depends(get_session)):
    return source_out(_source(source_id,db))


def _transcript(key,version):
    if not key:
        return {'version':version,'cues':[]}
    try:
        data = storage.read_json(key)
    except Exception:
        raise HTTPException(503,'Не удалось загрузить расшифровку. Повторите попытку.') from None
    cues = data if isinstance(data,list) else data.get('cues',data.get('segments',[]))
    return {'version':version,'cues':cues}


@router.get('/sources/{source_id}/transcript')
def source_transcript(source_id: uuid.UUID,db: Session = Depends(get_session)):
    source = _source(source_id,db)
    return _transcript(source.transcript_key,source.transcript_version)


@router.get('/jobs/{job_id}/transcript')
def job_transcript(job_id: uuid.UUID,db: Session = Depends(get_session)):
    job = db.get(Job,job_id)
    if not job:
        raise HTTPException(404,'Задание не найдено.')
    snapshot = job.transcript_snapshot or {}
    return _transcript(snapshot.get('key'),snapshot.get('version'))


@router.api_route('/sources/{source_id}/preview',methods=['GET','HEAD'])
def source_preview(source_id: uuid.UUID,request: Request,db: Session = Depends(get_session)):
    source = _source(source_id,db)
    if not source.preview_key or source.preview_status != 'ready':
        raise HTTPException(409,'Браузерное превью ещё не готово.')
    return storage.media_response(source.preview_key,request)
