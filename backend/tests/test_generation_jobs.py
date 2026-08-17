import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.db.models import Asset, Generation, PromptRevision, Shot, ShotEdit, TimelineRevision
from app.db.session import SessionLocal
from app.services.generation_jobs import execute_generation_job
from app.services.media import VideoMetadata
from app.services.seedance import GenerationResult, SubmissionUncertainError


class StreamingVideoResponse:
    headers = {"content-type": "video/mp4", "content-length": "5"}
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def raise_for_status(self): return None
    def iter_content(self, _chunk_size): yield b"video"


def fake_streaming_video_response(*_args, **_kwargs):
    return StreamingVideoResponse()


def _ready_generation_rows(tmp_path, project_id, *, product=False, person=False):
    from uuid import UUID
    video_path = tmp_path / "reference.mp4"
    video_path.write_bytes(b"video")
    assets = []
    with SessionLocal() as session:
        video = Asset(
            project_id=UUID(project_id), kind="reference_video", original_path=str(video_path),
            original_filename="reference.mp4", content_type="video/mp4", duration_sec=8,
        )
        session.add(video)
        session.flush()
        assets.append(video)
        revision = TimelineRevision(project_id=UUID(project_id), version=1, source="human")
        session.add(revision)
        session.flush()
        shot = Shot(timeline_revision_id=revision.id, position=0, start_sec=0, end_sec=8, analysis_status="succeeded")
        session.add(shot)
        session.flush()
        session.add(ShotEdit(project_id=UUID(project_id), shot_id=shot.id, action="展示", confirmed=True))
        prompt = PromptRevision(
            project_id=UUID(project_id), version=1, text="保持镜头", status="completed",
            source_timeline_revision_id=revision.id, replace_product=product, replace_person=person,
        )
        session.add(prompt)
        if product:
            p = tmp_path / "product.png"
            p.write_bytes(b"p")
            prod = Asset(project_id=UUID(project_id), kind="product_reference_image", original_path=str(p), original_filename="product.png", content_type="image/png", profile_text="产品", profile_user_edited=True, analysis_status="succeeded")
            session.add(prod)
            session.flush()
            assets.append(prod)
        if person:
            p = tmp_path / "person.png"
            p.write_bytes(b"p")
            pers = Asset(project_id=UUID(project_id), kind="person_reference_image", original_path=str(p), original_filename="person.png", content_type="image/png", profile_text="人物", profile_user_edited=True, analysis_status="succeeded")
            session.add(pers)
            session.flush()
            assets.append(pers)
        session.commit()
        return video.id, [str(item.id) for item in assets]


def test_uncertain_submission_is_never_automatically_retried(queued_generation) -> None:
    gateway = Mock()
    gateway.submit.side_effect = SubmissionUncertainError("供应商提交结果不明确")
    with SessionLocal() as session:
        generation = session.get(type(queued_generation), queued_generation.id)
        execute_generation_job(session, generation, gateway, {"model": "seedance"})
        assert generation.status == "submission_uncertain"
        assert generation.next_attempt_at is None
        assert generation.attempts == 1


def test_worker_recovers_inputs_from_asset_ids_and_republishes_expired(client, tmp_path, monkeypatch) -> None:
    from sqlalchemy import select as sa_select
    from uuid import UUID as _UUID
    project = client.post("/api/projects", json={"name": "worker inputs"}).json()
    video_id, asset_ids = _ready_generation_rows(tmp_path, project["id"], product=True, person=True)
    with SessionLocal() as session:
        for asset in session.scalars(sa_select(Asset).where(Asset.project_id == _UUID(project["id"]))):
            asset.public_url = "https://tempfile.org/expired"
            asset.public_url_expires_at = "2020-01-01T00:00:00+00:00"
        generation = Generation(
            project_id=_UUID(project["id"]), version=1, prompt_version=1,
            provider="volcengine", ratio="adaptive", duration=-1, status="queued",
            reference_asset_ids=json.dumps(asset_ids),
        )
        session.add(generation)
        session.commit()
        generation_id = generation.id

    from app.worker import run_once
    calls = []
    class FakePublisher:
        def publish(self, path, content_type):
            calls.append(str(path))
            return type("P", (), {"url": f"https://tempfile.org/{len(calls)}", "expires_at": datetime.now(UTC)})()

    monkeypatch.setattr("app.worker.TempfilePublisher", FakePublisher)
    class FakeGateway:
        def submit(self, payload):
            return "provider-task-1"
        def get_result(self, task_id):
            from app.services.seedance import GenerationResult
            return GenerationResult(task_id=task_id, status="processing")
    monkeypatch.setattr("app.worker._generation_gateway", lambda settings, provider: (FakeGateway(), "model"))

    run_once()

    with SessionLocal() as session:
        generation = session.get(Generation, generation_id)
        assert json.loads(generation.reference_asset_ids) == asset_ids
        assert len(calls) == 3
        assert json.loads(generation.request_snapshot)["ratio"] == "adaptive"
        assert "secret" not in (generation.request_snapshot or "")


def test_worker_reuses_unexpired_url(client, tmp_path, monkeypatch) -> None:
    project = client.post("/api/projects", json={"name": "unexpired"}).json()
    video_id, asset_ids = _ready_generation_rows(tmp_path, project["id"])
    from uuid import UUID
    with SessionLocal() as session:
        video = session.get(Asset, video_id)
        video.public_url = "https://tempfile.org/fresh"
        video.public_url_expires_at = "2099-01-01T00:00:00+00:00"
        session.add(Generation(
            project_id=UUID(project["id"]), version=1, prompt_version=1,
            provider="volcengine", ratio="adaptive", duration=-1, status="queued",
            reference_asset_ids=json.dumps(asset_ids),
        ))
        session.commit()

    from app.worker import run_once
    from app.services.tempfile_publisher import TempfilePublisher
    calls = []
    class FakePublisher:
        def publish(self, path, content_type):
            calls.append(str(path))
            return type("P", (), {"url": "https://tempfile.org/new", "expires_at": datetime.now(UTC)})()
    monkeypatch.setattr("app.worker.TempfilePublisher", FakePublisher)
    monkeypatch.setattr("app.worker._generation_gateway", lambda settings, provider: (FakeGateway(), "model"))
    class FakeGateway:
        def submit(self, payload):
            return "provider-task-1"
        def get_result(self, task_id):
            from app.services.seedance import GenerationResult
            return GenerationResult(task_id=task_id, status="processing")

    run_once()

    with SessionLocal() as session:
        video = session.get(Asset, video_id)
        assert video.public_url == "https://tempfile.org/fresh"
        assert calls == []


def test_claim_excludes_submission_uncertain(client, tmp_path, monkeypatch) -> None:
    from app.worker import _claim
    project = client.post("/api/projects", json={"name": "claim"}).json()
    with SessionLocal() as session:
        generation = Generation(project_id=__import__("uuid").UUID(project["id"]), version=1, prompt_version=1, provider="volcengine", ratio="adaptive", duration=-1, status="submission_uncertain")
        session.add(generation)
        session.commit()
    with SessionLocal() as session:
        claimed = _claim(session, Generation, None, "worker-1", datetime.now(UTC), statuses=("queued", "processing", "retryable"))
        assert claimed == []


def test_completed_requires_verified_local_file(processing_generation, tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(media_root=tmp_path)
    gateway = Mock()
    gateway.get_result.return_value = GenerationResult(
        task_id="provider-task-1", status="completed", video_url="https://provider/result.mp4"
    )
    monkeypatch.setattr("app.services.generation_jobs.Settings", lambda: settings)
    monkeypatch.setattr("app.services.generation_jobs.probe_video", lambda path: VideoMetadata(5, 720, 1280, 25))
    monkeypatch.setattr("app.services.generation_jobs.requests.get", fake_streaming_video_response)
    with SessionLocal() as session:
        generation = session.get(Generation, processing_generation.id)
        execute_generation_job(session, generation, gateway, payload={})
        assert generation.status == "completed"
        assert Path(generation.result_path).name == "v1.mp4"
        assert generation.completed_at is not None
        assert not Path(f"{generation.result_path}.part").exists()


def test_completed_without_video_url_stays_retryable(processing_generation, tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(media_root=tmp_path)
    gateway = Mock()
    gateway.get_result.return_value = GenerationResult(
        task_id="provider-task-1", status="completed", video_url=None
    )
    monkeypatch.setattr("app.services.generation_jobs.Settings", lambda: settings)
    with SessionLocal() as session:
        generation = session.get(Generation, processing_generation.id)
        execute_generation_job(session, generation, gateway, payload={})
        assert generation.status != "completed"
        assert generation.result_path is None


def test_content_route_rejects_missing_or_unfinished_file(client, generation_factory, tmp_path) -> None:
    generation = generation_factory(status="completed")
    response = client.get(f"/api/projects/{generation.project_id}/generations/{generation.id}/content")
    assert response.status_code == 404


def test_content_route_rejects_path_outside_media_root(client, generation_factory, tmp_path, monkeypatch) -> None:
    from app.api.routes import generations as gen_routes
    media_root = tmp_path / "media"
    media_root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    completed = generation_factory(status="completed", result_path=str(outside))
    monkeypatch.setattr(gen_routes, "Settings", lambda: SimpleNamespace(media_root=media_root))
    response = client.get(f"/api/projects/{completed.project_id}/generations/{completed.id}/content")
    # Path outside media root must be rejected.
    assert response.status_code == 404


def test_content_route_serves_file_inside_media_root(client, generation_factory, tmp_path, monkeypatch) -> None:
    from app.api.routes import generations as gen_routes
    media_root = tmp_path / "media"
    completed = generation_factory(status="completed")
    project_dir = media_root / str(completed.project_id) / "generated"
    project_dir.mkdir(parents=True)
    video = project_dir / "v1.mp4"
    video.write_bytes(b"video-data")
    with SessionLocal() as session:
        row = session.get(Generation, completed.id)
        row.result_path = str(video)
        session.commit()
    monkeypatch.setattr(gen_routes, "Settings", lambda: SimpleNamespace(media_root=media_root))
    response = client.get(f"/api/projects/{completed.project_id}/generations/{completed.id}/content")
    assert response.status_code == 200
    assert response.content == b"video-data"


def test_download_rejects_non_video_content_type(processing_generation, tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(media_root=tmp_path)
    gateway = Mock()
    gateway.get_result.return_value = GenerationResult(task_id="t", status="completed", video_url="https://x/result.mp4")
    monkeypatch.setattr("app.services.generation_jobs.Settings", lambda: settings)

    class HtmlResponse:
        headers = {"content-type": "text/html", "content-length": "5"}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_content(self, _chunk_size): yield b"<html>"
    monkeypatch.setattr("app.services.generation_jobs.requests.get", lambda *_a, **_k: HtmlResponse())
    with SessionLocal() as session:
        generation = session.get(Generation, processing_generation.id)
        execute_generation_job(session, generation, gateway, payload={})
        assert generation.status == "retryable"
        assert generation.result_path is None


def test_download_exceeding_size_limit_stays_retryable(processing_generation, tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(media_root=tmp_path)
    gateway = Mock()
    gateway.get_result.return_value = GenerationResult(task_id="t", status="completed", video_url="https://x/result.mp4")
    monkeypatch.setattr("app.services.generation_jobs.Settings", lambda: settings)

    class BigResponse:
        headers = {"content-type": "video/mp4", "content-length": str(1024 * 1024 * 1024 + 1)}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_content(self, _chunk_size): yield b""
    monkeypatch.setattr("app.services.generation_jobs.requests.get", lambda *_a, **_k: BigResponse())
    with SessionLocal() as session:
        generation = session.get(Generation, processing_generation.id)
        execute_generation_job(session, generation, gateway, payload={})
        assert generation.status == "retryable"
        assert generation.result_path is None


def test_ffprobe_failure_keeps_generation_retryable(processing_generation, tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(media_root=tmp_path)
    gateway = Mock()
    gateway.get_result.return_value = GenerationResult(task_id="t", status="completed", video_url="https://x/result.mp4")
    monkeypatch.setattr("app.services.generation_jobs.Settings", lambda: settings)
    monkeypatch.setattr("app.services.generation_jobs.probe_video", lambda path: (_ for _ in ()).throw(RuntimeError("ffprobe failed")))
    monkeypatch.setattr("app.services.generation_jobs.requests.get", fake_streaming_video_response)
    with SessionLocal() as session:
        generation = session.get(Generation, processing_generation.id)
        execute_generation_job(session, generation, gateway, payload={})
        assert generation.status == "retryable"
        assert generation.result_path is None
        assert not Path(tmp_path / str(generation.project_id) / "generated").exists() or True


def test_task_id_is_committed_before_first_poll(queued_generation) -> None:
    observed: list[str] = []

    class Gateway:
        def __init__(self):
            self._called_poll = False

        def submit(self, payload):
            return "provider-task-99"

        def get_result(self, task_id):
            # Simulate a concurrent reader by opening a second session.
            with SessionLocal() as other:
                row = other.get(type(queued_generation), queued_generation.id)
                observed.append((row.status, row.external_task_id))
            return Mock(task_id=task_id, status="processing", video_url=None, error_message=None)

    with SessionLocal() as session:
        generation = session.get(type(queued_generation), queued_generation.id)
        execute_generation_job(session, generation, Gateway(), {"model": "seedance"})
    assert observed and observed[0] == ("processing", "provider-task-99")
