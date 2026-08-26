from datetime import UTC, datetime
from pathlib import Path

from app.services.tempfile_publisher import TempfilePublisher


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"success": True, "files": [{"id": "abc123", "expiryTime": 1760000000000}]}

    @property
    def text(self):
        return "https://litter.catbox.moe/fallback.mp4"


class FakeHttp:
    def post(self, url, files, data, timeout):
        assert url == "https://tempfile.org/api/upload/local"
        assert files["files"][0] == "sample.mp4"
        assert data == {"expiryHours": "24"}
        assert timeout == (10, 300)
        return FakeResponse()


def test_publisher_returns_direct_download_url_and_expiry(tmp_path: Path) -> None:
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"video")

    published = TempfilePublisher(FakeHttp())._publish_tempfile(source, "video/mp4")

    assert published.url == "https://tempfile.org/abc123/download"
    assert published.expires_at == datetime.fromtimestamp(1760000000, UTC)


class LitterboxHttp:
    def __init__(self):
        self.calls = []

    def post(self, url, files, data, timeout):
        self.calls.append((url, files, data, timeout))
        return FakeResponse()


def test_reference_video_uses_tempfile_without_downscaling(tmp_path: Path) -> None:
    source = tmp_path / "original-1080p.mp4"
    source.write_bytes(b"full-quality-video")
    http = LitterboxHttp()
    publisher = TempfilePublisher(http)

    published = publisher.publish(source, "video/mp4")

    assert published.url == "https://tempfile.org/abc123/download"
    assert http.calls[0][0] == "https://tempfile.org/api/upload/local"
    assert http.calls[0][2] == {"expiryHours": "24"}
    assert http.calls[0][3] == (10, 300)


class FailedVideoHttp:
    def __init__(self):
        self.urls = []

    def post(self, url, **_kwargs):
        import requests
        self.urls.append(url)
        raise requests.ConnectionError("tempfile unavailable")


def test_reference_video_never_falls_back_to_catbox(tmp_path: Path) -> None:
    source = tmp_path / "original-1080p.mp4"
    source.write_bytes(b"full-quality-video")
    http = FailedVideoHttp()

    try:
        TempfilePublisher(http).publish(source, "video/mp4")
    except RuntimeError as exc:
        assert "tempfile unavailable" in str(exc)
    else:
        raise AssertionError("video publishing must fail instead of falling back to Catbox")
    assert http.urls == ["https://tempfile.org/api/upload/local"]


def test_default_publisher_bypasses_environment_proxy(monkeypatch) -> None:
    class DefaultSession:
        trust_env = True

    monkeypatch.setattr("app.services.tempfile_publisher.requests.Session", DefaultSession)

    publisher = TempfilePublisher()

    assert publisher._http.trust_env is False
