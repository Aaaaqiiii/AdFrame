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


def pytest_sessionfinish() -> None:
    # Windows 必须先释放 SQLite 连接，才能可靠删除测试库；正式库从不在此路径中。
    from app.db.session import engine

    engine.dispose()
    test_database.unlink(missing_ok=True)
    shutil.rmtree(test_media, ignore_errors=True)
