from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Asset, Generation, Job, PromptRevision, Shot, ShotEdit, TimelineRevision
from app.db.session import SessionLocal
from app.main import create_app
from app.services.final_prompt import execute_final_prompt_job, execute_prompt_refinement_job
from app import worker


def _client() -> TestClient:
    return TestClient(create_app())


def _project(client: TestClient, mode: str = "preserve_product") -> dict:
    response = client.post("/api/projects", json={"name": f"workflow-{mode}", "mode": mode})
    assert response.status_code == 201
    return response.json()


def _upload_product(client: TestClient, project_id: str) -> dict:
    with patch("app.services.reference_profiles.Settings") as settings:
        settings.return_value.openai_api_key = "configured"
        response = client.post(
            f"/api/projects/{project_id}/reference-images/product",
            files={"file": ("product.png", b"not-a-real-image-but-stored", "image/png")},
        )
    assert response.status_code == 202
    return response.json()


def test_project_mode_is_persisted_and_defaults_to_preserve_product() -> None:
    client = _client()
    default_project = client.post("/api/projects", json={"name": "legacy default"}).json()
    replace_project = _project(client, "replace_product")

    assert default_project["mode"] == "preserve_product"
    assert replace_project["mode"] == "replace_product"
    assert client.get(f"/api/projects/{replace_project['id']}").json()["mode"] == "replace_product"
    invalid = client.post("/api/projects", json={"name": "invalid", "mode": "anything"})
    assert invalid.status_code == 422


def test_product_upload_is_previewable_and_analysis_is_persisted_as_a_job() -> None:
    client = _client()
    project = _project(client, "replace_product")

    with patch("app.api.routes.projects.Settings") as settings:
        settings.return_value.media_root = __import__("pathlib").Path("E:/工具-商用/data/test-media")
        settings.return_value.openai_api_key = "configured"
        uploaded = _upload_product(client, project["id"])

    assert uploaded["analysis_status"] == "queued"
    assert uploaded["job_id"]
    preview = client.get(f"/api/projects/{project['id']}/reference-images/product/content")
    assert preview.status_code == 200
    assert preview.content == b"not-a-real-image-but-stored"

    details = client.get(f"/api/projects/{project['id']}").json()
    assert details["product_reference_image_url"].endswith("/reference-images/product/content")
    assert details["product_analysis_status"] == "queued"


def test_missing_comfly_key_is_a_clear_persisted_product_analysis_state() -> None:
    client = _client()
    project = _project(client, "replace_product")
    with patch("app.api.routes.projects.Settings") as settings:
        settings.return_value.media_root = __import__("pathlib").Path("E:/工具-商用/data/test-media")
        settings.return_value.comfly_api_key = ""
        uploaded = _upload_product(client, project["id"])

    assert uploaded["analysis_status"] == "failed"
    profile = client.get(f"/api/projects/{project['id']}/product-profile").json()
    assert profile["status"] == "failed"
    assert "Comfly API Key" in profile["error"]


def test_product_profile_can_be_corrected_and_restored() -> None:
    client = _client()
    project = _project(client, "replace_product")
    _upload_product(client, project["id"])

    response = client.put(
        f"/api/projects/{project['id']}/product-profile",
        json={
            "profile": "纸盒装牛奶，顶部吸管口，拿起后饮用",
            "structure": {"category": "盒装产品", "interaction": "拿起饮用"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["user_edited"] is True
    restored = client.get(f"/api/projects/{project['id']}/product-profile").json()
    assert restored["profile"].startswith("纸盒装牛奶")
    assert restored["structure"]["category"] == "盒装产品"


def test_page_two_requires_target_product_before_prompt_or_generation() -> None:
    client = _client()
    project = _project(client, "replace_product")

    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"product_profile": "", "visual_direction": "保持原节奏", "use_ai": False},
    )

    assert response.status_code == 422
    assert "替换产品" in str(response.json()["detail"])


def test_page_two_rejects_incompatible_actions_with_chinese_shot_details() -> None:
    client = _client()
    project = _project(client, "replace_product")
    _upload_product(client, project["id"])
    original = client.put(
        f"/api/projects/{project['id']}/product-profile",
        json={"profile": "牙膏外壳状软管产品，旋盖开口，通过挤压软管挤出内容物"},
    )
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 2}, {"start_sec": 2, "end_sec": 4}]},
    ).json()
    with SessionLocal() as session:
        shot = session.get(Shot, UUID(timeline["shots"][0]["id"]))
        shot.product_interaction = "拿起产品并直接喝下内容物"
        session.commit()

    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"product_profile": "牙膏外壳状软管产品", "visual_direction": "保持原节奏"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"] == "目标产品形态与部分原镜头动作不兼容，请先修正这些镜头"
    assert timeline["shots"][0]["id"] in detail["shot_ids"]
    assert "软管" in detail["conflicts"][0]["reason"]


def test_page_two_applies_target_product_to_every_product_shot() -> None:
    client = _client()
    project = _project(client, "replace_product")
    _upload_product(client, project["id"])
    profile = "纸盒装牛奶，顶部吸管口，拿起后饮用"
    profile_response = client.put(
        f"/api/projects/{project['id']}/product-profile",
        json={"profile": profile, "structure": {"summary_confirmed": True}},
    )
    assert profile_response.status_code == 200
    assert profile_response.json()["status"] == "succeeded"
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 2}, {"start_sec": 2, "end_sec": 4}]},
    ).json()
    for shot, product_interaction in zip(timeline["shots"], ("桌面产品特写", "拿起盒子饮用")):
        edit = client.put(
            f"/api/projects/{project['id']}/shots/{shot['id']}/edit",
            json={
                "product": "纸盒装牛奶",
                "product_interaction": product_interaction,
                "confirmed": True,
            },
        )
        assert edit.status_code == 200
        assert edit.json()["confirmed"] is True

    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"product_profile": profile, "visual_direction": "背景改为厨房", "use_ai": True},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    # AI 入队返回冻结的确定性前缀（非空），Worker 完成后再替换为完整提示词。
    assert response.json()["text"].startswith("原参考视频是时间轴")
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == response.json()["version"],
        ))
        assert revision is not None
        assert revision.prompt_mode == "full_reference_video_edit"
        assert revision.generation_segment_id is None
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "final_prompt_generation",
            Job.provider_input_id == str(revision.id),
        ))
        assert job is not None
        assert job.status == "queued"
        model_text = (
            "00:00.00–00:02.00\n保持：a\n修改：桌面产品特写\n删除：无。\n禁止：无。\n\n"
            "00:02.00–00:04.00\n保持：b\n修改：拿起盒子饮用\n删除：无。\n禁止：无。"
        )
        with patch("app.services.final_prompt._chat", return_value=model_text):
            execute_final_prompt_job(session, job, Settings(comfly_api_key="test-comfly-api-key"))
        session.refresh(revision)
        session.refresh(job)
        assert revision.status == "completed"
        assert job.status == "completed"
        assert "00:00.00–00:02.00" in revision.text
        assert "00:02.00–00:04.00" in revision.text
        assert profile in revision.text


def test_product_compatibility_can_be_checked_before_prompt_save() -> None:
    client = _client()
    project = _project(client, "replace_product")
    pending = client.get(f"/api/projects/{project['id']}/product-compatibility")
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"

    _upload_product(client, project["id"])
    client.put(
        f"/api/projects/{project['id']}/product-profile",
        json={"profile": "罐装膏体产品，旋盖打开后用手指取出内容物"},
    )
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}]},
    ).json()
    with SessionLocal() as session:
        shot = session.get(Shot, UUID(timeline["shots"][0]["id"]))
        shot.product_interaction = "拿起盒子直接喝产品"
        session.commit()

    compatibility = client.get(f"/api/projects/{project['id']}/product-compatibility").json()
    assert compatibility["status"] == "blocked"
    assert compatibility["conflicts"][0]["shot_id"] == timeline["shots"][0]["id"]
    assert compatibility["conflicts"][0]["suggestion"]


def test_preserve_product_mode_rejects_replacement_during_prompt_creation() -> None:
    client = _client()
    project = _project(client, "preserve_product")
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"product_profile": "原产品", "visual_direction": "把原产品替换为牛奶盒"},
    )
    assert response.status_code == 422


def test_preserve_mode_rejects_replace_product_even_with_legacy_target_asset() -> None:
    client = _client()
    project = _project(client, "preserve_product")
    with SessionLocal() as session:
        session.add(Asset(
            project_id=UUID(project["id"]),
            kind="target_product_reference_image",
            original_path="C:/legacy.png",
            profile_text="旧目标产品",
            profile_json='{"summary_confirmed": true}',
            analysis_status="succeeded",
        ))
        session.commit()

    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "保持节奏", "replace_product": True, "use_ai": False},
    )

    assert response.status_code == 422


def test_replace_mode_forces_replacement_when_client_sends_false() -> None:
    client = _client()
    project = _project(client, "replace_product")
    _upload_product(client, project["id"])
    client.put(
        f"/api/projects/{project['id']}/product-profile",
        json={"profile": "已确认盒装产品", "structure": {"summary_confirmed": True}},
    )
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}]},
    ).json()
    client.put(
        f"/api/projects/{project['id']}/shots/{timeline['shots'][0]['id']}/edit",
        json={"product": "原产品", "confirmed": True},
    )

    from app.services.final_prompt import build_full_prompt_prefix
    prefix = build_full_prompt_prefix(
        project_mode="replace_product", product_profile="已确认盒装产品",
        product_image_purposes=["other：锁定该角度结构"], people_reference=None,
        background_reference=None, audio_mode="keep_original", audio_style="",
    )
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={
            "visual_direction": (
                prefix + "\n\n"
                "00:00.00–00:03.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"
            ),
            "replace_product": False,
            "use_ai": False,
        },
    )

    assert response.status_code == 201
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"])
        ))
        assert revision is not None
        # 服务端以项目模式为准，客户端传 false 也被强制为替换模式。
        assert revision.replace_product is True


def test_refinement_uses_project_mode_not_source_revision() -> None:
    from app.services.final_prompt import build_full_prompt_prefix
    client = _client()
    project = _project(client, "preserve_product")
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}]},
    ).json()
    client.put(
        f"/api/projects/{project['id']}/shots/{timeline['shots'][0]['id']}/edit",
        json={"action": "展示", "confirmed": True},
    )
    prefix = build_full_prompt_prefix(
        project_mode="preserve_product", product_profile="", product_image_purposes=[],
        people_reference=None, background_reference=None,
        audio_mode="keep_original", audio_style="",
    )
    text = prefix + "\n\n" + "00:00.00–00:03.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"
    v1 = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": text, "use_ai": False},
    )
    assert v1.status_code == 201

    response = client.post(
        f"/api/projects/{project['id']}/prompts/refine",
        json={"instruction": "增强光线", "source_version": v1.json()["version"]},
    )

    assert response.status_code == 202
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == response.json()["version"],
        ))
        assert revision is not None
        assert revision.replace_product is False


def test_final_prompt_worker_rejects_replacement_output_in_preserve_mode() -> None:
    client = _client()
    project = _project(client, "preserve_product")
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}]},
    ).json()
    client.put(
        f"/api/projects/{project['id']}/shots/{timeline['shots'][0]['id']}/edit",
        json={"product": "原产品", "confirmed": True},
    )
    client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "保持节奏", "use_ai": True},
    )

    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == 1,
        ))
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "final_prompt_generation",
        ))
        assert revision is not None
        assert job is not None
        # 四栏目齐全，但“修改”栏目写入了产品替换 → 完整提示词 worker 必须拒绝。
        malicious = "00:00.00–00:03.00\n保持：a\n修改：将原产品替换为新产品。\n删除：无。\n禁止：无。"
        with patch("app.services.final_prompt._chat", return_value=malicious):
            try:
                execute_final_prompt_job(session, job, Settings(comfly_api_key="test-comfly-api-key"))
            except ValueError as error:
                assert "保留产品模式" in str(error)
            else:
                assert False, "preserve-mode worker must reject replacement output"
        assert revision.text.startswith("原参考视频是时间轴")
        assert revision.status == "queued"
        assert job.status == "queued"


def test_refinement_worker_rejects_replacement_output_in_preserve_mode() -> None:
    from app.services.final_prompt import build_full_prompt_prefix
    client = _client()
    project = _project(client, "preserve_product")
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}]},
    ).json()
    client.put(
        f"/api/projects/{project['id']}/shots/{timeline['shots'][0]['id']}/edit",
        json={"action": "展示", "confirmed": True},
    )
    prefix = build_full_prompt_prefix(
        project_mode="preserve_product", product_profile="", product_image_purposes=[],
        people_reference=None, background_reference=None,
        audio_mode="keep_original", audio_style="",
    )
    text = prefix + "\n\n" + "00:00.00–00:03.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"
    v1 = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": text, "use_ai": False},
    )
    assert v1.status_code == 201
    client.post(
        f"/api/projects/{project['id']}/prompts/refine",
        json={"instruction": "调整画面", "source_version": v1.json()["version"]},
    )

    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == v1.json()["version"] + 1,
        ))
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "prompt_refinement",
        ))
        assert revision is not None
        assert job is not None
        # 块正文的“修改”栏目写入了产品替换 → 精修必须拒绝。
        with patch("app.services.final_prompt._chat", return_value="00:00.00–00:03.00\n保持：a\n修改：将原产品替换为新产品。\n删除：无。\n禁止：无。"):
            try:
                execute_prompt_refinement_job(session, job, Settings(comfly_api_key="test-comfly-api-key"))
            except ValueError as error:
                assert "保留产品模式" in str(error)
            else:
                assert False, "preserve-mode worker must reject replacement output"
        assert revision.status == "queued"
        assert job.status == "queued"


def test_run_once_discards_unsafe_final_prompt_before_retry_commit() -> None:
    with SessionLocal() as session:
        for model in (Job, Generation):
            for record in session.scalars(select(model).where(model.status.in_(["queued", "uploaded", "processing", "retryable"]))):
                record.status = "failed"
        session.commit()
    client = _client()
    project = _project(client, "preserve_product")
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}]},
    ).json()
    client.put(
        f"/api/projects/{project['id']}/shots/{timeline['shots'][0]['id']}/edit",
        json={"product": "原产品", "confirmed": True},
    )
    client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "保持节奏", "use_ai": True},
    )

    with patch("app.worker.Settings", return_value=Settings(comfly_api_key="test-comfly-api-key")), patch(
        "app.services.final_prompt._chat", return_value="00:00.00–00:03.00\nreplace the bottle with shampoo",
    ):
        assert worker.run_once() == 1

    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(PromptRevision.project_id == UUID(project["id"])))
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "final_prompt_generation",
        ))
        assert revision is not None
        assert job is not None
        # 拒绝后保持排队时冻结的确定性前缀，绝不标记 completed。
        assert revision.text.startswith("原参考视频是时间轴")
        assert revision.status != "completed"
        assert revision.status in {"retryable", "failed"}
        assert job.status == revision.status


def test_human_timeline_preserves_overlapping_facts_without_starting_models() -> None:
    client = _client()
    project = _project(client)
    original = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [
            {"start_sec": 0, "end_sec": 2},
            {"start_sec": 2, "end_sec": 4},
            {"start_sec": 4, "end_sec": 6},
        ]},
    ).json()
    with SessionLocal() as session:
        shots = [session.get(Shot, UUID(item["id"])) for item in original["shots"]]
        for index, shot in enumerate(shots):
            shot.people = f"人物{index + 1}"
            shot.analysis_status = "succeeded"
        session.add(ShotEdit(project_id=UUID(project["id"]), shot_id=shots[1].id, background="蓝色影棚"))
        session.commit()

    revised = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [
            {"start_sec": 0, "end_sec": 2},
            {"start_sec": 2, "end_sec": 5},
            {"start_sec": 5, "end_sec": 6},
        ]},
    )

    assert revised.status_code == 202
    body = revised.json()
    assert body["affected_shot_ids"] == [body["shots"][1]["id"], body["shots"][2]["id"]]
    details = client.get(f"/api/projects/{project['id']}").json()
    assert details["timeline"]["shots"][0]["people"] == "人物1"
    assert details["timeline"]["shots"][0]["analysis_status"] == "succeeded"
    assert details["timeline"]["shots"][1]["edit"]["background"] == "蓝色影棚"
    assert details["timeline"]["shots"][1]["analysis_status"] == "queued"
    with SessionLocal() as session:
        queued = session.scalars(select(Job).where(
            Job.project_id == UUID(project["id"]), Job.kind == "vision_shot_analysis", Job.status == "queued"
        )).all()
        assert queued == []


def test_timeline_correction_without_vision_configuration_does_not_create_dead_jobs() -> None:
    client = _client()
    project = _project(client)
    client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 2}, {"start_sec": 2, "end_sec": 4}]},
    ).json()

    revised = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}, {"start_sec": 3, "end_sec": 4}]},
    )

    assert revised.status_code == 202
    assert revised.json()["affected_shot_ids"]
    with SessionLocal() as session:
        dead_jobs = session.scalars(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "vision_shot_analysis",
            Job.shot_id.in_(UUID(shot_id) for shot_id in revised.json()["affected_shot_ids"]),
        )).all()
        assert dead_jobs == []


def test_ai_initial_timeline_can_be_restored_and_single_shot_can_be_retried() -> None:
    client = _client()
    project = _project(client)
    with SessionLocal() as session:
        revision = TimelineRevision(project_id=UUID(project["id"]), version=1, source="vision_hybrid")
        session.add(revision)
        session.flush()
        session.add_all([
            Shot(timeline_revision_id=revision.id, position=0, start_sec=0, end_sec=2, people="AI人物", analysis_status="succeeded"),
            Shot(timeline_revision_id=revision.id, position=1, start_sec=2, end_sec=4, people="AI人物", analysis_status="succeeded"),
        ])
        session.commit()
        revision_id = str(revision.id)
    client.put(f"/api/projects/{project['id']}/timeline", json={"shots": [{"start_sec": 0, "end_sec": 4}]})

    restored = client.post(f"/api/projects/{project['id']}/timeline/{revision_id}/restore-ai")
    assert restored.status_code == 202
    assert len(restored.json()["shots"]) == 2
    shot_id = restored.json()["shots"][0]["id"]

    retry = client.post(f"/api/projects/{project['id']}/analysis/shots/{shot_id}/retry")
    assert retry.status_code == 202
    assert retry.json()["status"] == "queued"
    jobs = client.get(f"/api/projects/{project['id']}/analysis/jobs").json()
    assert any(item["shot_id"] == shot_id and item["status"] == "queued" for item in jobs)
