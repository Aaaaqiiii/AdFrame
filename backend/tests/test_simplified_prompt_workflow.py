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
from app.api.routes.analysis import _job_status, request_shot_vision_analysis
from app.services.vision_jobs import execute_vision_job
from app.services.final_prompt import execute_final_prompt_job, execute_prompt_refinement_job
from app.services.volcengine_vision import VisionTaskResult


class CompletedShotGateway:
    def upload_video(self, source: Path) -> str:
        return str(source)

    def submit_vision(self, source: str) -> str:
        return "local-result"

    def get_result(self, task_id: str) -> VisionTaskResult:
        bundle = {
            "qwen": {"action": "右手拿起瓶子"},
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


def test_retryable_job_status_is_not_hidden_as_ordinary_queueing() -> None:
    assert _job_status("retryable") == "retryable"


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
        assert {item.provider for item in analyses} == {"qwen_full_shot"}


def test_final_prompt_uses_only_confirmed_edit_and_can_be_saved_manually() -> None:
    """完整提示词：AI 生成只使用已确认镜头，人工保存立即完成。"""
    from app.services.media import VideoMetadata
    with patch("app.api.routes.projects.probe_video", return_value=VideoMetadata(3.2, 1280, 720, 30)):
        client = TestClient(create_app())
        project = client.post("/api/projects", json={"name": "confirmed prompt"}).json()
        uploaded = client.post(
            f"/api/projects/{project['id']}/reference-video",
            files={"file": ("reference.mp4", b"video-bytes", "video/mp4")},
        )
        assert uploaded.status_code == 202
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
    assert response.status_code == 202
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
        assert revision.prompt_mode == "full_reference_video_edit"
        assert revision.generation_segment_id is None
        assert job is not None
        assert job.status == "queued"
        model_text = "00:00.00–00:03.20\n保持：人物身份、动作节奏、手部位置、背景、构图、镜头运动不变。\n修改：无。\n删除：无。\n禁止：不得新增文字或改变动作。"
        with patch("app.services.final_prompt._chat", return_value=model_text) as chat:
            execute_final_prompt_job(session, job, Settings(comfly_api_key="test-key"))
        session.refresh(revision)
        session.refresh(job)
        assert revision.status == "completed"
        assert "00:00.00–00:03.20" in revision.text
        assert "保持：" in revision.text and "禁止：" in revision.text
        assert job.status == "completed"
        assert chat.call_count >= 1

    from app.services.final_prompt import build_full_prompt_prefix
    manual_prefix = build_full_prompt_prefix(
        project_mode="preserve_product", product_profile="", product_image_purposes=[],
        people_reference=None, background_reference=None, audio_mode="keep_original", audio_style="",
    )
    manual = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": (
            manual_prefix + "\n\n"
            "00:00.00–00:03.20\n保持：a\n修改：无。\n删除：无。\n禁止：无。"
        ), "use_ai": False},
    )
    assert manual.status_code == 201
    assert manual.json()["status"] == "completed"

    missing_audio_requirement = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "完整复刻全部镜头", "use_ai": True, "prompt_mode": "standalone_video_recreation", "audio_mode": "custom"},
    )
    assert missing_audio_requirement.status_code == 422
    assert "音频要求" in str(missing_audio_requirement.json()["detail"])

    standalone = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "完整复刻全部镜头", "use_ai": True, "prompt_mode": "standalone_video_recreation"},
    )
    assert standalone.status_code == 202
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == standalone.json()["version"],
        ))
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.provider_input_id == str(revision.id),
        ))
        assert revision.prompt_mode == "standalone_video_recreation"
        assert revision.audio_mode == "none"
        recreation_text = (
            "全局一致性：人物和场景保持一致。\n\n00:00.00–00:03.20\n"
            "画面：人工确认人物位于明亮室内。\n动作：人工确认动作完整发生。\n"
            "镜头：中近景缓慢推近。\n光线：左前方柔光。\n声音：不生成新音频。\n禁止：不得新增文字。"
        )
        with patch("app.services.final_prompt._chat", return_value=recreation_text):
            execute_final_prompt_job(session, job, Settings(comfly_api_key="test-key"))
        session.refresh(revision)
        assert revision.status == "completed"
        assert "画面：" in revision.text and "镜头：" in revision.text
        assert "保持：" not in revision.text
    filtered = client.get(
        f"/api/projects/{project['id']}/prompts?status=completed&prompt_mode=standalone_video_recreation"
    )
    assert filtered.status_code == 200
    assert [item["version"] for item in filtered.json()] == [standalone.json()["version"]]
    assert filtered.json()[0]["audio_mode"] == "none"


def test_prompt_refinement_worker_uses_queued_source_snapshot() -> None:
    source_text = "00:00.00–00:03.00\nfirst source"
    refined_text = "00:00.00–00:03.00\nrevised v1"
    with SessionLocal() as session:
        project = Project(name="refinement worker snapshot")
        session.add(project)
        session.flush()
        session.add(PromptRevision(
            project_id=project.id, version=1, text=source_text,
            visual_direction="first", status="completed",
        ))
        queued = PromptRevision(
            project_id=project.id, version=3, text=source_text,
            visual_direction="修改第一版", status="queued",
        )
        session.add(queued)
        session.flush()
        job = Job(
            project_id=project.id, kind="prompt_refinement", status="queued",
            provider_input_id=str(queued.id),
        )
        session.add_all([
            job,
            PromptRevision(
                project_id=project.id, version=2,
                text="00:00.00–00:03.00\nsecond source",
                visual_direction="second", status="completed",
            ),
        ])
        session.commit()

        with patch("app.services.final_prompt.refine_prompt", return_value=refined_text) as refine:
            execute_prompt_refinement_job(session, job, Settings(comfly_api_key="test-key"))

        assert refine.call_args.args[0] == source_text
        session.refresh(queued)
        session.refresh(job)
        assert queued.text == refined_text
        assert queued.status == "completed"
        assert job.status == "completed"
