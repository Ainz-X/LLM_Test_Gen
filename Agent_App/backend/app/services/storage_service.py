from __future__ import annotations

from pathlib import Path

from minio import Minio

from app.core.config import settings


def minio_client() -> Minio | None:
    if not settings.minio_enabled:
        return None
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def put_object(local_path: Path, object_key: str) -> str | None:
    client = minio_client()
    if client is None:
        return None
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)
    client.fput_object(settings.minio_bucket, object_key, str(local_path))
    return f"{settings.minio_bucket}/{object_key}"


def remove_object(stored_object: str | None) -> None:
    client = minio_client()
    if client is None or not stored_object:
        return
    prefix = f"{settings.minio_bucket}/"
    object_key = stored_object[len(prefix) :] if stored_object.startswith(prefix) else stored_object
    try:
        client.remove_object(settings.minio_bucket, object_key)
    except Exception:
        pass
