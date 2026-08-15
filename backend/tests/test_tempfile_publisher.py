from datetime import UTC, datetime
from pathlib import Path

from app.services.tempfile_publisher import TempfilePublisher


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"success": True, "files": [{"id": "abc123", "expiryTime": 1760000000000}]}


class FakeHttp:
    def post(self, url, files, data, timeout):
        assert url == "https://tempfile.org/api/upload/local"
        assert files["files"][0] == "sample.mp4"
        assert data == {"expiryHours": "24"}
        return FakeResponse()


def test_publisher_returns_direct_download_url_and_expiry(tmp_path: Path) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")

    published = TempfilePublisher(FakeHttp()).publish(source, "video/mp4")

    assert published.url == "https://tempfile.org/abc123/download"
    assert published.expires_at == datetime.fromtimestamp(1760000000, UTC)
