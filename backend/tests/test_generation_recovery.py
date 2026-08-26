from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.db.models import Generation
from app.db.session import SessionLocal
from app.main import create_app
from app.worker import _claim, run_once


def test_processing_task_survives_new_api_session(processing_generation) -> None:
    generation_id = processing_generation.id
    project_id = processing_generation.project_id
    with SessionLocal() as restored:
        generation = restored.get(Generation, generation_id)
        assert generation.project_id == project_id
        assert generation.external_task_id == "provider-task-1"
        assert generation.status == "processing"


def test_expired_generation_lease_is_claimed_after_worker_restart(processing_generation) -> None:
    from sqlalchemy import select
    with SessionLocal() as session:
        # 隔离：只保留本测试的过期租约任务，其他可认领任务移出可认领状态。
        for other in session.scalars(select(Generation).where(Generation.id != processing_generation.id, Generation.status.in_(("queued", "processing", "retryable")))):
            other.status = "failed"
        session.commit()
        generation = session.get(Generation, processing_generation.id)
        generation.leased_by = "dead-worker"
        generation.leased_at = datetime.now(UTC) - timedelta(minutes=21)
        generation.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        claimed = _claim(
            session, Generation, None, "new-worker", datetime.now(UTC),
            statuses=("queued", "processing", "retryable"),
        )
        assert [item.id for item in claimed] == [processing_generation.id]


def test_submission_uncertain_is_never_reclaimed(uncertain_generation, monkeypatch) -> None:
    from sqlalchemy import select
    # 隔离：把同库中其他可被认领的生成任务移出可认领状态，只留本测试的不确定任务。
    with SessionLocal() as session:
        for other in session.scalars(select(Generation).where(Generation.id != uncertain_generation.id, Generation.status.in_(("queued", "processing", "retryable")))):
            other.status = "failed"
            other.leased_at = None
            other.next_attempt_at = None
        session.commit()
    # Stub the generation gateway so run_once would process anything it claims.
    class Gateway:
        def submit(self, payload):
            return "task-x"
        def get_result(self, task_id):
            from app.services.seedance import GenerationResult
            return GenerationResult(task_id=task_id, status="processing")
    monkeypatch.setattr("app.worker._generation_gateway", lambda settings, provider: (Gateway(), "model"))
    run_once()
    run_once()
    with SessionLocal() as session:
        generation = session.get(Generation, uncertain_generation.id)
        assert generation.status == "submission_uncertain"
        assert generation.leased_by is None


def test_completed_record_returns_same_local_url_from_new_client(client, generation_factory, tmp_path) -> None:
    local_file = tmp_path / "v1.mp4"
    local_file.write_bytes(b"video")
    generation = generation_factory(status="completed", result_path=str(local_file))
    fresh = TestClient(create_app())
    body = fresh.get(f"/api/projects/{generation.project_id}/generations/{generation.id}").json()
    assert body["local_video_url"].endswith(f"/{generation.id}/content")
