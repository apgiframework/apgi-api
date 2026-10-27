"""Object storage for acquisition chunks (private bucket, V4 signed upload URLs).

The google-cloud-storage client is imported lazily so local development and unit tests
never need it. ``settings.acquisition_bucket`` unset disables the feature (the routes
answer 503) rather than silently writing nowhere.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)


class AcquisitionObjectStore(ABC):
    """What the ingest service needs from a bucket."""

    @abstractmethod
    def signed_upload_url(self, key: str, *, size_bytes: int, expires_in_s: int) -> str:
        """Return a time-limited URL the client PUTs ``key`` to."""

    @abstractmethod
    def size(self, key: str) -> Optional[int]:
        """Stored byte length, or ``None`` when the object does not exist."""

    @abstractmethod
    def read(self, key: str) -> bytes:
        """Return the stored bytes."""


class GCSAcquisitionStore(AcquisitionObjectStore):
    """Google Cloud Storage implementation."""

    def __init__(self, bucket_name: str, project: Optional[str] = None) -> None:
        from google.cloud import storage  # lazy: optional dependency

        self._bucket: Any = storage.Client(project=project).bucket(bucket_name)

    def signed_upload_url(self, key: str, *, size_bytes: int, expires_in_s: int) -> str:
        blob = self._bucket.blob(key)
        return str(
            blob.generate_signed_url(
                version="v4",
                expiration=timedelta(seconds=expires_in_s),
                method="PUT",
                content_type="application/octet-stream",
            )
        )

    def size(self, key: str) -> Optional[int]:
        blob = self._bucket.get_blob(key)
        return None if blob is None else int(blob.size)

    def read(self, key: str) -> bytes:
        return bytes(self._bucket.blob(key).download_as_bytes())


def get_acquisition_store() -> Optional[AcquisitionObjectStore]:
    """FastAPI dependency; ``None`` when no bucket is configured."""
    if not settings.acquisition_bucket:
        return None
    return GCSAcquisitionStore(settings.acquisition_bucket, settings.gcp_project_id)
