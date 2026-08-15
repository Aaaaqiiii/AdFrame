from app.services.volcengine_vision import build_submit_vision_payload, parse_vision_result
from app.services.volcengine_vision import VisionConfigurationError, validate_vision_configuration
from app.core.config import Settings


def test_submit_payload_uses_vod_vid_and_vision_skill() -> None:
    payload = build_submit_vision_payload("adflow-space", "v0123")

    assert payload["SpaceName"] == "adflow-space"
    assert payload["MultiInputs"] == [{"Type": "Vid", "Vid": "v0123"}]
    assert payload["SkillType"] == "Vision"
    assert "只输出 JSON" in payload["Prompt"]


def test_completed_vision_result_returns_only_vision_content() -> None:
    result = parse_vision_result({
        "Result": {"TaskId": "task-1", "Status": "Completed", "ApiResponses": [
            {"VodTaskType": "Vision", "Vision": {"Content": "A person holds the product."}},
        ]},
    })

    assert result.task_id == "task-1"
    assert result.content == "A person holds the product."


def test_vision_requires_vod_credentials() -> None:
    settings = Settings(volcengine_access_key="", volcengine_secret_key="", volcengine_vod_space="")

    try:
        validate_vision_configuration(settings)
    except VisionConfigurationError as exc:
        assert "VOLCENGINE_ACCESS_KEY" in str(exc)
    else:
        raise AssertionError("configuration should be rejected")
