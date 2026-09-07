"""Document storage.

Source PDFs live behind this interface rather than in the database, because
they are large, immutable once uploaded, and served directly to the viewer.

Two backends exist for one reason: MinIO needs Docker, and Docker needs WSL2,
which is not available on every machine this has to run on. The filesystem
backend keeps development possible there. Both satisfy the same protocol, so
nothing above this layer knows which is in use.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable


def content_hash(data: bytes) -> str:
    """SHA-256 of the raw bytes, used as the deduplication key.

    Re-uploading a document must not re-run the pipeline or re-spend tokens,
    and tender packs are commonly re-sent in full when a single file changes.
    """
    return hashlib.sha256(data).hexdigest()


@runtime_checkable
class DocumentStorage(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        """Store bytes and return the key they can be read back by."""
        ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None: ...

    async def url_for(self, key: str, *, expires_seconds: int = 3600) -> str:
        """A URL the viewer can load the document from.

        Time-limited where the backend supports it, since tender documents are
        commercially sensitive and a link that never expires is a leak waiting
        to happen.
        """
        ...


class StorageError(RuntimeError):
    """Raised when a document cannot be stored or retrieved."""
