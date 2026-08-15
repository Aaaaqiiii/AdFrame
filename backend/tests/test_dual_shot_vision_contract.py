from unittest.mock import Mock

from app.services.dual_shot_vision import _json_object, _raise_provider_error, _responses_text


def test_json_parser_accepts_fenced_or_prefixed_json() -> None:
    assert _json_object("说明：```json\n{\"action\":\"拿起产品\"}\n```")["action"] == "拿起产品"


def test_volcengine_responses_text_contract() -> None:
    response = Mock()
    response.json.return_value = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"people\":\"一人\"}"}]}]}
    assert _responses_text(response) == '{"people":"一人"}'


def test_provider_error_keeps_api_message() -> None:
    response = Mock(ok=False, status_code=400, text="")
    response.json.return_value = {"error": {"message": "unsupported fps"}}
    try:
        _raise_provider_error(response, "火山方舟")
    except RuntimeError as error:
        assert str(error) == "火山方舟 HTTP 400: unsupported fps"
    else:
        raise AssertionError("expected provider error")
