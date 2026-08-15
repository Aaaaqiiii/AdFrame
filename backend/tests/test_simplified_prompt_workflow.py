import json
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Asset, Job, Project, PromptRevision, Shot, ShotEdit, TimelineRevision, VideoAnalysis
from app.db.session import SessionLocal
from app.main import create_app
from app.api.routes.analysis import request_shot_vision_analysis
from app.services.vision_jobs import execute_vision_job
from app.services.final_prompt import execute_final_prompt_job
from app.services.volcengine_vision import VisionTaskResult


class CompletedShotGateway:
    def upload_video(self, source: Path) -> str:
        return str(source)

    def submit_vision(self, source: str) -> str:
        return "local-result"

    def get_result(self, task_id: str) -> VisionTaskResult:
        bundle = {
            "doubao": {"action": "拿起瓶子"},
            "gpt": {"action": "右手拿起瓶子"},
            "final": {
                "people": "一名短发女性",
                "action": "人物先拿起瓶子，随后转向镜头展示",
                "product": "透明圆柱瓶",
                "product_interaction": "右手握住瓶身中部",
                "background": "明亮梳妆台",
                "camera": "中近景，缓慢推近",
                "lighting": "左前方柔光",
                "visual_style": "明亮护肤广告",
                "visible_text": "SERUM",
                "keep_unchanged": "镜头时长、动作节奏和运镜",
                "uncertainties": "瓶身小字无法确认",
            },
        }
        return VisionTaskResult(task_id, "Completed", content=json.dumps(bundle, ensure_ascii=False))


def test_batch_analysis_skips_successful_shots() -> None:
    with SessionLocal() as session:
        project = Project(name="incremental analysis")
        session.add(project)
        session.flush()
        session.add(Asset(project_id=project.id, kind="reference_video", original_path="C:/video.mp4"))
        revision = TimelineRevision(project_id=project.id, version=1, source="human")
        session.add(revision)
        session.flush()
        succeeded = Shot(
            timeline_revision_id=revision.id,
            position=0,
            start_sec=0,
            end_sec=2,
            analysis_status="succeeded",
        )
        failed = Shot(
            timeline_revision_id=revision.id,
            position=1,
            start_sec=2,
            end_sec=4,
            analysis_status="failed",
        )
        session.add_all([succeeded, failed])
        session.flush()
        obsolete = Job(
            project_id=project.id,
            shot_id=succeeded.id,
            kind="vision_shot_analysis",
            status="queued",
        )
        session.add(obsolete)
        session.commit()

        with patch("app.api.routes.analysis.validate_dual_vision_configuration"):
            result = request_shot_vision_analysis(project.id, session)

        all_jobs = session.scalars(select(Job).where(
            Job.project_id == project.id,
            Job.kind == "vision_shot_analysis",
        )).all()
        assert result == {
            "queued_shots": 1,
            "skipped_succeeded": 1,
            "already_active": 0,
            "cancelled_obsolete": 1,
        }
        assert [(job.shot_id, job.status) for job in all_jobs] == [
            (succeeded.id, "cancelled"),
            (failed.id, "queued"),
        ]
        assert succeeded.analysis_status == "succeeded"
        assert failed.analysis_status == "queued"


def test_human_timeline_save_does_not_start_models() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "manual boundary"}).json()
    response = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 2}, {"start_sec": 2, "end_sec": 4}]},
    )
    assert response.status_code == 202
    with SessionLocal() as session:
        assert session.scalars(select(Job).where(Job.project_id == UUID(project["id"]), Job.kind == "vision_shot_analysis")).all() == []


def test_dual_results_stay_in_backend_and_only_final_facts_reach_shot() -> None:
    with SessionLocal() as session:
        project = Project(name="dual result")
        session.add(project)
        session.flush()
        asset = Asset(project_id=project.id, kind="reference_video", original_path="C:/video.mp4")
        revision = TimelineRevision(project_id=project.id, version=1, source="human")
        session.add_all([asset, revision])
        session.flush()
        shot = Shot(timeline_revision_id=revision.id, position=0, start_sec=0, end_sec=3)
        session.add(shot)
        session.flush()
        job = Job(project_id=project.id, shot_id=shot.id, kind="vision_shot_analysis", status="queued")
        session.add(job)
        session.commit()
        with patch("app.services.vision_jobs.clip_video", return_value=Path("C:/clip.mp4")):
            execute_vision_job(session, job, CompletedShotGateway())

        session.refresh(shot)
        analyses = session.scalars(select(VideoAnalysis).where(VideoAnalysis.shot_id == shot.id)).all()
        assert shot.action == "人物先拿起瓶子，随后转向镜头展示"
        assert shot.visual_style == "明亮护肤广告"
        assert {item.provider for item in analyses} == {"doubao_full_shot", "gpt_keyframes"}


def test_final_prompt_uses_only_confirmed_edit_and_can_be_saved_manually() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "confirmed prompt"}).json()
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3.2}]},
    ).json()
    shot_id = timeline["shots"][0]["id"]
    edited = client.put(
        f"/api/projects/{project['id']}/shots/{shot_id}/edit",
        json={"people": "人工确认人物", "action": "人工确认动作", "keep_unchanged": ["镜头时长"], "confirmed": True},
    )
    assert edited.status_code == 200

    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "保持原节奏", "replace_product": False, "replace_person": False, "use_ai": True},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == response.json()["version"],
        ))
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "final_prompt_generation",
        ))
        assert revision is not None
        assert job is not None
        assert job.status == "queued"
        with patch("app.services.final_prompt.generate_final_prompt", return_value="00:00.00–00:03.20\n人工确认动作") as generate:
            execute_final_prompt_job(session, job, Settings())
        session.refresh(revision)
        session.refresh(job)
        assert revision.status == "completed"
        assert revision.text == "00:00.00–00:03.20\n人工确认动作"
        assert job.status == "completed"
        assert generate.call_args.kwargs["shots"][0]["facts"]["people"] == "人工确认人物"

    manual = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "人工修改后的最终正文", "use_ai": False},
    )
    assert manual.status_code == 201
    assert manual.json()["text"] == "人工修改后的最终正文"
    assert manual.json()["status"] == "completed"
