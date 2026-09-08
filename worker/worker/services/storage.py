from __future__ import annotations

from pathlib import Path

import boto3
from botocore.config import Config

from worker.config import settings


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.minio_endpoint,
        aws_access_key_id=settings.minio_root_user,
        aws_secret_access_key=settings.minio_root_password,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket() -> None:
    c = _client()
    existing = {b["Name"] for b in c.list_buckets().get("Buckets", [])}
    if settings.minio_bucket not in existing:
        c.create_bucket(Bucket=settings.minio_bucket)


def upload_file(local_path: Path, key: str, content_type: str) -> int:
    ensure_bucket()
    c = _client()
    c.upload_file(
        str(local_path),
        settings.minio_bucket,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    return local_path.stat().st_size


def upload_bytes(body: bytes, key: str, content_type: str) -> int:
    ensure_bucket()
    c = _client()
    c.put_object(Bucket=settings.minio_bucket, Key=key, Body=body, ContentType=content_type)
    return len(body)


def download_file(key: str, local_path: Path) -> None:
    c = _client()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    c.download_file(settings.minio_bucket, key, str(local_path))


def download_bytes(key: str) -> bytes:
    c = _client()
    obj = c.get_object(Bucket=settings.minio_bucket, Key=key)
    return obj["Body"].read()


def object_exists(key: str) -> bool:
    try:
        _client().head_object(Bucket=settings.minio_bucket, Key=key)
        return True
    except Exception as exc:
        response = getattr(exc, "response", {})
        code = str(response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def copy_object(source_key: str, destination_key: str, content_type: str | None = None) -> None:
    ensure_bucket()
    args = {
        "Bucket": settings.minio_bucket,
        "Key": destination_key,
        "CopySource": {"Bucket": settings.minio_bucket, "Key": source_key},
        "MetadataDirective": "COPY",
    }
    if content_type:
        args.update(ContentType=content_type, MetadataDirective="REPLACE")
    _client().copy_object(**args)
