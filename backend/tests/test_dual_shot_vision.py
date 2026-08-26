import base64
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from app.core.config import Settings
from app.services.dual_shot_vision import DualShotVisionGateway
from app.services.media import VideoMetadata


def test_qwen_result_is_cached_and_normalized_without_gpt_call(tmp_path, monkeypatch) -> None:
    source = tmp_path / "project" / "analysis-clips" / "shot.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    gateway = object.__new__(DualShotVisionGateway)
    gateway._job_id = uuid4()
    gateway._absolute_start_sec = 0.0
    monkeypatch.setattr(gateway, "_qwen_understand", lambda _source, _work_dir: {"action": "Qwen结果", "extra": "ignored"})
    result_path = gateway.submit_vision(str(source))

    work_dir = source.parent.parent / "dual-shot-vision" / str(gateway._job_id)
    assert json.loads((work_dir / "qwen.json").read_text(encoding="utf-8"))["action"] == "Qwen结果"
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    assert result["final"]["action"] == "Qwen结果"
    assert "extra" not in result["final"]
    assert "gpt" not in result

    monkeypatch.setattr(gateway, "_qwen_understand", lambda _source, _work_dir: (_ for _ in ()).throw(AssertionError("cached Qwen result should be reused")))
    gateway.submit_vision(str(source))


def test_short_video_is_repeated_by_stream_copy_without_changing_timeline(tmp_path, monkeypatch) -> None:
    source = tmp_path / "shot.mp4"
    source.write_bytes(b"video")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    gateway = object.__new__(DualShotVisionGateway)
    monkeypatch.setattr(
        "app.services.dual_shot_vision.probe_video",
        lambda _source: VideoMetadata(0.8, 1920, 1080, 30, video_codec="h264", pixel_format="yuv420p"),
    )
    repeated: list[Path] = []

    def copy_streams(sources: list[Path], destination: Path) -> Path:
        repeated.extend(sources)
        destination.write_bytes(b"same-streams")
        return destination

    monkeypatch.setattr("app.services.dual_shot_vision.concat_videos_lossless", copy_streams)
    transport, duration, repeat_count = gateway._prepare_qwen_video(source, work_dir)
    assert duration == 0.8
    assert repeat_count == 4
    assert repeated == [source] * 4
    assert transport.read_bytes() == b"same-streams"


def test_qwen_direct_video_payload_keeps_exact_clip_bytes(tmp_path, monkeypatch) -> None:
    source = tmp_path / "shot.mp4"
    source.write_bytes(b"exact-video-bytes")
    publisher = Mock()
    gateway = DualShotVisionGateway(Settings(_env_file=None, comfly_api_key="key"), publisher=publisher)
    gateway.set_job_context(uuid4(), 1.25)
    monkeypatch.setattr(gateway, "_prepare_qwen_video", lambda _source, _work_dir: (source, 4.0, 1))
    response = Mock(ok=True)
    response.json.return_value = {"choices": [{"message": {"content": '{"action":"拿起产品"}'}}]}
    posted: dict = {}

    def post(_url, **kwargs):
        posted.update(kwargs["json"])
        return response

    monkeypatch.setattr("app.services.dual_shot_vision.requests.post", post)
    assert gateway._qwen_understand(source, tmp_path)["action"] == "拿起产品"
    content = posted["messages"][0]["content"]
    encoded = content[0]["video_url"]["url"].split(",", 1)[1]
    assert posted["model"] == "qwen3.8-max"
    assert base64.b64decode(encoded) == source.read_bytes()
    assert "1.250秒" in content[1]["text"]
    publisher.publish.assert_not_called()
