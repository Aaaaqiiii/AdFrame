from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.services.dual_shot_vision import _json_object, _raise_provider_error, validate_dual_vision_configuration
from app.services.volcengine_vision import VisionConfigurationError


def test_json_parser_accepts_fenced_or_prefixed_json() -> None:
    assert _json_object("说明：```json\n{\"action\":\"拿起产品\"}\n```")["action"] == "拿起产品"


def test_qwen_shot_vision_only_requires_comfly_key() -> None:
    validate_dual_vision_configuration(Settings(_env_file=None, comfly_api_key="configured"))
    with pytest.raises(VisionConfigurationError, match="Comfly API Key"):
        validate_dual_vision_configuration(Settings(_env_file=None, comfly_api_key=""))


def test_provider_error_keeps_api_message() -> None:
    response = Mock(ok=False, status_code=400, text="")
    response.json.return_value = {"error": {"message": "unsupported fps"}}
    try:
        _raise_provider_error(response, "火山方舟")
    except RuntimeError as error:
        assert str(error) == "火山方舟 HTTP 400: unsupported fps"
    else:
        raise AssertionError("expected provider error")
