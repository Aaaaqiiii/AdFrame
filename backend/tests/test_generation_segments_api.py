import json
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select

from app.db.models import Asset, GenerationSegment, PromptRevision, Shot, ShotEdit, TimelineRevision
from app.db.session import SessionLocal


def _segment_project(client, tmp_path, duration_sec=32.0, shots=None, confirm=True, mode="preserve_product"):
    """Build a project with a current timeline of confirmed shots and no segment plan."""
    project_body = client.post("/api/projects", json={"name": "segment-project", "mode": mode}).json()
    project_id = UUID(project_body["id"])
    video_path = tmp_path / "reference.mp4"
    video_path.write_bytes(b"stored-video")
    with SessionLocal() as session:
        video = Asset(
            project_id=project_id,
            kind="reference_video",
            original_path=str(video_path),
            original_filename="reference.mp4",
            content_type="video/mp4",
            duration_sec=duration_sec,
        )
        revision = TimelineRevision(project_id=project_id, version=1, source="human")
        session.add_all([video, revision])
        session.flush()
        ranges = shots or [(0.0, 8.0), (8.0, 16.0), (16.0, 24.0), (24.0, 32.0)]
        for index, (start, end) in enumerate(ranges):
            shot = Shot(timeline_revision_id=revision.id, position=index, start_sec=start, end_sec=end, analysis_status="succeeded")
            session.add(shot)
            session.flush()
            if confirm:
                session.add(ShotEdit(project_id=project_id, shot_id=shot.id, action="展示", confirmed=True))
        session.commit()
        return SimpleNamespace(project_id=project_id, revision_id=revision.id, segments_url=f"/api/projects/{project_id}/generation-segments")


def test_empty_plan_returns_200_with_zero_version(client, tmp_path) -> None:
    project = _segment_project(client, tmp_path)
    response = client.get(project.segments_url)
    assert response.status_code == 200
    body = response.json()
    assert body["plan_version"] == 0
    assert body["timeline_revision_id"] == str(project.revision_id)
    assert body["segments"] == []
    assert body["max_segment_seconds"] == 29
    assert body["recommended_min_seconds"] == 8


def test_auto_plan_returns_balanced_shot_boundary_segments(client, tmp_path) -> None:
    project = _segment_project(client, tmp_path, duration_sec=32.0)
    auto = client.post(f"{project.segments_url}/auto")
    assert auto.status_code == 201
    body = auto.json()
    assert [(x["source_start_sec"], x["source_end_sec"]) for x in body["segments"]] == [(0.0, 16.0), (16.0, 32.0)]
    assert body["plan_version"] == 1
    assert body["segments"][0]["start_boundary_type"] == "video_edge"
    assert body["segments"][0]["end_boundary_type"] == "shot_boundary"
    assert body["segments"][1]["end_boundary_type"] == "video_edge"


def test_manual_plan_creates_new_version_and_is_current(client, tmp_path) -> None:
    project = _segment_project(client, tmp_path, duration_sec=32.0)
    auto = client.post(f"{project.segments_url}/auto")
    assert auto.status_code == 201
    manual = client.put(project.segments_url, json={
        "segments": [
            {"source_start_sec": 0, "source_end_sec": 12, "start_boundary_type": "video_edge", "end_boundary_type": "inside_shot", "short_segment_accepted": False},
            {"source_start_sec": 12, "source_end_sec": 32, "start_boundary_type": "inside_shot", "end_boundary_type": "video_edge", "short_segment_accepted": False},
        ]
    })
    assert manual.status_code == 201
    assert manual.json()["plan_version"] == 2
    current = client.get(project.segments_url).json()
    assert current["plan_version"] == 2
    assert [(x["source_start_sec"], x["source_end_sec"]) for x in current["segments"]] == [(0.0, 12.0), (12.0, 32.0)]
    with SessionLocal() as session:
        # 旧方案记录仍在，只是不再是当前方案。
        versions = list(session.scalars(select(GenerationSegment.plan_version).where(GenerationSegment.project_id == project.project_id).distinct()))
        assert sorted(versions) == [1, 2]


def test_auto_plan_rejects_missing_timeline(client, tmp_path) -> None:
    project_body = client.post("/api/projects", json={"name": "no-timeline", "mode": "preserve_product"}).json()
    project_id = project_body["id"]
    response = client.post(f"/api/projects/{project_id}/generation-segments/auto")
    assert response.status_code == 422
    assert response.json()["detail"] == "请先确认时间轴"


def test_auto_plan_rejects_unconfirmed_shots(client, tmp_path) -> None:
    project = _segment_project(client, tmp_path, confirm=False)
    response = client.post(f"{project.segments_url}/auto")
    assert response.status_code == 422
    assert response.json()["detail"] == "时间轴存在未确认镜头，请先确认全部镜头"


def test_manual_plan_rejects_gap(client, tmp_path) -> None:
    project = _segment_project(client, tmp_path, duration_sec=32.0)
    # 两端类型一致（inside_shot）且位置合法（10/14 都不是分镜边界，位于镜头内部），
    # 但 10→14 之间有空缺，应触发空缺校验。
    response = client.put(project.segments_url, json={
        "segments": [
            {"source_start_sec": 0, "source_end_sec": 10, "start_boundary_type": "video_edge", "end_boundary_type": "inside_shot", "short_segment_accepted": False},
            {"source_start_sec": 14, "source_end_sec": 32, "start_boundary_type": "inside_shot", "end_boundary_type": "video_edge", "short_segment_accepted": False},
        ]
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "生成片段不能有空缺或重叠"


def test_manual_plan_rejects_over_limit(client, tmp_path) -> None:
    project = _segment_project(client, tmp_path, duration_sec=32.0)
    response = client.put(project.segments_url, json={
        "segments": [
            {"source_start_sec": 0, "source_end_sec": 30, "start_boundary_type": "video_edge", "end_boundary_type": "inside_shot", "short_segment_accepted": False},
            {"source_start_sec": 30, "source_end_sec": 32, "start_boundary_type": "inside_shot", "end_boundary_type": "video_edge", "short_segment_accepted": False},
        ]
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "生成片段超过 29 秒安全上限"


def test_manual_plan_rejects_unaccepted_short_segment(client, tmp_path) -> None:
    project = _segment_project(client, tmp_path, duration_sec=32.0, shots=[(0.0, 15.0), (15.0, 22.0), (22.0, 32.0)])
    # 第二段 7 秒 < 8 秒，未确认时拒绝。
    response = client.put(project.segments_url, json={
        "segments": [
            {"source_start_sec": 0, "source_end_sec": 15, "start_boundary_type": "video_edge", "end_boundary_type": "shot_boundary", "short_segment_accepted": False},
            {"source_start_sec": 15, "source_end_sec": 22, "start_boundary_type": "shot_boundary", "end_boundary_type": "shot_boundary", "short_segment_accepted": False},
            {"source_start_sec": 22, "source_end_sec": 32, "start_boundary_type": "shot_boundary", "end_boundary_type": "video_edge", "short_segment_accepted": False},
        ]
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "短片段需要明确确认"
    # 明确确认后通过。
    accepted = client.put(project.segments_url, json={
        "segments": [
            {"source_start_sec": 0, "source_end_sec": 15, "start_boundary_type": "video_edge", "end_boundary_type": "shot_boundary", "short_segment_accepted": False},
            {"source_start_sec": 15, "source_end_sec": 22, "start_boundary_type": "shot_boundary", "end_boundary_type": "shot_boundary", "short_segment_accepted": True},
            {"source_start_sec": 22, "source_end_sec": 32, "start_boundary_type": "shot_boundary", "end_boundary_type": "video_edge", "short_segment_accepted": False},
        ]
    })
    assert accepted.status_code == 201


def test_stale_plan_after_timeline_update_is_not_current(client, tmp_path) -> None:
    """时间轴更新后，旧时间轴的分段方案不得再作为当前方案返回。"""
    project = _segment_project(client, tmp_path, duration_sec=32.0)
    auto = client.post(f"{project.segments_url}/auto")
    assert auto.status_code == 201
    # 创建更新的时间轴版本（旧方案仍挂在旧 revision 上）。
    with SessionLocal() as session:
        newer = TimelineRevision(project_id=project.project_id, version=2, source="human")
        session.add(newer)
        session.commit()
    current = client.get(project.segments_url)
    assert current.status_code == 200
    body = current.json()
    # 新时间轴没有对应方案，返回空当前方案，而不是旧方案。
    assert body["plan_version"] == 0
    assert body["segments"] == []
    assert body["timeline_revision_id"] != str(project.revision_id)


def test_new_timeline_plan_uses_next_project_plan_version(client, tmp_path) -> None:
    """新时间轴的首个方案必须使用项目全局下一版本，避免撞唯一约束。"""
    project = _segment_project(client, tmp_path, duration_sec=32.0)
    v1_plan = client.post(f"{project.segments_url}/auto")
    assert v1_plan.status_code == 201
    assert v1_plan.json()["plan_version"] == 1
    # 创建带完整确认镜头的时间轴 v2。
    with SessionLocal() as session:
        revision2 = TimelineRevision(project_id=project.project_id, version=2, source="human")
        session.add(revision2)
        session.flush()
        for index, (start, end) in enumerate([(0.0, 8.0), (8.0, 16.0), (16.0, 24.0), (24.0, 32.0)]):
            shot = Shot(timeline_revision_id=revision2.id, position=index, start_sec=start, end_sec=end, analysis_status="succeeded")
            session.add(shot)
            session.flush()
            session.add(ShotEdit(project_id=project.project_id, shot_id=shot.id, action="展示", confirmed=True))
        session.commit()
        revision2_id = revision2.id
    # v2 自动规划必须成功，且版本号全局递增为 2，不撞 v1 的唯一约束。
    v2_plan = client.post(f"{project.segments_url}/auto")
    assert v2_plan.status_code == 201
    body = v2_plan.json()
    assert body["plan_version"] == 2
    assert body["timeline_revision_id"] == str(revision2_id)
    # GET 只返回 v2 方案。
    current = client.get(project.segments_url).json()
    assert current["plan_version"] == 2
    # v1 方案仍保留在数据库中。
    with SessionLocal() as session:
        versions = list(session.scalars(select(GenerationSegment.plan_version).where(GenerationSegment.project_id == project.project_id).distinct()))
        assert sorted(versions) == [1, 2]


def test_manual_plan_rejects_false_shot_boundary_claim(client, tmp_path) -> None:
    """切点不在真实分镜边界时，声明 shot_boundary 必须被拒。"""
    project = _segment_project(client, tmp_path, duration_sec=32.0)
    # 12.3 秒不是任何分镜结束点（分镜结束点为 8/16/24/32），却声明 shot_boundary。
    response = client.put(project.segments_url, json={
        "segments": [
            {"source_start_sec": 0, "source_end_sec": 12.3, "start_boundary_type": "video_edge", "end_boundary_type": "shot_boundary", "short_segment_accepted": False},
            {"source_start_sec": 12.3, "source_end_sec": 32, "start_boundary_type": "shot_boundary", "end_boundary_type": "video_edge", "short_segment_accepted": False},
        ]
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "声明为镜头边界的切点必须落在分镜结束点"


def test_manual_plan_rejects_false_inside_shot_claim(client, tmp_path) -> None:
    """切点落在真实分镜边界时，声明 inside_shot 必须被拒。"""
    project = _segment_project(client, tmp_path, duration_sec=32.0)
    # 16 秒是真实分镜边界，却声明 inside_shot。
    response = client.put(project.segments_url, json={
        "segments": [
            {"source_start_sec": 0, "source_end_sec": 16, "start_boundary_type": "video_edge", "end_boundary_type": "inside_shot", "short_segment_accepted": False},
            {"source_start_sec": 16, "source_end_sec": 32, "start_boundary_type": "inside_shot", "end_boundary_type": "video_edge", "short_segment_accepted": False},
        ]
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "声明为镜头内部的切点不能落在分镜边界"


def test_response_does_not_expose_clip_path_or_public_url(client, tmp_path) -> None:
    """输出不得暴露 Worker 内部本地路径或临时公网 URL。"""
    project = _segment_project(client, tmp_path, duration_sec=32.0)
    auto = client.post(f"{project.segments_url}/auto")
    assert auto.status_code == 201
    with SessionLocal() as session:
        segment = session.scalar(select(GenerationSegment).where(GenerationSegment.project_id == project.project_id))
        segment.clip_path = "C:/secret/local/path.mp4"
        segment.public_url = "https://tempfile.org/signed/segment-0/download"
        segment.public_url_expires_at = "2026-08-18T00:00:00Z"
        session.commit()
    body = client.get(project.segments_url).json()
    segment_body = body["segments"][0]
    assert "clip_path" not in segment_body
    assert "public_url" not in segment_body
    assert "public_url_expires_at" not in segment_body


def test_manual_plan_rejects_invalid_boundary_type(client, tmp_path) -> None:
    """非法边界类型字符串必须被 Pydantic 拒绝。"""
    project = _segment_project(client, tmp_path, duration_sec=32.0)
    response = client.put(project.segments_url, json={
        "segments": [
            {"source_start_sec": 0, "source_end_sec": 16, "start_boundary_type": "video_edge", "end_boundary_type": "banana", "short_segment_accepted": False},
            {"source_start_sec": 16, "source_end_sec": 32, "start_boundary_type": "banana", "end_boundary_type": "video_edge", "short_segment_accepted": False},
        ]
    })
    assert response.status_code == 422


def test_manual_plan_rejects_mismatched_shared_boundary_types(client, tmp_path) -> None:
    """同一真实分镜边界切点，两端声明不同类型必须被拒（两端之一位置校验失败）。"""
    project = _segment_project(client, tmp_path, duration_sec=32.0)
    response = client.put(project.segments_url, json={
        "segments": [
            {"source_start_sec": 0, "source_end_sec": 16, "start_boundary_type": "video_edge", "end_boundary_type": "shot_boundary", "short_segment_accepted": False},
            {"source_start_sec": 16, "source_end_sec": 32, "start_boundary_type": "inside_shot", "end_boundary_type": "video_edge", "short_segment_accepted": False},
        ]
    })
    # 16 是真实分镜边界：后段 inside_shot 声明违反"内部切点不得落在分镜边界"。
    assert response.status_code == 422
    assert response.json()["detail"] == "声明为镜头内部的切点不能落在分镜边界"
