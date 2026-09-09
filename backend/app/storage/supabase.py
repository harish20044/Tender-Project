"""Supabase Storage backend, for the hosted document archive.

Supabase is Postgres plus object storage behind one project URL. Documents go
to its Storage service over the same REST interface its dashboard uses, so no
additional client library is needed — httpx, already a dependency, is enough.

Buckets are created in the Supabase dashboard (Storage → New bucket). A public
bucket serves documents at stable URLs; a private one hands out time-limited
signed URLs instead, which is the production posture for commercially
sensitive tender packs.
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.storage.base import StorageError

logger = get_logger(__name__)


class SupabaseStorage:
    """DocumentStorage against one Supabase project's Storage service."""

    def __init__(
        self,
        base_url: str | None = None,
        service_key: str | None = None,
        bucket: str | None = None,
        *,
        public_bucket: bool | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self._base = (base_url or settings.supabase_url).rstrip("/")
        self._key = service_key or settings.supabase_service_role_key
        self._bucket = bucket or settings.supabase_bucket
        self._public = settings.supabase_public_bucket if public_bucket is None else public_bucket
        self._client = client or httpx.Client(timeout=60.0)
        if not self._base or not self._key:
            raise StorageError(
                "Supabase storage needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY configured."
            )

    def _headers(self, content_type: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "apikey": self._key,
            "Content-Type": content_type,
            # Re-archiving a corrected pack must not collide with the first.
            "x-upsert": "true",
        }

    def _object_url(self, key: str) -> str:
        return f"{self._base}/storage/v1/object/{self._bucket}/{key}"

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        def _put() -> None:
            response = self._client.post(
                self._object_url(key), content=data, headers=self._headers(content_type)
            )
            if response.status_code not in (200, 201):
                raise StorageError(
                    f"Supabase upload failed for {key}: "
                    f"{response.status_code} {response.text[:200]}"
                )

        try:
            await asyncio.to_thread(_put)
        except httpx.HTTPError as exc:
            raise StorageError(f"Could not store {key} in Supabase: {exc}") from exc

        logger.info("stored_document", key=key, bytes=len(data), content_type=content_type)
        return key

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            response = self._client.get(
                self._object_url(key), headers=self._headers("application/octet-stream")
            )
            if response.status_code != 200:
                raise StorageError(f"Supabase read failed for {key}: {response.status_code}")
            return response.content

        try:
            return await asyncio.to_thread(_get)
        except httpx.HTTPError as exc:
            raise StorageError(f"Could not read {key} from Supabase: {exc}") from exc

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            response = self._client.head(
                self._object_url(key), headers=self._headers("application/octet-stream")
            )
            return response.status_code == 200

        try:
            return await asyncio.to_thread(_head)
        except httpx.HTTPError:
            return False

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            response = self._client.delete(
                self._object_url(key), headers=self._headers("application/octet-stream")
            )
            if response.status_code not in (200, 204):
                raise StorageError(f"Supabase delete failed for {key}: {response.status_code}")

        try:
            await asyncio.to_thread(_delete)
        except httpx.HTTPError as exc:
            raise StorageError(f"Could not delete {key} from Supabase: {exc}") from exc

    async def url_for(self, key: str, *, expires_seconds: int = 3600) -> str:
        """A public URL, or a signed one when the bucket is private."""
        if self._public:
            return f"{self._base}/storage/v1/object/public/{self._bucket}/{key}"

        def _sign() -> str:
            response = self._client.post(
                f"{self._base}/storage/v1/object/sign/{self._bucket}/{key}",
                json={"expiresIn": expires_seconds},
                headers=self._headers("application/json"),
            )
            if response.status_code != 200:
                raise StorageError(f"Supabase signing failed for {key}: {response.status_code}")
            path = str(response.json().get("signedURL", ""))
            return f"{self._base}{path}" if path.startswith("/") else path

        try:
            return await asyncio.to_thread(_sign)
        except httpx.HTTPError as exc:
            raise StorageError(f"Could not sign {key} in Supabase: {exc}") from exc
