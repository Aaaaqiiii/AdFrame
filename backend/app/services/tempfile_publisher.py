from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests


@dataclass(frozen=True)
class PublishedAsset:
    url: str
    expires_at: datetime


class TempfilePublisher:
    endpoint = "https://tempfile.org/api/upload/local"
    fallback_endpoint = "https://litterbox.catbox.moe/resources/internals/api.php"

    def __init__(self, http: requests.Session | None = None):
        self._http = http or requests.Session()
        if http is None:
            # 大文件经本机 HTTP(S)_PROXY 上传会被中途断开；临时文件服务必须直连。
            self._http.trust_env = False

    def _publish_tempfile(self, source: Path, content_type: str) -> PublishedAsset:
        with source.open("rb") as stream:
            response = self._http.post(
                self.endpoint,
                files={"files": (source.name, stream, content_type)},
                # 保持 1080p 参考视频时，临时服务完成接收与落盘可能超过两分钟。
                # 连接仍需在 10 秒内建立；读取响应给足 300 秒，避免文件已上传却被客户端过早判失败。
                data={"expiryHours": "24"}, timeout=(10, 300),
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

    def _publish_litterbox(self, source: Path, content_type: str) -> PublishedAsset:
        with source.open("rb") as stream:
            response = self._http.post(
                self.fallback_endpoint,
                files={"fileToUpload": (source.name, stream, content_type)},
                data={"reqtype": "fileupload", "time": "24h"},
                timeout=(10, 300),
            )
        response.raise_for_status()
        url = response.text.strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "litter.catbox.moe":
            raise RuntimeError("Litterbox temporary publishing response is invalid")
        return PublishedAsset(url=url, expires_at=datetime.now(UTC) + timedelta(hours=24))

    def publish(self, source: Path, content_type: str) -> PublishedAsset:
        # Seedance 对部分 Litterbox 视频 URL 无法读取时长；视频固定走 tempfile.org，
        # 上传失败就停止，不能再把同一个生成任务静默回退到不可读通道。
        publishers = (self._publish_tempfile,) if content_type.startswith("video/") else (
            self._publish_litterbox, self._publish_tempfile,
        )
        errors: list[str] = []
        for publisher in publishers:
            try:
                return publisher(source, content_type)
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                errors.append(str(exc))
        raise RuntimeError("Temporary publishing failed: " + " | ".join(errors))
