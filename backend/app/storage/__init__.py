"""Storage backend selection."""

from functools import lru_cache

from app.core.config import get_settings
from app.storage.base import DocumentStorage, StorageError, content_hash

__all__ = ["DocumentStorage", "StorageError", "content_hash", "get_storage"]


@lru_cache
def get_storage() -> DocumentStorage:
    """Return the configured backend.

    The S3 client is imported lazily so that a machine running the filesystem
    backend does not need boto3 resolved at import time.
    """
    settings = get_settings()

    if settings.storage_backend == "filesystem":
        from app.storage.filesystem import FilesystemStorage

        return FilesystemStorage(settings.storage_path)

    from app.storage.s3 import S3Storage

    return S3Storage()
