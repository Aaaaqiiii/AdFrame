import os
from pathlib import Path
import shutil


# 测试必须在导入应用前切换到独立 SQLite，绝不读写用户的正式 PostgreSQL。
test_database = Path(__file__).resolve().parent / ".adflow-test.sqlite3"
test_database.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{test_database.as_posix()}"
test_media = Path(__file__).resolve().parent / ".test-media"
os.environ["MEDIA_ROOT"] = str(test_media)


def pytest_sessionfinish() -> None:
    # Windows 必须先释放 SQLite 连接，才能可靠删除测试库；正式库从不在此路径中。
    from app.db.session import engine

    engine.dispose()
    test_database.unlink(missing_ok=True)
    shutil.rmtree(test_media, ignore_errors=True)
