"""Complete uploads only: bounded disk spool, container validation, multipart S3 commit."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import uuid

from fastapi import HTTPException, UploadFile
from sqlalchemy import select, text, func
from sqlalchemy.dialects.postgresql import insert
from app.models import Source, Job
from app.config import settings
from app.services import storage
from shared.policy import MAX_UPLOAD_BYTES, youtube_id, PolicyError


def lock_key(db, key):
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'), {'key':key})


def url_source(db, url):
    try:
        video_id = youtube_id(url)
    except PolicyError as exc:
        raise HTTPException(422, str(exc)) from None
    identity = 'youtube:'+video_id
    canonical = 'https://www.youtube.com/watch?v='+video_id
    db.execute(insert(Source).values(id=uuid.uuid4(), source_key=identity,source_type='url',source_url=canonical,title='YouTube · '+video_id).on_conflict_do_nothing(index_elements=['source_key']))
    return db.scalar(select(Source).where(Source.source_key == identity).with_for_update())


def probe_upload(file: UploadFile):
    # fileno forces the standard bounded spool to disk; no second full file copy.
    file.file.seek(0,2)
    size = file.file.tell()
    file.file.seek(0)
    if not size:
        raise HTTPException(422, 'Файл пуст.')
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, 'Максимальный размер файла — 10 ГБ.')
    fd = file.file.fileno()
    path = f'/dev/fd/{fd}' if sys.platform == 'darwin' else f'/proc/self/fd/{fd}'
    try:
        result = subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',path],pass_fds=(fd,),capture_output=True,text=True,timeout=60,check=True)
        info = json.loads(result.stdout)
        if not any(s.get('codec_type') == 'video' for s in info.get('streams',[])):
            raise ValueError('no video')
        duration = float(info['format']['duration'])
        if not 0 < duration < float('inf'):
            raise ValueError('invalid duration')
    except FileNotFoundError:
        raise HTTPException(503, 'ffprobe недоступен в API; обновите локальный запуск.') from None
    except (subprocess.SubprocessError,ValueError,KeyError):
        raise HTTPException(422, 'Не удалось прочитать видеоконтейнер. Файл повреждён или не содержит видео.') from None
    file.file.seek(0)
    return size, duration


def upload_source(db, file):
    size, duration = probe_upload(file)
    storage.ensure_bucket()
    client = storage._client()
    temporary = f'sources/uploads/{uuid.uuid4()}/original'
    bucket = settings.minio_bucket
    mime = file.content_type if file.content_type and file.content_type.startswith('video/') else 'application/octet-stream'
    upload_id = client.create_multipart_upload(Bucket=bucket,Key=temporary,ContentType=mime)['UploadId']
    digest = sha256()
    parts, received = [], 0
    try:
        while chunk := file.file.read(8*1024*1024):
            received += len(chunk)
            if received > MAX_UPLOAD_BYTES:
                raise HTTPException(413, 'Максимальный размер файла — 10 ГБ.')
            digest.update(chunk)
            number = len(parts)+1
            part = client.upload_part(Bucket=bucket,Key=temporary,UploadId=upload_id,PartNumber=number,Body=chunk)
            parts.append({'PartNumber':number,'ETag':part['ETag']})
        if received != size:
            raise HTTPException(422, 'Загрузка оборвалась. Выберите файл и загрузите его заново.')
        client.complete_multipart_upload(Bucket=bucket,Key=temporary,UploadId=upload_id,MultipartUpload={'Parts':parts})
    except BaseException:
        client.abort_multipart_upload(Bucket=bucket,Key=temporary,UploadId=upload_id)
        raise
    identity = 'sha256:'+digest.hexdigest()
    lock_key(db, identity)
    source = db.scalar(select(Source).where(Source.source_key == identity).with_for_update())
    if source:
        from botocore.exceptions import ClientError
        cached = False
        if source.original_key and source.status != 'failed':
            try:
                cached = client.head_object(Bucket=bucket,Key=source.original_key)['ContentLength'] == size
            except ClientError as exc:
                if exc.response['Error']['Code'] not in ('404','NoSuchKey','NotFound'):
                    raise
        if not cached:
            old_key = source.original_key
            source.original_key,source.original_mime,source.size_bytes = temporary,mime,size
            source.duration_sec,source.status,source.error = duration,'pending',None
            if source.preview_key == old_key or source.preview_status != 'ready':
                source.preview_key,source.preview_status = None,'pending'
            return source
        client.delete_object(Bucket=bucket,Key=temporary)
        return source
    filename = Path(file.filename or 'video').name
    source = Source(source_key=identity, source_type='file',filename=filename,title=filename,
        original_key=temporary,original_mime=mime,size_bytes=size,duration_sec=duration,content_hash=digest.hexdigest())
    db.add(source)
    db.flush()
    return source
