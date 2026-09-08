from __future__ import annotations

import boto3
from botocore.config import Config

from app.config import settings


def _client(endpoint_url: str | None = None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or settings.minio_endpoint,
        aws_access_key_id=settings.minio_root_user,
        aws_secret_access_key=settings.minio_root_password,
        config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}),
        region_name="us-east-1",
    )


def presigned_get_url(
    key: str,
    expires_in: int = 3600,
    download_filename: str | None = None,
) -> str:
    public = settings.minio_public_endpoint or settings.minio_endpoint
    params: dict = {"Bucket": settings.minio_bucket, "Key": key}
    if download_filename:
        safe = download_filename.replace('"', "")
        params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
    return _client(public).generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expires_in,
    )


def ensure_bucket():
    from botocore.exceptions import ClientError
    client = _client()
    try:
        client.head_bucket(Bucket=settings.minio_bucket)
    except ClientError as exc:
        if exc.response['Error']['Code'] not in ('404', 'NoSuchBucket'):
            raise
        client.create_bucket(Bucket=settings.minio_bucket)


def read_json(key):
    import json
    body = _client().get_object(Bucket=settings.minio_bucket, Key=key)['Body']
    try:
        return json.loads(body.read())
    finally:
        body.close()


def media_response(key, request, filename=None):
    """Proxy a bounded single HTTP byte range; stable URLs never expire mid-playback."""
    from fastapi import HTTPException, Response
    from fastapi.responses import StreamingResponse
    from botocore.exceptions import ClientError
    from urllib.parse import quote
    import re
    client = _client()
    try:
        info = client.head_object(Bucket=settings.minio_bucket, Key=key)
    except ClientError as exc:
        if exc.response['Error']['Code'] in ('404','NoSuchKey','NotFound'):
            raise HTTPException(404, 'Файл не найден в хранилище.') from None
        raise HTTPException(503, 'Хранилище недоступно.') from None
    size = info['ContentLength']
    start, end, status = 0, size - 1, 200
    requested = request.headers.get('range')
    if requested:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
        if not match or not any(match.groups()):
            raise HTTPException(416, 'Недопустимый диапазон.', headers={'Content-Range':f'bytes */{size}'})
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last), size-1) if last else size-1
        else:
            start = max(0,size-int(last))
        if start >= size or start < 0 or end < start:
            raise HTTPException(416, 'Диапазон вне файла.', headers={'Content-Range':f'bytes */{size}'})
        status = 206
    headers = {'Accept-Ranges':'bytes','Content-Length':str(max(0,end-start+1)), 'ETag':info.get('ETag',''), 'Cache-Control':'private, max-age=0'}
    if status == 206:
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    if filename:
        headers['Content-Disposition'] = "attachment; filename=download; filename*=UTF-8''"+quote(filename, safe='')
    media_type = info.get('ContentType','application/octet-stream')
    if request.method == 'HEAD':
        return Response(status_code=status,headers=headers,media_type=media_type)
    kwargs = {'Bucket':settings.minio_bucket,'Key':key}
    if status == 206:
        kwargs['Range'] = f'bytes={start}-{end}'
    body = client.get_object(**kwargs)['Body']
    def chunks():
        try:
            yield from body.iter_chunks(chunk_size=1024*1024)
        finally:
            body.close()
    return StreamingResponse(chunks(),status_code=status,headers=headers,media_type=media_type)
