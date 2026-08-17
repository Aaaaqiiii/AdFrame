from collections.abc import Generator
from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


database_url = Settings().database_url
# 无论从 IDE、命令行还是单个测试启动，pytest 都不得连接正式 PostgreSQL。
if any("pytest" in Path(argument).name.lower() for argument in sys.argv) and not database_url.startswith("sqlite"):
    raise RuntimeError("Tests must use the isolated SQLite database, never the production PostgreSQL database.")

engine = create_engine(database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
