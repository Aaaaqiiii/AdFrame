from collections.abc import Generator
from pathlib import Path
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.db import models  # noqa: F401


database_url = Settings().database_url
# 无论从 IDE、命令行还是单个测试启动，pytest 都不得连接正式 PostgreSQL。
if any("pytest" in Path(argument).name.lower() for argument in sys.argv) and not database_url.startswith("sqlite"):
    raise RuntimeError("Tests must use the isolated SQLite database, never the production PostgreSQL database.")

engine = create_engine(database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_schema() -> None:
    Base.metadata.create_all(engine)
    # SQLite 只用于自动化测试；以下兼容升级语句属于本地 PostgreSQL 安装。
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS created_at TIMESTAMP"))
        connection.execute(text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS mode VARCHAR(30) DEFAULT 'preserve_product'"))
        connection.execute(text("UPDATE projects SET mode = 'preserve_product' WHERE mode IS NULL"))
        connection.execute(text("UPDATE projects SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
        connection.execute(text("ALTER TABLE assets ADD COLUMN IF NOT EXISTS original_filename VARCHAR(255)"))
        for column, definition in (
            ("content_type", "VARCHAR(100)"), ("size_bytes", "INTEGER"), ("duration_sec", "DOUBLE PRECISION"),
            ("width", "INTEGER"), ("height", "INTEGER"), ("fps", "DOUBLE PRECISION"), ("public_url", "VARCHAR(1000)"), ("public_url_expires_at", "VARCHAR(40)"), ("profile_text", "TEXT"), ("profile_json", "TEXT"), ("profile_user_edited", "BOOLEAN DEFAULT FALSE"), ("analysis_status", "VARCHAR(20)"), ("analysis_error", "TEXT"),
        ):
            connection.execute(text(f"ALTER TABLE assets ADD COLUMN IF NOT EXISTS {column} {definition}"))
        for column in ("people", "action", "product", "product_interaction", "background", "camera", "lighting", "visual_style", "keep_unchanged", "on_screen_text", "observations", "inferences", "uncertainties"):
            connection.execute(text(f"ALTER TABLE shots ADD COLUMN IF NOT EXISTS {column} TEXT"))
        connection.execute(text("ALTER TABLE shots ADD COLUMN IF NOT EXISTS analysis_status VARCHAR(20) DEFAULT 'pending'"))
        connection.execute(text("ALTER TABLE shots ADD COLUMN IF NOT EXISTS analysis_error TEXT"))
        connection.execute(text("ALTER TABLE video_analyses ADD COLUMN IF NOT EXISTS shot_id UUID"))
        for column in ("product", "product_interaction", "lighting", "visual_style", "visible_text", "uncertainties"):
            connection.execute(text(f"ALTER TABLE shot_edits ADD COLUMN IF NOT EXISTS {column} TEXT"))
        connection.execute(text("ALTER TABLE shot_edits ADD COLUMN IF NOT EXISTS confirmed BOOLEAN DEFAULT FALSE"))
        connection.execute(text("ALTER TABLE shot_edits ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1"))
        connection.execute(text("ALTER TABLE shot_edits ADD COLUMN IF NOT EXISTS ai_summary_version INTEGER DEFAULT 0"))
        for column, definition in (("visual_direction", "TEXT DEFAULT ''"), ("audio_mode", "VARCHAR(30) DEFAULT 'keep_original'"), ("audio_style", "VARCHAR(500) DEFAULT ''"), ("replace_product", "BOOLEAN DEFAULT FALSE"), ("replace_person", "BOOLEAN DEFAULT FALSE"), ("source_timeline_revision_id", "UUID"), ("status", "VARCHAR(20) DEFAULT 'completed'"), ("error_message", "TEXT"), ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")):
            connection.execute(text(f"ALTER TABLE prompt_revisions ADD COLUMN IF NOT EXISTS {column} {definition}"))
        for column, definition in (("shot_id", "UUID"), ("provider", "VARCHAR(50)"), ("external_task_id", "VARCHAR(255)"), ("provider_input_id", "VARCHAR(255)"), ("error_message", "TEXT"), ("attempts", "INTEGER DEFAULT 0"), ("next_attempt_at", "TIMESTAMP"), ("leased_at", "TIMESTAMP"), ("leased_by", "VARCHAR(100)"), ("created_at", "TIMESTAMP")):
            connection.execute(text(f"ALTER TABLE jobs ADD COLUMN IF NOT EXISTS {column} {definition}"))
        for column, definition in (("provider", "VARCHAR(50)"), ("ratio", "VARCHAR(10) DEFAULT '9:16'"), ("duration", "INTEGER DEFAULT 5"), ("generate_audio", "BOOLEAN DEFAULT FALSE"), ("reference_image_urls", "TEXT"), ("external_task_id", "VARCHAR(255)"), ("status", "VARCHAR(30)"), ("result_url", "VARCHAR(1000)"), ("error_message", "TEXT"), ("attempts", "INTEGER DEFAULT 0"), ("next_attempt_at", "TIMESTAMP"), ("result_path", "VARCHAR(500)"), ("leased_at", "TIMESTAMP"), ("leased_by", "VARCHAR(100)"), ("created_at", "TIMESTAMP")):
            connection.execute(text(f"ALTER TABLE generations ADD COLUMN IF NOT EXISTS {column} {definition}"))


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
