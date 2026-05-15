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
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def presigned_get_url(key: str, expires_in: int = 3600) -> str:
    public = settings.minio_public_endpoint or settings.minio_endpoint
    return _client(public).generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.minio_bucket, "Key": key},
        ExpiresIn=expires_in,
    )
