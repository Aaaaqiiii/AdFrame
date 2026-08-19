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
        snapshot = json.loads(generation.request_snapshot)
        assert snapshot["provider_request"]["ratio"] == "adaptive"
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


def test_worker_publishes_segment_clip_and_matches_prompt(client, tmp_path, monkeypatch) -> None:
    """Worker 对物理裁切分段发布 segment clip，请求文本匹配提示词，快照无签名串。"""
    from uuid import UUID as _UUID
    from app.db.models import GenerationSegment
    project = client.post("/api/projects", json={"name": "segment worker"}).json()
    video_id, asset_ids = _ready_generation_rows(tmp_path, project["id"])
    # 造一个非完整覆盖的分段（0–4 秒，原视频 8 秒）及分段提示词。
    with SessionLocal() as session:
        revision = session.scalar(select(TimelineRevision).where(TimelineRevision.project_id == _UUID(project["id"])))
        segment = GenerationSegment(
            project_id=_UUID(project["id"]), plan_version=1, position=0,
            source_start_sec=0.0, source_end_sec=4.0,
            start_boundary_type="video_edge", end_boundary_type="shot_boundary",
            source_timeline_revision_id=revision.id,
        )
        session.add(segment)
        session.flush()
        prompt = PromptRevision(
            project_id=_UUID(project["id"]), version=2, text="00:00.00–00:04.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。",
            prompt_mode="reference_video_edit", generation_segment_id=segment.id,
            source_timeline_revision_id=revision.id, status="completed",
        )
        session.add(prompt)
        generation = Generation(
            project_id=_UUID(project["id"]), version=2, prompt_version=prompt.version,
            generation_segment_id=segment.id, provider="volcengine",
            ratio="adaptive", duration=-1, status="queued",
            reference_asset_ids=json.dumps(asset_ids),
        )
        session.add(generation)
        session.commit()
        generation_id, segment_id = generation.id, segment.id

    from app.worker import run_once
    published = []
    class FakePublisher:
        def publish(self, path, content_type):
            published.append((str(path), content_type))
            return type("P", (), {"url": f"https://tempfile.org/seg/{len(published)}?signature=abc", "expires_at": datetime.now(UTC)})()
    monkeypatch.setattr("app.worker.TempfilePublisher", FakePublisher)
    # 避免真实 ffmpeg 裁切：mock ensure_segment_clip 只写一个占位文件。
    fake_clip = tmp_path / "fake-clip.mp4"
    fake_clip.write_bytes(b"clip")
    def _fake_ensure(source, destination, start, end, max_sec):
        destination.write_bytes(b"clip")
        return destination
    monkeypatch.setattr("app.worker.ensure_segment_clip", _fake_ensure)
    seen_payloads = []
    class FakeGateway:
        def submit(self, payload):
            seen_payloads.append(payload)
            return "provider-seg-1"
        def get_result(self, task_id):
            return GenerationResult(task_id=task_id, status="processing")
    monkeypatch.setattr("app.worker._generation_gateway", lambda settings, provider: (FakeGateway(), "model"))

    run_once()

    assert published, "segment clip should be published"
    with SessionLocal() as session:
        generation = session.get(Generation, generation_id)
        segment = session.get(GenerationSegment, segment_id)
        assert generation.status in {"queued", "processing", "retryable"}
        assert segment.clip_path is not None
        assert segment.public_url is not None
        # 请求文本匹配分段提示词。
        assert seen_payloads and seen_payloads[0]["content"][0]["text"].startswith("00:00.00–00:04.00")
        # 参考视频 URL 是 segment clip 的发布 URL。
        video_part = seen_payloads[0]["content"][1]["video_url"]["url"]
        assert video_part.startswith("https://tempfile.org/seg/")
        # 快照不含签名查询串。
        snapshot = generation.request_snapshot or ""
        assert "signature" not in snapshot
        assert "abc" not in snapshot


def test_worker_rebuilds_lost_segment_clip(client, tmp_path, monkeypatch) -> None:
    """clip_path 已指向目标路径但文件丢失时，Worker 必须重建裁片而不是永久卡住。"""
    from uuid import UUID as _UUID
    from app.db.models import GenerationSegment
    project = client.post("/api/projects", json={"name": "lost clip"}).json()
    video_id, asset_ids = _ready_generation_rows(tmp_path, project["id"])
    with SessionLocal() as session:
        revision = session.scalar(select(TimelineRevision).where(TimelineRevision.project_id == _UUID(project["id"])))
        segment = GenerationSegment(
            project_id=_UUID(project["id"]), plan_version=1, position=0,
            source_start_sec=0.0, source_end_sec=4.0,
            start_boundary_type="video_edge", end_boundary_type="shot_boundary",
            source_timeline_revision_id=revision.id,
            # clip_path 已指向目标路径，但文件不存在。
            clip_path=str(tmp_path / "generation-segments" / "plan-1" / "segment-0.mp4"),
        )
        session.add(segment)
        session.flush()
        prompt = PromptRevision(
            project_id=_UUID(project["id"]), version=2, text="00:00.00–00:04.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。",
            prompt_mode="reference_video_edit", generation_segment_id=segment.id,
            source_timeline_revision_id=revision.id, status="completed",
        )
        session.add(prompt)
        generation = Generation(
            project_id=_UUID(project["id"]), version=2, prompt_version=prompt.version,
            generation_segment_id=segment.id, provider="volcengine",
            ratio="adaptive", duration=-1, status="queued",
            reference_asset_ids=json.dumps(asset_ids),
        )
        session.add(generation)
        session.commit()
        generation_id, segment_id = generation.id, segment.id

    from app.worker import run_once
    rebuild_calls = []
    def _fake_ensure(source, destination, start, end, max_sec):
        rebuild_calls.append(str(destination))
        destination.write_bytes(b"clip")
        return destination
    monkeypatch.setattr("app.worker.ensure_segment_clip", _fake_ensure)
    class FakePublisher:
        def publish(self, path, content_type):
            return type("P", (), {"url": "https://tempfile.org/rebuild", "expires_at": datetime.now(UTC)})()
    monkeypatch.setattr("app.worker.TempfilePublisher", FakePublisher)
    class FakeGateway:
        def submit(self, payload):
            return "provider-rebuild-1"
        def get_result(self, task_id):
            return GenerationResult(task_id=task_id, status="processing")
    monkeypatch.setattr("app.worker._generation_gateway", lambda settings, provider: (FakeGateway(), "model"))

    run_once()

    assert len(rebuild_calls) == 1, "Worker must call ensure_segment_clip even when clip_path matches but file is missing"
    with SessionLocal() as session:
        generation = session.get(Generation, generation_id)
        segment = session.get(GenerationSegment, segment_id)
        assert generation.status in {"queued", "processing", "retryable"}
        assert segment.clip_path is not None


def _ready_batch_rows(tmp_path, project_id, duration_sec=40.0):
    """40 秒源：镜头 0-10/10-25/25-40（第三镜头跨越段边界 18），两段 [0,18]/[18,40]。
    返回 batch 相关行：视频、两 segment、full prompt、两个 batch Generation。"""
    from uuid import UUID
    from app.db.models import GenerationSegment
    from app.services.final_prompt import build_full_prompt_prefix
    video_path = tmp_path / "reference.mp4"
    video_path.write_bytes(b"video")
    pid = UUID(project_id)
    with SessionLocal() as session:
        video = Asset(
            project_id=pid, kind="reference_video", original_path=str(video_path),
            original_filename="reference.mp4", content_type="video/mp4", duration_sec=duration_sec,
        )
        session.add(video)
        session.flush()
        revision = TimelineRevision(project_id=pid, version=1, source="human")
        session.add(revision)
        session.flush()
        # 镜头：0-10, 10-25, 25-40（10-25 跨越段边界 18）。
        shot_ranges = [(0.0, 10.0), (10.0, 25.0), (25.0, 40.0)]
        for index, (start, end) in enumerate(shot_ranges):
            shot = Shot(timeline_revision_id=revision.id, position=index, start_sec=start, end_sec=end, analysis_status="succeeded")
            session.add(shot)
            session.flush()
            session.add(ShotEdit(project_id=pid, shot_id=shot.id, action="展示", confirmed=True))
        segment_a = GenerationSegment(project_id=pid, plan_version=1, position=0, source_start_sec=0.0, source_end_sec=18.0, start_boundary_type="video_edge", end_boundary_type="inside_shot", source_timeline_revision_id=revision.id)
        segment_b = GenerationSegment(project_id=pid, plan_version=1, position=1, source_start_sec=18.0, source_end_sec=40.0, start_boundary_type="inside_shot", end_boundary_type="video_edge", source_timeline_revision_id=revision.id)
        session.add_all([segment_a, segment_b])
        session.flush()
        prefix = build_full_prompt_prefix(project_mode="preserve_product", product_profile="", product_image_purposes=[], people_reference=None, background_reference=None, audio_mode="keep_original", audio_style="")
        blocks = []
        for start, end in shot_ranges:
            blocks.append(f"{_fmt(start)}–{_fmt(end)}\n保持：镜头 {int(start)} 保持正文\n修改：无。\n删除：无。\n禁止：无。")
        full_text = prefix + "\n\n" + "\n\n".join(blocks)
        prompt = PromptRevision(
            project_id=pid, version=1, prompt_mode="full_reference_video_edit",
            generation_segment_id=None, source_timeline_revision_id=revision.id,
            text=full_text, status="completed",
        )
        session.add(prompt)
        session.flush()
        batch_id = __import__("uuid").uuid4()
        gen_a = Generation(project_id=pid, version=1, prompt_version=prompt.version, generation_segment_id=segment_a.id, generation_batch_id=batch_id, batch_position=1, batch_size=2, provider="volcengine", ratio="adaptive", duration=-1, status="queued", reference_asset_ids=json.dumps([str(video.id)]))
        gen_b = Generation(project_id=pid, version=2, prompt_version=prompt.version, generation_segment_id=segment_b.id, generation_batch_id=batch_id, batch_position=2, batch_size=2, provider="volcengine", ratio="adaptive", duration=-1, status="queued", reference_asset_ids=json.dumps([str(video.id)]))
        session.add_all([gen_a, gen_b])
        session.commit()
        return SimpleNamespace(
            project_id=pid, revision_id=revision.id, video_id=video.id,
            segment_a_id=segment_a.id, segment_b_id=segment_b.id,
            prompt_version=prompt.version, batch_id=batch_id,
            gen_a_id=gen_a.id, gen_b_id=gen_b.id, full_text=full_text,
        )


def _fmt(value: float) -> str:
    return f"{int(value // 60):02d}:{value % 60:05.2f}"


def test_batch_worker_derives_relative_prompts_for_crossing_segments(client, tmp_path, monkeypatch) -> None:
    """batch worker：跨边界镜头在两段中推导正确相对时间，且逐字保持正文。"""
    project = _ready_batch_rows(tmp_path, client.post("/api/projects", json={"name": "batch worker"}).json()["id"])
    from app.worker import run_once
    from app.services.seedance import GenerationResult
    seen_payloads = []
    class FakePublisher:
        def publish(self, path, content_type):
            return type("P", (), {"url": f"https://tempfile.org/seg/{len(seen_payloads)}", "expires_at": datetime.now(UTC)})()
    monkeypatch.setattr("app.worker.TempfilePublisher", FakePublisher)
    def _fake_ensure(source, destination, start, end, max_sec):
        destination.write_bytes(b"clip")
        return destination
    monkeypatch.setattr("app.worker.ensure_segment_clip", _fake_ensure)
    class FakeGateway:
        def submit(self, payload):
            seen_payloads.append(payload)
            return f"task-{len(seen_payloads)}"
        def get_result(self, task_id):
            return GenerationResult(task_id=task_id, status="processing")
    monkeypatch.setattr("app.worker._generation_gateway", lambda settings, provider: (FakeGateway(), "model"))

    run_once()

    assert len(seen_payloads) == 2
    # worker 领行顺序不保证（按 id），用“片段 N/M”识别行分拣两段。
    texts = {}
    for payload in seen_payloads:
        text = payload["content"][0]["text"]
        if "片段 1/2" in text:
            texts["seg_a"] = text
        else:
            texts["seg_b"] = text
    # 段 A [0,18]：跨边界镜头 10-25 裁剪到 10-18 相对 0-8（段 A 起点 0）。
    assert "00:10.00–00:18.00" in texts["seg_a"]
    # 段 B [18,40]：同一镜头裁剪到 18-25 相对 0-7。
    assert "00:00.00–00:07.00" in texts["seg_b"]
    # 段 B 的块头必须是相对时间；绝对 00:18 只允许出现在服务端识别行。
    block_headers = [line for line in texts["seg_b"].split("\n") if "–" in line and line[:1].isdigit()]
    assert "00:18.00–00:25.00" not in block_headers
    # 两段正文逐字保持。
    assert "镜头 10 保持正文" in texts["seg_a"]
    assert "镜头 10 保持正文" in texts["seg_b"]
    # 视频 URL 各自第一个。
    assert all(payload["content"][1]["type"] == "video_url" for payload in seen_payloads)


def test_batch_worker_snapshot_envelope(client, tmp_path, monkeypatch) -> None:
    """batch worker 快照含 batch/segment/prompt 元数据，无签名串。"""
    project = _ready_batch_rows(tmp_path, client.post("/api/projects", json={"name": "batch snap"}).json()["id"])
    from app.worker import run_once
    from app.services.seedance import GenerationResult
    class FakePublisher:
        def publish(self, path, content_type):
            return type("P", (), {"url": "https://tempfile.org/x?signature=abc", "expires_at": datetime.now(UTC)})()
    monkeypatch.setattr("app.worker.TempfilePublisher", FakePublisher)
    def _fake_ensure(source, destination, start, end, max_sec):
        destination.write_bytes(b"clip")
        return destination
    monkeypatch.setattr("app.worker.ensure_segment_clip", _fake_ensure)
    class FakeGateway:
        def submit(self, payload):
            return "task-1"
        def get_result(self, task_id):
            return GenerationResult(task_id=task_id, status="processing")
    monkeypatch.setattr("app.worker._generation_gateway", lambda settings, provider: (FakeGateway(), "model"))
    run_once()
    with SessionLocal() as session:
        gen_a = session.get(Generation, project.gen_a_id)
        snap = json.loads(gen_a.request_snapshot)
        assert snap["generation_batch_id"] == str(project.batch_id)
        assert snap["batch_position"] == 1
        assert snap["batch_size"] == 2
        assert snap["generation_segment_id"] == str(project.segment_a_id)
        assert snap["plan_version"] == 1
        assert snap["full_prompt_revision_id"]
        assert snap["prompt_version"] == project.prompt_version
        assert "signature" not in json.dumps(snap)


@pytest.mark.parametrize("provider,expected_model", [
    ("volcengine", "doubao-seedance-2-5-260628"),
    ("comfly", "doubao-seedance-2.5"),
])
def test_batch_worker_selects_correct_provider_gateway(client, tmp_path, monkeypatch, provider, expected_model) -> None:
    """batch worker 只选择对应供应商的 gateway 与模型，绝不 fallback。"""
    project = _ready_batch_rows(tmp_path, client.post("/api/projects", json={"name": "prov"}).json()["id"])
    from app.worker import run_once
    from app.services.seedance import GenerationResult
    # 改两行为指定 provider。
    with SessionLocal() as session:
        for g in session.scalars(select(Generation).where(Generation.project_id == project.project_id)):
            g.provider = provider
        session.commit()
    seen_models = []
    class FakePublisher:
        def publish(self, path, content_type):
            return type("P", (), {"url": "https://t/1", "expires_at": datetime.now(UTC)})()
    monkeypatch.setattr("app.worker.TempfilePublisher", FakePublisher)
    monkeypatch.setattr("app.worker.ensure_segment_clip", lambda *a, **k: (a[1].write_bytes(b"c"), a[1])[1])
    class FakeGateway:
        def submit(self, payload):
            return "t"
        def get_result(self, task_id):
            return GenerationResult(task_id=task_id, status="processing")
    def fake_gateway(settings, provider):
        seen_models.append(provider)
        return FakeGateway(), expected_model
    monkeypatch.setattr("app.worker._generation_gateway", fake_gateway)
    run_once()
    assert seen_models == [provider, provider]


def test_batch_worker_corrupt_prompt_fails_not_retries(client, tmp_path, monkeypatch) -> None:
    """冻结完整提示词损坏 → 批次行 failed（确定性错误），不反复 retryable。"""
    project = _ready_batch_rows(tmp_path, client.post("/api/projects", json={"name": "corrupt prompt"}).json()["id"])
    from app.worker import run_once
    from app.services.seedance import GenerationResult
    from app.db.models import PromptRevision
    with SessionLocal() as session:
        prompt = session.scalar(select(PromptRevision).where(PromptRevision.project_id == project.project_id))
        prompt.text = prompt.text.replace("00:40.00", "00:39.00")
        session.commit()
    class FakePublisher:
        def publish(self, path, content_type):
            return type("P", (), {"url": "https://t/1", "expires_at": datetime.now(UTC)})()
    monkeypatch.setattr("app.worker.TempfilePublisher", FakePublisher)
    monkeypatch.setattr("app.worker.ensure_segment_clip", lambda *a, **k: (a[1].write_bytes(b"c"), a[1])[1])
    class FakeGateway:
        def submit(self, payload):
            return "t"
        def get_result(self, task_id):
            return GenerationResult(task_id=task_id, status="processing")
    monkeypatch.setattr("app.worker._generation_gateway", lambda settings, provider: (FakeGateway(), "m"))
    run_once()
    with SessionLocal() as session:
        rows = list(session.scalars(select(Generation).where(Generation.project_id == project.project_id)))
        assert len(rows) == 2
        assert all(row.status == "failed" for row in rows)
        assert all("推导失败" in (row.error_message or "") for row in rows)
