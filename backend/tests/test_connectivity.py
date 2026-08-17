from app.core.config import Settings
from app.services.connectivity import test_connection as check_connection


def test_generation_connectivity_treats_non_auth_client_error_as_reachable(monkeypatch) -> None:
    class Response:
        status_code = 404

    monkeypatch.setattr("app.services.connectivity.requests.get", lambda *args, **kwargs: Response())
    settings = Settings(_env_file=None, comfly_api_key="test-key")

    result = check_connection("comfly_generation", settings)

    assert result.connected is True
    assert "404" in result.message


def test_generation_connectivity_rejects_unauthorized_key(monkeypatch) -> None:
    class Response:
        status_code = 401

    monkeypatch.setattr("app.services.connectivity.requests.get", lambda *args, **kwargs: Response())
    settings = Settings(_env_file=None, volcengine_api_key="bad-key")

    result = check_connection("volcengine_generation", settings)

    assert result.connected is False


def test_comfly_prompt_connectivity_hits_chat_completions_endpoint(monkeypatch) -> None:
    captured = {}

    class Response:
        status_code = 200

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return Response()

    monkeypatch.setattr("app.services.connectivity.requests.post", fake_post)
    settings = Settings(_env_file=None, comfly_api_key="test-key", comfly_vision_base_url="https://ai.comfly.org")

    result = check_connection("comfly_prompt", settings)

    assert result.connected is True
    assert captured["url"] == "https://ai.comfly.org/v1/chat/completions"
    assert captured["headers"].get("Authorization") == "Bearer test-key"


def test_comfly_prompt_connectivity_rejects_unauthorized_key(monkeypatch) -> None:
    class Response:
        status_code = 401

    monkeypatch.setattr("app.services.connectivity.requests.post", lambda *args, **kwargs: Response())
    settings = Settings(_env_file=None, comfly_api_key="bad-key", comfly_vision_base_url="https://ai.comfly.org")

    result = check_connection("comfly_prompt", settings)

    assert result.connected is False


def test_comfly_prompt_connectivity_requires_key(monkeypatch) -> None:
    settings = Settings(_env_file=None, comfly_api_key="", comfly_vision_base_url="https://ai.comfly.org")

    result = check_connection("comfly_prompt", settings)

    assert result.connected is False
    assert "尚未填写" in result.message
