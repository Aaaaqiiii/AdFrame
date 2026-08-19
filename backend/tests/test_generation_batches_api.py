import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Asset, Generation, GenerationSegment, Project, PromptRevision, Shot, ShotEdit, TimelineRevision
from app.db.session import SessionLocal
from app.main import create_app


def _batch_project(client: TestClient, tmp_path, duration_sec=50.0, mode="preserve_product", confirm_shots=True):
    """duration_sec 秒视频 + 4 等分镜头 + 自动两段方案 + 完整提示词（full_reference_video_edit）。"""
    project = client.post("/api/projects", json={"name": "batch-project", "mode": mode}).json()
    project_id = UUID(project["id"])
    video_path = tmp_path / "reference.mp4"
    video_path.write_bytes(b"stored-video")
    with SessionLocal() as session:
        video = Asset(
            project_id=project_id, kind="reference_video", original_path=str(video_path),
            original_filename="reference.mp4", content_type="video/mp4", duration_sec=duration_sec,
        )
        revision = TimelineRevision(project_id=project_id, version=1, source="human")
        session.add_all([video, revision])
        session.flush()
        shot_ids = []
        shot_ranges = [(duration_sec * i / 4, duration_sec * (i + 1) / 4) for i in range(4)]
        for index, (start, end) in enumerate(shot_ranges):
            shot = Shot(timeline_revision_id=revision.id, position=index, start_sec=start, end_sec=end, analysis_status="succeeded")
            session.add(shot)
            session.flush()
            if confirm_shots:
                session.add(ShotEdit(project_id=project_id, shot_id=shot.id, action="展示", confirmed=True))
            shot_ids.append(str(shot.id))
        if mode == "replace_product":
            session.add(Asset(
                project_id=project_id, kind="product_reference_image",
                original_path="C:/p.png", original_filename="front.png", content_type="image/png",
                profile_text="已确认目标产品",
                profile_json=json.dumps({"view_label": "front", "display_name": "front", "summary_confirmed": True}),
                analysis_status="succeeded",
            ))
        session.commit()
        video_id, revision_id = video.id, revision.id
    plan = client.post(f"/api/projects/{project_id}/generation-segments/auto")
    assert plan.status_code == 201
    segments = plan.json()["segments"]
    assert len(segments) == 2
    # 完整提示词（覆盖全部 4 镜头绝对时间）。
    from app.services.final_prompt import build_full_prompt_prefix
    if mode == "replace_product":
        prefix = build_full_prompt_prefix(
            project_mode="replace_product", product_profile="已确认目标产品",
            product_image_purposes=["front：锁定该角度结构"], people_reference=None,
            background_reference=None, audio_mode="keep_original", audio_style="",
        )
    else:
        prefix = build_full_prompt_prefix(
            project_mode="preserve_product", product_profile="", product_image_purposes=[],
            people_reference=None, background_reference=None, audio_mode="keep_original", audio_style="",
        )
    blocks = []
    for start, end in shot_ranges:
        blocks.append(f"{_fmt(start)}–{_fmt(end)}\n保持：镜头 {start} 保持正文\n修改：无。\n删除：无。\n禁止：无。")
    full_text = prefix + "\n\n" + "\n\n".join(blocks)
    if confirm_shots:
        prompt = client.post(
            f"/api/projects/{project_id}/prompts",
            json={"visual_direction": full_text, "use_ai": False},
        )
        assert prompt.status_code == 201
        prompt_version = prompt.json()["version"]
    else:
        # 未确认镜头时无法通过 API 创建 prompt；直接数据库直插有效 full 提示词，
        # 让批次校验真正走到“镜头未确认”分支。
        with SessionLocal() as session:
            revision = session.scalar(select(TimelineRevision).where(TimelineRevision.project_id == UUID(str(project_id))))
            prompt = PromptRevision(
                project_id=UUID(str(project_id)), version=1, prompt_mode="full_reference_video_edit",
                generation_segment_id=None, source_timeline_revision_id=revision.id,
                text=full_text, status="completed",
            )
            session.add(prompt)
            session.commit()
            prompt_version = prompt.version
    return SimpleNamespace(
        project_id=str(project_id), revision_id=str(revision_id), video_id=str(video_id),
        segments=segments, prompt_version=prompt_version, mode=mode,
        batch_url=f"/api/projects/{project_id}/generation-batches",
        payload={
            "provider": "volcengine",
            "prompt_version": prompt.json()["version"],
            "generate_audio": False,
            "include_person_reference": False,
            "include_background_reference": False,
        },
    )


def _fmt(value: float) -> str:
    return f"{int(value // 60):02d}:{value % 60:05.2f}"


def _generation_count(client: TestClient, project_id) -> int:
    with SessionLocal() as session:
        return len(session.scalars(__import__("sqlalchemy").select(Generation).where(Generation.project_id == UUID(project_id))).all())


def test_batch_creation_creates_two_rows_in_one_batch(client, tmp_path) -> None:
    """happy path：一次请求创建两行，共享 batch UUID，位置 1/2，大小 2。"""
    project = _batch_project(client, tmp_path)
    response = client.post(project.batch_url, json=project.payload)
    assert response.status_code == 201
    body = response.json()
    assert body["batch_size"] == 2
    assert body["status"] == "queued"
    assert len(body["generations"]) == 2
    batch_id = body["generation_batch_id"]
    positions = [item["batch_position"] for item in body["generations"]]
    assert positions == [1, 2]
    with SessionLocal() as session:
        rows = list(session.scalars(__import__("sqlalchemy").select(Generation).where(Generation.project_id == UUID(project.project_id)).order_by(Generation.batch_position)))
        assert len(rows) == 2
        assert all(str(row.generation_batch_id) == str(batch_id) for row in rows)
        assert [row.batch_position for row in rows] == [1, 2]
        assert all(row.batch_size == 2 for row in rows)
        assert all(row.provider == "volcengine" for row in rows)
        assert all(row.prompt_version == project.prompt_version for row in rows)
        # reference_asset_ids[0] 是原视频。
        assert json.loads(rows[0].reference_asset_ids)[0] == str(project.video_id)
        # 位置对应 segment 顺序。
        assert rows[0].generation_segment_id == UUID(project.segments[0]["id"])
        assert rows[1].generation_segment_id == UUID(project.segments[1]["id"])


def test_batch_creation_no_provider_call_during_http(client, tmp_path, monkeypatch) -> None:
    """HTTP 创建不调用任何 provider 网关。"""
    from unittest.mock import patch
    from app.services.seedance import JsonTaskGateway
    project = _batch_project(client, tmp_path)
    with patch.object(JsonTaskGateway, "submit", side_effect=AssertionError("provider called")):
        response = client.post(project.batch_url, json=project.payload)
    assert response.status_code == 201


def test_batch_creation_rejects_missing_project(client, tmp_path) -> None:
    response = client.post(f"/api/projects/{uuid4()}/generation-batches", json={"provider": "volcengine", "prompt_version": 1})
    assert response.status_code == 404
    assert "项目不存在" in str(response.json()["detail"])


def test_batch_creation_rejects_missing_timeline(client, tmp_path) -> None:
    project = client.post("/api/projects", json={"name": "no-timeline", "mode": "preserve_product"}).json()
    response = client.post(f"/api/projects/{project['id']}/generation-batches", json={"provider": "volcengine", "prompt_version": 1})
    assert response.status_code == 422
    assert _generation_count(client, project["id"]) == 0


def test_batch_creation_rejects_unconfirmed_shot(client, tmp_path) -> None:
    """先建已确认项目+方案，再把镜头改为未确认 → 批次 422。"""
    project = _batch_project(client, tmp_path)
    with SessionLocal() as session:
        for edit in session.scalars(__import__("sqlalchemy").select(ShotEdit).where(ShotEdit.project_id == UUID(project.project_id))):
            edit.confirmed = False
        session.commit()
    response = client.post(project.batch_url, json=project.payload)
    assert response.status_code == 422
    assert _generation_count(client, project.project_id) == 0


def test_batch_creation_rejects_historical_prompt_mode(client, tmp_path) -> None:
    project = _batch_project(client, tmp_path)
    # 把一个历史 reference_video_edit 提示词作为源。
    with SessionLocal() as session:
        revision = session.scalar(__import__("sqlalchemy").select(TimelineRevision).where(TimelineRevision.project_id == UUID(project.project_id)))
        legacy = PromptRevision(
            project_id=UUID(project.project_id), version=99, prompt_mode="reference_video_edit",
            generation_segment_id=None, source_timeline_revision_id=revision.id,
            text="00:00.00–00:12.50\n保持：a\n修改：无。\n删除：无。\n禁止：无。", status="completed",
        )
        session.add(legacy)
        session.commit()
        legacy_version = legacy.version
    response = client.post(project.batch_url, json={**project.payload, "prompt_version": legacy_version})
    assert response.status_code == 422
    assert _generation_count(client, project.project_id) == 0


def test_batch_creation_rejects_duplicate_active(client, tmp_path) -> None:
    """重复活动批次 → 409；全部 terminal 后允许新批次。"""
    project = _batch_project(client, tmp_path)
    first = client.post(project.batch_url, json=project.payload)
    assert first.status_code == 201
    duplicate = client.post(project.batch_url, json=project.payload)
    assert duplicate.status_code == 409
    # 全部 terminal 后允许。
    with SessionLocal() as session:
        rows = list(session.scalars(__import__("sqlalchemy").select(Generation).where(Generation.project_id == UUID(project.project_id))))
        for row in rows:
            row.status = "completed"
        session.commit()
    retry_batch = client.post(project.batch_url, json=project.payload)
    assert retry_batch.status_code == 201


def test_batch_creation_rejects_missing_provider_key(client, tmp_path, monkeypatch) -> None:
    """缺 provider key → 409，不创建行。"""
    from types import SimpleNamespace as _NS
    fake = _NS(volcengine_api_key="", volcengine_seedance_base_url="https://x", volcengine_seedance_task_path="/t",
               comfly_api_key="", comfly_base_url="https://y", comfly_seedance_task_path="/t",
               effective_segment_limit_seconds=29.0, recommended_min_segment_seconds=8.0)
    monkeypatch.setattr("app.api.routes.generation_batches.Settings", lambda: fake)
    project = _batch_project(client, tmp_path)
    response = client.post(project.batch_url, json=project.payload)
    assert response.status_code == 409
    assert _generation_count(client, project.project_id) == 0


def test_batch_creation_rejects_stale_plan(client, tmp_path) -> None:
    project = _batch_project(client, tmp_path)
    # 创建新时间轴 v2，旧方案失效。
    with SessionLocal() as session:
        newer = TimelineRevision(project_id=UUID(project.project_id), version=2, source="human")
        session.add(newer)
        session.commit()
    response = client.post(project.batch_url, json=project.payload)
    assert response.status_code == 422
    assert _generation_count(client, project.project_id) == 0


def test_legacy_single_endpoint_conflicts_with_active_batch(client, tmp_path) -> None:
    """旧单 Generation 端点对已有活动批次任务的片段返回 409。"""
    project = _batch_project(client, tmp_path)
    batch = client.post(project.batch_url, json=project.payload)
    assert batch.status_code == 201
    # 旧单端点用历史 reference_video_edit 提示词 + 同一 segment。
    from app.db.models import GenerationSegment
    with SessionLocal() as session:
        segment = session.scalar(select(GenerationSegment).where(
            GenerationSegment.project_id == UUID(project.project_id),
            GenerationSegment.position == 0,
        ))
        legacy = PromptRevision(
            project_id=UUID(project.project_id), version=98, prompt_mode="reference_video_edit",
            generation_segment_id=segment.id,
            source_timeline_revision_id=UUID(project.revision_id),
            text="00:00.00–00:12.50\n保持：a\n修改：无。\n删除：无。\n禁止：无。", status="completed",
        )
        session.add(legacy)
        session.commit()
        legacy_version = legacy.version
    response = client.post(
        f"/api/projects/{project.project_id}/generations",
        json={"provider": "volcengine", "prompt_version": legacy_version, "generation_segment_id": str(segment.id)},
    )
    assert response.status_code == 409
    assert "活动批次" in str(response.json()["detail"])


def test_batch_creation_allows_explicitly_accepted_short_segment(client, tmp_path) -> None:
    """短段已明确确认且方案整体合法时，批次创建成功。"""
    project = _batch_project(client, tmp_path, duration_sec=40.0)
    # 手动 3 段方案：0-5(短已确认) 5-29 29-40。
    plan = client.put(f"/api/projects/{project.project_id}/generation-segments", json={"segments": [
        {"source_start_sec": 0, "source_end_sec": 5, "start_boundary_type": "video_edge", "end_boundary_type": "inside_shot", "short_segment_accepted": True},
        {"source_start_sec": 5, "source_end_sec": 29, "start_boundary_type": "inside_shot", "end_boundary_type": "inside_shot", "short_segment_accepted": False},
        {"source_start_sec": 29, "source_end_sec": 40, "start_boundary_type": "inside_shot", "end_boundary_type": "video_edge", "short_segment_accepted": False},
    ]})
    assert plan.status_code == 201
    response = client.post(project.batch_url, json=project.payload)
    assert response.status_code == 201
    body = response.json()
    assert body["batch_size"] == 3
    assert [item["batch_position"] for item in body["generations"]] == [1, 2, 3]


def test_derive_batch_status_combinations() -> None:
    from app.api.routes.generation_batches import derive_batch_status
    assert derive_batch_status(["submission_uncertain", "completed"]) == "uncertain"
    assert derive_batch_status(["queued", "queued"]) == "queued"
    assert derive_batch_status(["queued", "processing"]) == "processing"
    assert derive_batch_status(["processing", "retryable"]) == "processing"
    assert derive_batch_status(["completed", "completed"]) == "complete"
    assert derive_batch_status(["completed", "failed"]) == "partial"
    assert derive_batch_status(["failed", "failed"]) == "failed"
    assert derive_batch_status([]) == "failed"


def test_batch_list_and_detail_status(client, tmp_path) -> None:
    """list/detail 返回派生状态；detail 404 当批次不属于项目。"""
    project = _batch_project(client, tmp_path)
    created = client.post(project.batch_url, json=project.payload)
    assert created.status_code == 201
    batch_id = created.json()["generation_batch_id"]
    # 全 queued → queued。
    listed = client.get(f"/api/projects/{project.project_id}/generation-batches").json()
    assert len(listed) == 1
    assert listed[0]["status"] == "queued"
    detail = client.get(f"/api/projects/{project.project_id}/generation-batches/{batch_id}").json()
    assert detail["status"] == "queued"
    assert [g["batch_position"] for g in detail["generations"]] == [1, 2]
    # 改一行 completed、一行 failed → partial。
    with SessionLocal() as session:
        rows = list(session.scalars(select(Generation).where(Generation.project_id == UUID(project.project_id)).order_by(Generation.batch_position)))
        rows[0].status = "completed"
        rows[1].status = "failed"
        session.commit()
    detail2 = client.get(f"/api/projects/{project.project_id}/generation-batches/{batch_id}").json()
    assert detail2["status"] == "partial"
    # 其他项目批次 → 404。
    other = client.post("/api/projects", json={"name": "other", "mode": "preserve_product"}).json()
    missing = client.get(f"/api/projects/{other['id']}/generation-batches/{batch_id}")
    assert missing.status_code == 404


def test_batch_retry_preserves_slot_and_reruns_validation(client, tmp_path) -> None:
    """retry 失败 batch 行保持槽位；旧时间轴时 422 不建行。"""
    project = _batch_project(client, tmp_path)
    created = client.post(project.batch_url, json=project.payload)
    assert created.status_code == 201
    rows = created.json()["generations"]
    gen_a = rows[0]
    # 标 failed。
    with SessionLocal() as session:
        g = session.get(Generation, UUID(gen_a["id"]))
        g.status = "failed"
        session.commit()
    retried = client.post(f"/api/projects/{project.project_id}/generations/{gen_a['id']}/retry")
    assert retried.status_code == 202
    payload = retried.json()
    assert payload["generation_batch_id"] == gen_a["generation_batch_id"]
    assert payload["batch_position"] == 1
    assert payload["batch_size"] == 2
    # 旧时间轴时 retry 422。
    with SessionLocal() as session:
        newer = TimelineRevision(project_id=UUID(project.project_id), version=2, source="human")
        session.add(newer)
        session.commit()
    blocked = client.post(f"/api/projects/{project.project_id}/generations/{gen_a['id']}/retry")
    assert blocked.status_code == 422


def test_batch_content_download_filename_has_position(client, tmp_path) -> None:
    """download=true 时 content 返回 attachment，文件名含批次位置。"""
    from app.db.models import Generation
    project = _batch_project(client, tmp_path)
    created = client.post(project.batch_url, json=project.payload)
    assert created.status_code == 201
    from app.core.config import Settings
    with SessionLocal() as session:
        row = session.scalar(select(Generation).where(Generation.project_id == UUID(project.project_id), Generation.batch_position == 2))
        row.status = "completed"
        result_dir = Settings().media_root / str(project.project_id) / "generated"
        result_dir.mkdir(parents=True, exist_ok=True)
        result = result_dir / "v2.mp4"
        result.write_bytes(b"video")
        row.result_path = str(result)
        session.commit()
        gen_id = row.id
    response = client.get(f"/api/projects/{project.project_id}/generations/{gen_id}/content?download=true")
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")
    assert "segment-2.mp4" in response.headers.get("content-disposition", "")
