from pathlib import Path

from app.core.config import Settings


def test_settings_uses_local_defaults(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MEDIA_ROOT", raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://adflow:change-me@localhost:5432/adflow"
    assert settings.media_root == Path(r"E:\工具-商用\data\media")


def test_settings_reads_env_from_backend_directory_when_started_at_project_root() -> None:
    assert Settings().model_config["env_file"] == Path(__file__).resolve().parents[1] / ".env"
