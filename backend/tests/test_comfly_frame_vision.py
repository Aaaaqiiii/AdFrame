import json
from pathlib import Path

from app.core.config import Settings
from app.services.comfly_frame_vision import ComflyFrameVisionGateway, validate_frame_vision_configuration
from app.services.media import EvidenceFrame


def test_frame_vision_requires_comfly_key() -> None:
    try:
        validate_frame_vision_configuration(Settings(_env_file=None, comfly_api_key=""))
    except RuntimeError as exc:
        assert "Comfly" in str(exc)
    else:
        raise AssertionError("configuration should be rejected")


def test_shot_analysis_sends_ordered_image_frames(monkeypatch, tmp_path: Path) -> None:
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    captured = {}

    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"action": "拿起产品"}, ensure_ascii=False)}}]}

    def post(*_args, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("app.services.comfly_frame_vision.requests.post", post)
    gateway = ComflyFrameVisionGateway(Settings(_env_file=None, comfly_api_key="key"))
    result = gateway._analyze_shot([EvidenceFrame(1.25, image)], 1, 2)

    assert result["action"] == "拿起产品"
    content = captured["messages"][1]["content"]
    assert content[1]["text"].endswith("1.250 秒")
    assert content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")
