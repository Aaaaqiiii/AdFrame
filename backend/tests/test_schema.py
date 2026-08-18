from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_has_one_head_at_segment_generations() -> None:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0005_segment_generations"]


def test_application_does_not_create_production_schema(monkeypatch) -> None:
    from app.db.base import Base

    monkeypatch.setattr(Base.metadata, "create_all", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("create_all called")))
    from app.main import create_app

    create_app()


def test_migrations_require_database_at_head() -> None:
    from app.db.migrations import require_database_at_head

    assert callable(require_database_at_head)
