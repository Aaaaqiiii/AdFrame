import os
from pathlib import Path
import shutil
import subprocess

import pytest


# 测试必须在导入应用前切换到独立 SQLite，绝不读写用户的正式 PostgreSQL。
test_database = Path(__file__).resolve().parent / ".adflow-test.sqlite3"
test_database.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{test_database.as_posix()}"
test_media = Path(__file__).resolve().parent / ".test-media"
os.environ["MEDIA_ROOT"] = str(test_media)

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.db.base import Base
from app.db import models  # noqa: F401
from app.db.session import engine


def _tool_runs(name: str) -> bool:
    executable = shutil.which(name)
    if not executable:
        return False
    try:
        subprocess.run([executable, "-version"], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "media_tools: requires executable ffmpeg and ffprobe")


def pytest_collection_modifyitems(items) -> None:
    if _tool_runs("ffmpeg") and _tool_runs("ffprobe"):
        return
    marker = pytest.mark.skip(reason="FFmpeg/FFprobe is not executable in this environment")
    for item in items:
        if "media_tools" in item.keywords:
            item.add_marker(marker)


def pytest_sessionstart(session) -> None:
    Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def isolate_database() -> None:
    """每个测试前按外键逆序清空测试库，保证用例与文件执行顺序无关。"""
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


def pytest_sessionfinish() -> None:
    # Windows 必须先释放 SQLite 连接，才能可靠删除测试库；正式库从不在此路径中。
    engine.dispose()
    test_database.unlink(missing_ok=True)
    shutil.rmtree(test_media, ignore_errors=True)


@pytest.fixture()
def client():
    from app.main import create_app
    with TestClient(create_app()) as value:
        yield value


@pytest.fixture()
def generation_factory():
    from app.db.models import Generation, Project
    from app.db.session import SessionLocal

    def create(*, status="queued", version=1, result_path=None, external_task_id=None, project_id=None):
        with SessionLocal() as session:
            project = session.get(Project, project_id) if project_id else None
            if project is None:
                project = Project(name=f"generation-{version}", mode="preserve_product")
                session.add(project)
                session.flush()
            generation = Generation(
                project_id=project.id,
                version=version,
                prompt_version=1,
                provider="volcengine",
                ratio="adaptive",
                duration=-1,
                status=status,
                result_path=result_path,
                external_task_id=external_task_id,
                attempts=0,
                created_at=datetime.now(UTC),
            )
            session.add(generation)
            session.commit()
            return generation
    return create


@pytest.fixture()
def processing_generation(generation_factory):
    return generation_factory(status="processing", external_task_id="provider-task-1")


@pytest.fixture()
def queued_generation(generation_factory):
    return generation_factory(status="queued")


@pytest.fixture()
def failed_generation(generation_factory):
    return generation_factory(status="failed")


@pytest.fixture()
def uncertain_generation(generation_factory):
    return generation_factory(status="submission_uncertain")
