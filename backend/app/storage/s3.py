"""S3-compatible document storage, backed by Supabase Storage."""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.storage.base import StorageError

logger = get_logger(__name__)


class S3Storage:
    def __init__(self, client: Any | None = None, bucket: str | None = None) -> None:
        settings = get_settings()
        self._bucket = bucket or settings.s3_bucket
        self._client = client or boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )

        try:
            await asyncio.to_thread(_put)
        except ClientError as exc:
            raise StorageError(f"Could not store {key}: {exc}") from exc

        logger.info("stored_document", key=key, bytes=len(data), content_type=content_type)
        return key

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
            return body

        try:
            return await asyncio.to_thread(_get)
        except ClientError as exc:
            raise StorageError(f"Could not read {key}: {exc}") from exc

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
                return True
            except ClientError:
                return False

        return await asyncio.to_thread(_head)

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            self._client.delete_object(Bucket=self._bucket, Key=key)

        await asyncio.to_thread(_delete)

    async def url_for(self, key: str, *, expires_seconds: int = 3600) -> str:
        def _sign() -> str:
            url: str = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_seconds,
            )
            return url

        return await asyncio.to_thread(_sign)
