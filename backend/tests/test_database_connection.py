from sqlalchemy import text

from app.db.session import engine


def test_tests_use_isolated_database() -> None:
    assert engine.dialect.name == "sqlite"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
