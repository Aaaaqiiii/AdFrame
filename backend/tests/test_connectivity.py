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
