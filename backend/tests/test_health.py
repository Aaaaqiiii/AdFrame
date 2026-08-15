from fastapi.testclient import TestClient

from app.main import create_app
from app.services.connectivity import ConnectionCheck


def successful_check(*_args, **_kwargs) -> ConnectionCheck:
    return ConnectionCheck(True, "连接成功")


def test_health_endpoint_returns_service_status() -> None:
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_preflight_reports_config_without_returning_secrets() -> None:
    client = TestClient(create_app())

    response = client.get("/api/preflight")

    assert response.status_code == 200
    assert "volcengine_generation" in response.json()
    assert "api_key" not in str(response.json()).lower()


def test_local_settings_accepts_and_never_returns_comfly_key(monkeypatch) -> None:
    class FakePath:
        content = "COMFLY_API_KEY=old\n"
        def __init__(self, _value): pass
        def exists(self): return True
        def read_text(self, **_kwargs): return self.content
        def write_text(self, value, **_kwargs): type(self).content = value

    monkeypatch.setattr("app.main.Path", FakePath)
    monkeypatch.setattr("app.main.test_connection", successful_check)
    client = TestClient(create_app())

    response = client.put("/api/local-settings", json={"comfly_api_key": "replacement-key"})

    assert response.status_code == 200
    assert response.json()["connection"]["connected"] is True
    assert "replacement-key" in FakePath.content


def test_local_settings_accepts_official_ark_key_without_returning_it(monkeypatch) -> None:
    class FakePath:
        content = ""
        def __init__(self, _value): pass
        def exists(self): return False
        def write_text(self, value, **_kwargs): type(self).content = value

    monkeypatch.setattr("app.main.Path", FakePath)
    monkeypatch.setattr("app.main.test_connection", successful_check)
    client = TestClient(create_app())

    response = client.put("/api/local-settings", json={"volcengine_api_key": "ark-key"})

    assert response.status_code == 200
    assert response.json()["service"] == "volcengine_generation"
    assert FakePath.content == "VOLCENGINE_API_KEY=ark-key\n"
    assert "ark-key" not in response.text


def test_local_settings_accepts_vision_credentials_without_returning_them(monkeypatch) -> None:
    class FakePath:
        content = "COMFLY_API_KEY=old\n"
        def __init__(self, _value): pass
        def exists(self): return True
        def read_text(self, **_kwargs): return self.content
        def write_text(self, value, **_kwargs): type(self).content = value

    monkeypatch.setattr("app.main.Path", FakePath)
    monkeypatch.setattr("app.main.test_connection", successful_check)
    client = TestClient(create_app())

    response = client.put("/api/local-settings", json={
        "volcengine_access_key": "ak-value",
        "volcengine_secret_key": "sk-value",
        "volcengine_vod_space": "ad-videos",
    })

    assert response.status_code == 200
    assert response.json()["service"] == "volcengine_vision"
    assert "VOLCENGINE_ACCESS_KEY=ak-value" in FakePath.content
    assert "VOLCENGINE_SECRET_KEY=sk-value" in FakePath.content
    assert "VOLCENGINE_VOD_SPACE=ad-videos" in FakePath.content
    assert "ak-value" not in response.text
