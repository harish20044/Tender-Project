"""Filesystem-backed document storage, for development without Docker.

Not intended for production: there are no expiring URLs, no replication, and
files are served by the API process rather than by object storage. It exists
so the pipeline can be developed and tested on a machine that cannot run
MinIO.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.logging import get_logger
from app.storage.base import StorageError

logger = get_logger(__name__)


class FilesystemStorage:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        logger.info("filesystem_storage_ready", root=str(self._root))

    def _path_for(self, key: str) -> Path:
        # Keys are built internally, but they end up in URLs, so a traversal
        # attempt must not be able to escape the storage root.
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root):
            raise StorageError(f"Refusing to access a path outside the storage root: {key}")
        return candidate

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        path = self._path_for(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a sibling then move, so a crash mid-write cannot leave a
            # half-written document that later reads as valid.
            temp = path.with_suffix(path.suffix + ".partial")
            temp.write_bytes(data)
            temp.replace(path)

        await asyncio.to_thread(_write)
        logger.info("stored_document", key=key, bytes=len(data), content_type=content_type)
        return key

    async def get(self, key: str) -> bytes:
        path = self._path_for(key)

        def _read() -> bytes:
            if not path.is_file():
                raise StorageError(f"No stored document for key: {key}")
            return path.read_bytes()

        return await asyncio.to_thread(_read)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path_for(key).is_file)

    async def delete(self, key: str) -> None:
        path = self._path_for(key)
        await asyncio.to_thread(path.unlink, True)

    async def url_for(self, key: str, *, expires_seconds: int = 3600) -> str:
        """Served by the API rather than by object storage.

        The expiry is accepted and ignored, since this backend has no way to
        honour it. Another reason it is development-only.
        """
        del expires_seconds
        return f"/api/documents/raw/{key}"
