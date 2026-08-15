from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests


@dataclass(frozen=True)
class PublishedAsset:
    url: str
    expires_at: datetime


class TempfilePublisher:
    endpoint = "https://tempfile.org/api/upload/local"

    def __init__(self, http: requests.Session | None = None):
        self._http = http or requests.Session()

    def publish(self, source: Path, content_type: str) -> PublishedAsset:
        with source.open("rb") as stream:
            response = self._http.post(
                self.endpoint,
                files={"files": (source.name, stream, content_type)},
                data={"expiryHours": "24"}, timeout=(10, 120),
            )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success") or not payload.get("files"):
            raise RuntimeError(payload.get("error", "Temporary publishing failed"))
        file = payload["files"][0]
        if not file.get("id") or not file.get("expiryTime"):
            raise RuntimeError("Temporary publishing response is incomplete")
        return PublishedAsset(
            url=f"https://tempfile.org/{file['id']}/download",
            expires_at=datetime.fromtimestamp(int(file["expiryTime"]) / 1000, UTC),
        )
