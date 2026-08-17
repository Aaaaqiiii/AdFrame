from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.core.config import BACKEND_ROOT
from app.db.session import engine


def require_database_at_head() -> None:
    if engine.dialect.name != "postgresql":
        return
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    expected = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        actual = MigrationContext.configure(connection).get_current_revision()
    if actual != expected:
        raise RuntimeError(f"Database revision {actual or 'none'} is behind required revision {expected}. Run Alembic upgrade first.")
