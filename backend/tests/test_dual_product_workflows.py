from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Asset, Job, Shot, ShotEdit, TimelineRevision
from app.db.session import SessionLocal
from app.main import create_app


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


def test_missing_openai_key_is_a_clear_persisted_product_analysis_state() -> None:
    client = _client()
    project = _project(client, "replace_product")
    with patch("app.api.routes.projects.Settings") as settings:
        settings.return_value.media_root = __import__("pathlib").Path("E:/工具-商用/data/test-media")
        settings.return_value.openai_api_key = ""
        uploaded = _upload_product(client, project["id"])

    assert uploaded["analysis_status"] == "failed"
    profile = client.get(f"/api/projects/{project['id']}/product-profile").json()
    assert profile["status"] == "failed"
    assert "OPENAI_API_KEY" in profile["error"]


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
        json={"product_profile": "", "visual_direction": "保持原节奏"},
    )

    assert response.status_code == 422
    assert "目标产品" in str(response.json()["detail"])


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
    client.put(f"/api/projects/{project['id']}/product-profile", json={"profile": profile})
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 2}, {"start_sec": 2, "end_sec": 4}]},
    ).json()
    with SessionLocal() as session:
        shots = [session.get(Shot, UUID(item["id"])) for item in timeline["shots"]]
        shots[0].product_interaction = "桌面产品特写"
        shots[1].product_interaction = "拿起盒子饮用"
        session.commit()

    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"product_profile": profile, "visual_direction": "背景改为厨房"},
    )

    assert response.status_code == 201
    text = response.json()["text"]
    assert "将原视频中的产品统一替换为目标产品" in text
    assert text.count("目标产品：纸盒装牛奶") >= 2


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


def test_human_timeline_preserves_overlapping_facts_and_requeues_only_affected_shots() -> None:
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

    with patch("app.api.routes.timeline.validate_dual_vision_configuration"):
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
        assert {str(job.shot_id) for job in queued} == set(body["affected_shot_ids"])


def test_timeline_correction_without_vision_configuration_does_not_create_dead_jobs() -> None:
    client = _client()
    project = _project(client)
    client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 2}, {"start_sec": 2, "end_sec": 4}]},
    ).json()

    with patch("app.api.routes.timeline.validate_dual_vision_configuration", side_effect=__import__("app.services.volcengine_vision", fromlist=["VisionConfigurationError"]).VisionConfigurationError("missing")):
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
