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
    response = client.put(project.segments_url, json={
        "segments": [
            {"source_start_sec": 0, "source_end_sec": 15, "start_boundary_type": "video_edge", "end_boundary_type": "shot_boundary", "short_segment_accepted": False},
            {"source_start_sec": 16, "source_end_sec": 32, "start_boundary_type": "shot_boundary", "end_boundary_type": "video_edge", "short_segment_accepted": False},
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
