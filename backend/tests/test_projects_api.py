from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from unittest.mock import patch
from uuid import UUID, uuid4

from app.core.config import Settings
from app.db.models import Asset, Job, PromptRevision, Shot, ShotEdit, TimelineRevision
from app.db.session import SessionLocal
from app.main import create_app
from app.services.media import VideoMetadata
from app.services.final_prompt import execute_final_prompt_job



def _accepted_video_upload(client: TestClient, project_id: str, name: str = "reference.mp4", content: bytes = b"video-bytes"):
    with patch("app.api.routes.projects.probe_video", return_value=VideoMetadata(8, 1280, 720, 30)):
        return client.post(
            f"/api/projects/{project_id}/reference-video",
            files={"file": (name, content, "video/mp4")},
        )


def _ready_generation_project(client: TestClient, name: str = "ready") -> dict:
    """旧单 Generation 测试的历史兼容项目：分段方案 + 数据库直插历史分段提示词。"""
    project = client.post("/api/projects", json={"name": name}).json()
    _accepted_video_upload(client, project["id"])
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 8}]},
    ).json()
    shot_id = timeline["shots"][0]["id"]
    client.put(
        f"/api/projects/{project['id']}/shots/{shot_id}/edit",
        json={"action": "展示产品", "confirmed": True},
    )
    plan = client.post(f"/api/projects/{project['id']}/generation-segments/auto")
    assert plan.status_code == 201
    segment_id = plan.json()["segments"][0]["id"]
    # 历史兼容：直接插入 reference_video_edit 提示词，不经过新提示词 POST（新契约已拒绝分段提示词）。
    with SessionLocal() as session:
        revision = session.scalar(select(TimelineRevision).where(TimelineRevision.project_id == UUID(project["id"])))
        prompt = PromptRevision(
            project_id=UUID(project["id"]),
            version=1,
            prompt_mode="reference_video_edit",
            generation_segment_id=UUID(segment_id),
            source_timeline_revision_id=revision.id,
            text=(
                "全局规则：保持原产品不变，禁止替换、删除或重新设计原产品。\n\n"
                "00:00.00–00:08.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"
            ),
            status="completed",
        )
        session.add(prompt)
        session.commit()
        prompt_version = prompt.version
    return {"id": project["id"], "segment_id": segment_id, "prompt_version": prompt_version}


def test_create_reference_adaptation_project() -> None:
    client = TestClient(create_app())

    response = client.post("/api/projects", json={"name": "春日精华广告"})

    assert response.status_code == 201
    assert response.json()["name"] == "春日精华广告"


def test_project_list_returns_most_recent_projects_first() -> None:
    client = TestClient(create_app())
    first = client.post("/api/projects", json={"name": "first"}).json()
    second = client.post("/api/projects", json={"name": "second"}).json()
    _accepted_video_upload(client, first["id"], "first.mp4", b"x" * 200_000)
    _accepted_video_upload(client, second["id"], "second.mp4", b"x" * 200_000)

    response = client.get("/api/projects")

    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert names.index(second["name"]) < names.index(first["name"])
    assert response.json()[names.index(second["name"])]["reference_video_name"] == "second.mp4"


def test_project_list_can_be_filtered_to_page_one() -> None:
    client = TestClient(create_app())
    page_one = client.post("/api/projects", json={"name": "page one", "mode": "preserve_product"}).json()
    page_two = client.post("/api/projects", json={"name": "page two", "mode": "replace_product"}).json()
    _accepted_video_upload(client, page_one["id"], content=b"x" * 200_000)
    _accepted_video_upload(client, page_two["id"], content=b"x" * 200_000)

    response = client.get("/api/projects?mode=preserve_product")

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}
    assert page_one["id"] in ids
    assert page_two["id"] not in ids
    assert client.get("/api/projects?mode=invalid").status_code == 422


def test_upload_reference_video_creates_asset_and_analysis_job() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "上传测试"}).json()

    with patch("app.api.routes.projects.probe_video", side_effect=ValueError("invalid video")):
        response = client.post(
            f"/api/projects/{project['id']}/reference-video",
            files={"file": ("reference.mp4", b"test-video", "video/mp4")},
        )

    assert response.status_code == 422
    assert "视频文件无法解码" in response.json()["detail"]


def test_project_details_restore_uploaded_video_name() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "恢复项目"}).json()
    _accepted_video_upload(client, project["id"], "my-reference.mp4")

    response = client.get(f"/api/projects/{project['id']}")

    assert response.status_code == 200
    assert response.json()["reference_video_name"] == "my-reference.mp4"
    assert response.json()["reference_video_url"].endswith(f"/api/projects/{project['id']}/reference-video/content")


def test_uploaded_reference_video_can_be_streamed_for_preview() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "视频预览"}).json()
    _accepted_video_upload(client, project["id"], "preview.mp4")

    response = client.get(f"/api/projects/{project['id']}/reference-video/content")

    assert response.status_code == 200
    assert response.content == b"video-bytes"
    assert response.headers["content-type"].startswith("video/mp4")


def test_upload_rejects_unsafe_or_unsupported_video_names() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "安全上传"}).json()

    traversal = client.post(
        f"/api/projects/{project['id']}/reference-video",
        files={"file": ("../outside.mp4", b"bytes", "video/mp4")},
    )
    unsupported = client.post(
        f"/api/projects/{project['id']}/reference-video",
        files={"file": ("reference.exe", b"bytes", "video/mp4")},
    )

    assert traversal.status_code == 422
    assert unsupported.status_code == 422


def test_reference_image_upload_requires_consent_and_is_restored() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "image reference"}).json()

    without_consent = client.post(f"/api/projects/{project['id']}/reference-images/person", files={"file": ("person.jpg", b"image", "image/jpeg")})
    with_consent = client.post(f"/api/projects/{project['id']}/reference-images/person?consent=true", files={"file": ("person.jpg", b"image", "image/jpeg")})

    assert without_consent.status_code == 422
    assert with_consent.status_code == 202
    assert client.get(f"/api/projects/{project['id']}").json()["person_reference_image_name"] == "person.jpg"


def test_product_reference_image_can_be_uploaded_and_is_restored() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "product reference"}).json()
    response = client.post(f"/api/projects/{project['id']}/reference-images/product", files={"file": ("product.png", b"image", "image/png")})

    assert response.status_code == 202
    assert response.json()["kind"] == "product_reference_image"
    assert client.get(f"/api/projects/{project['id']}").json()["product_reference_image_name"] == "product.png"


def test_multiple_target_product_images_are_restored_and_combined() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "multiple product views"}).json()
    for name in ("front.png", "side.png"):
        response = client.post(
            f"/api/projects/{project['id']}/reference-images/target_product",
            files={"file": (name, name.encode(), "image/png")},
        )
        assert response.status_code == 202

    with SessionLocal() as session:
        assets = session.scalars(select(Asset).where(
            Asset.project_id == UUID(project["id"]),
            Asset.kind == "target_product_reference_image",
        )).all()
        for asset in assets:
            asset.profile_text = f"{asset.original_filename} 可见事实"
            asset.analysis_status = "succeeded"
        session.commit()

    details = client.get(f"/api/projects/{project['id']}").json()
    assert {item["filename"] for item in details["target_product_reference_images"]} == {"front.png", "side.png"}
    assert "front.png 可见事实" in details["target_product_profile"]
    assert "side.png 可见事实" in details["target_product_profile"]
    assert details["target_product_analysis_status"] == "succeeded"
    for item in details["target_product_reference_images"]:
        assert client.get(item["image_url"]).status_code == 200


def test_project_details_restore_latest_human_timeline() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "恢复时间轴"}).json()
    saved = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}, {"start_sec": 3, "end_sec": 8}]},
    ).json()

    response = client.get(f"/api/projects/{project['id']}")

    assert response.status_code == 200
    assert response.json()["timeline"]["revision_id"] == saved["revision_id"]
    assert len(response.json()["timeline"]["shots"]) == 2
    assert response.json()["timeline"]["shots"][0]["observations"] is None


def test_prompt_history_filters_to_completed_current_timeline() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "prompt history"}).json()
    timeline_v1 = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 2}]},
    ).json()
    with SessionLocal() as session:
        session.add(PromptRevision(
            project_id=UUID(project["id"]), version=1, text="v1 completed",
            visual_direction="v1", source_timeline_revision_id=UUID(timeline_v1["revision_id"]),
            status="completed",
        ))
        session.commit()
    timeline_v2 = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}]},
    ).json()
    with SessionLocal() as session:
        session.add_all([
            PromptRevision(
                project_id=UUID(project["id"]), version=2, text="source snapshot",
                visual_direction="queued", source_timeline_revision_id=UUID(timeline_v2["revision_id"]),
                status="queued",
            ),
            PromptRevision(
                project_id=UUID(project["id"]), version=3, text="v3 completed",
                visual_direction="v3", source_timeline_revision_id=UUID(timeline_v2["revision_id"]),
                status="completed",
            ),
            PromptRevision(
                project_id=UUID(project["id"]), version=4, text="",
                visual_direction="empty", source_timeline_revision_id=UUID(timeline_v2["revision_id"]),
                status="completed",
            ),
        ])
        session.commit()

    response = client.get(
        f"/api/projects/{project['id']}/prompts?current_timeline_only=true&status=completed"
    )
    assert response.status_code == 200
    assert [item["version"] for item in response.json()] == [3]
    assert response.json()[0]["source_timeline_revision_id"] == timeline_v2["revision_id"]
    assert set(response.json()[0]) == {
        "id", "version", "text", "status", "source_timeline_revision_id",
        "prompt_mode", "generation_segment_id",
        "replace_product", "replace_person", "created_at",
    }
    assert response.json()[0]["prompt_mode"] == "full_video_description"

    all_revisions = client.get(f"/api/projects/{project['id']}/prompts")
    assert all_revisions.status_code == 200
    assert [item["version"] for item in all_revisions.json()] == [4, 3, 2, 1]


def test_project_details_ignore_newer_unfinished_prompt_revision() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "prompt recovery"}).json()
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 2}]},
    ).json()
    with SessionLocal() as session:
        session.add_all([
            PromptRevision(
                project_id=UUID(project["id"]), version=1, text="stable completed text",
                visual_direction="stable direction", audio_mode="add_style",
                audio_style="stable audio", replace_product=True, replace_person=True,
                source_timeline_revision_id=UUID(timeline["revision_id"]), status="completed",
            ),
            PromptRevision(
                project_id=UUID(project["id"]), version=2, text="",
                visual_direction="queued direction", audio_mode="keep_original",
                audio_style="queued audio", replace_product=False, replace_person=False,
                source_timeline_revision_id=UUID(timeline["revision_id"]), status="queued",
            ),
        ])
        session.commit()

    details = client.get(f"/api/projects/{project['id']}")
    assert details.status_code == 200
    assert details.json()["latest_prompt_version"] == 1
    assert details.json()["latest_prompt_text"] == "stable completed text"
    assert details.json()["prompt_visual_direction"] == "stable direction"
    assert details.json()["prompt_audio_mode"] == "add_style"
    assert details.json()["prompt_audio_style"] == "stable audio"
    assert details.json()["prompt_replace_product"] is True
    assert details.json()["prompt_replace_person"] is True


def test_prompt_history_rejects_unknown_status_filter() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "prompt status"}).json()

    response = client.get(f"/api/projects/{project['id']}/prompts?status=queued")

    assert response.status_code == 422
    assert "completed" in str(response.json()["detail"])


def test_prompt_history_current_timeline_without_timeline_is_empty() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "prompt no timeline"}).json()

    response = client.get(f"/api/projects/{project['id']}/prompts?current_timeline_only=true")

    assert response.status_code == 200
    assert response.json() == []


def test_prompt_history_missing_project_returns_not_found() -> None:
    client = TestClient(create_app())

    response = client.get(f"/api/projects/{uuid4()}/prompts")

    assert response.status_code == 404


def test_refinement_queues_selected_completed_version_snapshot() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "refinement snapshot"}).json()
    with SessionLocal() as session:
        session.add_all([
            PromptRevision(
                project_id=UUID(project["id"]), version=1,
                text="00:00.00–00:03.00\nfirst source",
                visual_direction="first", status="completed",
            ),
            PromptRevision(
                project_id=UUID(project["id"]), version=2,
                text="00:00.00–00:03.00\nsecond source",
                visual_direction="second", status="completed",
            ),
        ])
        session.commit()

    selected = client.post(
        f"/api/projects/{project['id']}/prompts/refine",
        json={"instruction": "只修改第一版", "source_version": 1},
    )

    assert selected.status_code == 202
    assert selected.json()["status"] == "queued"
    assert selected.json()["text"] == ""
    with SessionLocal() as session:
        queued = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == selected.json()["version"],
        ))
        assert queued is not None
        assert queued.text == "00:00.00–00:03.00\nfirst source"

    latest = client.post(
        f"/api/projects/{project['id']}/prompts/refine",
        json={"instruction": "修改最新版本"},
    )

    assert latest.status_code == 202
    with SessionLocal() as session:
        queued = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == latest.json()["version"],
        ))
        assert queued is not None
        assert queued.text == "00:00.00–00:03.00\nsecond source"
        for job in session.scalars(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "prompt_refinement",
        )):
            job.status = "failed"
        session.commit()


@pytest.mark.parametrize("source_version", [0, 2, 3, 4, 999])
def test_refinement_rejects_unknown_or_unfinished_source_version(source_version: int) -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "invalid refinement source"}).json()
    with SessionLocal() as session:
        session.add_all([
            PromptRevision(
                project_id=UUID(project["id"]), version=1, text="complete source",
                visual_direction="complete", status="completed",
            ),
            PromptRevision(
                project_id=UUID(project["id"]), version=2, text="queued source",
                visual_direction="queued", status="queued",
            ),
            PromptRevision(
                project_id=UUID(project["id"]), version=3, text="failed source",
                visual_direction="failed", status="failed",
            ),
            PromptRevision(
                project_id=UUID(project["id"]), version=4, text="",
                visual_direction="empty", status="completed",
            ),
        ])
        session.commit()

    response = client.post(
        f"/api/projects/{project['id']}/prompts/refine",
        json={"instruction": "修改", "source_version": source_version},
    )

    assert response.status_code == 422


def test_page_one_rejects_product_replacement() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "产品锁定"}).json()

    response = client.post(
        f"/api/projects/{project['id']}/prompt/validate",
        json={"text": "replace the serum with shampoo"},
    )

    assert response.status_code == 422


def test_legacy_full_video_request_uses_configured_frame_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMFLY_API_KEY", "test-comfly-api-key")
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "vision configuration"}).json()
    _accepted_video_upload(client, project["id"])

    response = client.post(f"/api/projects/{project['id']}/analysis/vision")

    # 新流程不会由前端调用此旧入口，但保留它兼容已有项目。
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["job_id"]


def test_shot_vision_request_queues_one_job_per_current_timeline_shot() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "shot vision"}).json()
    _accepted_video_upload(client, project["id"])
    client.put(f"/api/projects/{project['id']}/timeline", json={"shots": [{"start_sec": 0, "end_sec": 3}, {"start_sec": 3, "end_sec": 8}]})

    with patch("app.api.routes.analysis.validate_dual_vision_configuration"):
        response = client.post(f"/api/projects/{project['id']}/analysis/vision-shots")

    assert response.status_code == 202
    assert response.json()["queued_shots"] == 2


def test_shot_edits_are_saved_against_the_current_project() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "shot edits"}).json()
    timeline = client.put(f"/api/projects/{project['id']}/timeline", json={"shots": [{"start_sec": 0, "end_sec": 2}]}).json()

    response = client.put(
        f"/api/projects/{project['id']}/shots/{timeline['shots'][0]['id']}/edit",
        json={"background": "warm bathroom", "keep_unchanged": ["product shape"]},
    )

    assert response.status_code == 200
    assert response.json()["background"] == "warm bathroom"
    assert response.json()["keep_unchanged"] == ["product shape"]


def test_prompt_save_cannot_bypass_product_lock() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "prompt lock"}).json()

    response = client.post(f"/api/projects/{project['id']}/prompts", json={
        "product_profile": "bottle", "visual_direction": "replace the bottle with shampoo",
    })

    assert response.status_code == 422


def test_page_one_product_lock_covers_common_chinese_replacement_phrases() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "Chinese product lock"}).json()

    for instruction in (
        "把原产品变为牛奶盒",
        "将原商品换成牙膏",
        "将瓶子改成软管",
        "把原有包装更换为罐装产品",
    ):
        response = client.post(
            f"/api/projects/{project['id']}/prompt/validate",
            json={"text": instruction},
        )
        assert response.status_code == 422, instruction

    allowed = client.post(
        f"/api/projects/{project['id']}/prompt/validate",
        json={"text": "将背景改为有鲜花的梳妆台，保持原产品不变"},
    )
    assert allowed.status_code == 204


def test_page_one_product_lock_also_validates_each_shot_edit() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "shot product lock"}).json()
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 2}]},
    ).json()

    blocked = client.put(
        f"/api/projects/{project['id']}/shots/{timeline['shots'][0]['id']}/edit",
        json={"action": "把原产品变为牛奶盒"},
    )

    assert blocked.status_code == 422


def test_publish_reference_video_saves_temporary_link() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "publish asset"}).json()
    _accepted_video_upload(client, project["id"])

    class Published:
        url = "https://tempfile.org/test-id/download"
        expires_at = __import__("datetime").datetime(2026, 8, 13, tzinfo=__import__("datetime").UTC)

    with patch("app.api.routes.projects.TempfilePublisher.publish", return_value=Published()):
        response = client.post(f"/api/projects/{project['id']}/reference-video/publish")

    assert response.status_code == 200
    assert response.json()["url"].endswith("/download")


def test_generation_creation_does_not_publish_material_in_http() -> None:
    client = TestClient(create_app())
    project = _ready_generation_project(client, "no publish")
    from app.services.tempfile_publisher import TempfilePublisher
    with patch.object(TempfilePublisher, "publish") as publish:
        response = client.post(
            f"/api/projects/{project['id']}/generations",
            json={"provider": "volcengine", "prompt_version": project["prompt_version"], "generation_segment_id": project["segment_id"]},
        )
    assert response.status_code == 202
    assert not publish.called


def test_generation_rejects_missing_published_material_or_provider_config() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "generation"}).json()

    response = client.post(f"/api/projects/{project['id']}/generations", json={"provider": "volcengine", "prompt_version": 1})

    assert response.status_code == 422


def test_generation_api_queues_work_without_calling_provider() -> None:
    client = TestClient(create_app())
    project = _ready_generation_project(client, "queued generation")

    response = client.post(
        f"/api/projects/{project['id']}/generations",
        json={"provider": "volcengine", "prompt_version": project["prompt_version"], "generation_segment_id": project["segment_id"], "generate_audio": True},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    from app.db.models import Generation
    from app.db.session import SessionLocal
    generation_id = UUID(response.json()["id"])
    with SessionLocal() as session:
        generation = session.get(Generation, generation_id)
        assert generation is not None
        assert (generation.ratio, generation.duration, generation.generate_audio) == ("adaptive", -1, True)


def test_generation_does_not_reject_full_video_over_30_when_segment_legal() -> None:
    """原视频总时长超过 30 秒但提交分段合法时，不应按完整总时长拒绝。"""
    client = TestClient(create_app())
    project = _ready_generation_project(client, "long reference")
    with SessionLocal() as session:
        video = session.scalar(select(Asset).where(Asset.project_id == UUID(project["id"]), Asset.kind == "reference_video"))
        video.duration_sec = 31
        session.commit()

    response = client.post(
        f"/api/projects/{project['id']}/generations",
        json={"provider": "volcengine", "prompt_version": project["prompt_version"], "generation_segment_id": project["segment_id"]},
    )

    # 提交分段 0–8 秒合法，即使原视频 31 秒也允许生成。
    assert response.status_code == 202


def test_generation_queues_person_asset_only_when_requested() -> None:
    client = TestClient(create_app())
    project = _ready_generation_project(client, "image option")
    from app.db.models import Asset
    with SessionLocal() as session:
        session.add(Asset(project_id=UUID(project["id"]), kind="person_reference_image", original_path="C:/person.jpg", original_filename="person.jpg", content_type="image/jpeg", profile_text="已确认人物", profile_user_edited=True, analysis_status="succeeded"))
        session.commit()

    response = client.post(
        f"/api/projects/{project['id']}/generations",
        json={"provider": "volcengine", "prompt_version": project["prompt_version"], "generation_segment_id": project["segment_id"], "include_person_reference": False},
    )
    assert response.status_code == 202
    from app.db.models import Generation
    import json as _json
    with SessionLocal() as session:
        generation = session.get(Generation, UUID(response.json()["id"]))
        assert _json.loads(generation.reference_asset_ids) == [str(session.scalar(select(Asset).where(Asset.project_id == UUID(project["id"]), Asset.kind == "reference_video")).id)]


def _segment_ready_project(client: TestClient, duration_sec: float = 32.0, mode: str = "preserve_product") -> dict:
    """带确认镜头 + 当前分段方案的完整项目，返回各 ID 供提示词接口使用。"""
    project = client.post("/api/projects", json={"name": "segment-prompt", "mode": mode}).json()
    _accepted_video_upload(client, project["id"])
    with SessionLocal() as session:
        video = session.scalar(select(Asset).where(Asset.project_id == UUID(project["id"]), Asset.kind == "reference_video"))
        video.duration_sec = duration_sec
        revision = TimelineRevision(project_id=UUID(project["id"]), version=1, source="human")
        session.add(revision)
        session.flush()
        ranges = [(0.0, 8.0), (8.0, 16.0), (16.0, 24.0), (24.0, 32.0)]
        shot_ids = []
        for index, (start, end) in enumerate(ranges):
            shot = Shot(timeline_revision_id=revision.id, position=index, start_sec=start, end_sec=end, analysis_status="succeeded")
            session.add(shot)
            session.flush()
            session.add(ShotEdit(project_id=UUID(project["id"]), shot_id=shot.id, action="展示产品", confirmed=True))
            shot_ids.append(str(shot.id))
        session.commit()
        revision_id = str(revision.id)
    plan = client.post(f"/api/projects/{project['id']}/generation-segments/auto")
    assert plan.status_code == 201
    segment_id = plan.json()["segments"][0]["id"]
    return {"id": project["id"], "revision_id": revision_id, "segment_id": segment_id, "shot_ids": shot_ids}

    project = _segment_ready_project(client)
    # 新建更新的时间轴，旧分段方案失效。
    with SessionLocal() as session:
        newer = TimelineRevision(project_id=UUID(project["id"]), version=2, source="human")
        session.add(newer)
        session.commit()
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "保持", "use_ai": True, "generation_segment_id": project["segment_id"]},
    )
    assert response.status_code == 422


def test_segment_prompt_rejects_unconfirmed_shot() -> None:
    """完整提示词：存在未确认镜头时拒绝创建。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    with SessionLocal() as session:
        first_shot_edit = session.scalar(select(ShotEdit).where(ShotEdit.project_id == UUID(project["id"])))
        first_shot_edit.confirmed = False
        session.commit()
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "保持", "use_ai": True},
    )
    assert response.status_code == 422

def test_refinement_inherits_segment_identity() -> None:
    """完整提示词精修新版本必须继承 full_reference_video_edit 模式且不绑定分段。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    created = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": _full_prompt_text(project), "use_ai": False},
    )
    assert created.status_code == 201
    v1_version = created.json()["version"]
    refined = client.post(
        f"/api/projects/{project['id']}/prompts/refine",
        json={"instruction": "增强光线", "source_version": v1_version},
    )
    assert refined.status_code == 202
    with SessionLocal() as session:
        new_revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == refined.json()["version"],
        ))
        assert new_revision.prompt_mode == "full_reference_video_edit"
        assert new_revision.generation_segment_id is None


def test_segment_refinement_missing_block_fails_not_completed() -> None:
    """完整提示词精修若删除时间块或栏目，任务必须失败而不能标记 completed。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    v1 = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": _full_prompt_text(project), "use_ai": False},
    )
    assert v1.status_code == 201
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == v1.json()["version"],
        ))
        revision.status = "completed"
        session.commit()
    refined = client.post(
        f"/api/projects/{project['id']}/prompts/refine",
        json={"instruction": "增强光线", "source_version": v1.json()["version"]},
    )
    assert refined.status_code == 202
    with SessionLocal() as session:
        new_revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == refined.json()["version"],
        ))
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "prompt_refinement",
            Job.provider_input_id == str(new_revision.id),
        ))
        # 精修结果删掉了第一个时间块和“禁止”栏目。
        broken = "00:04.00–00:09.50\n保持：b\n修改：无。\n删除：无。"
        from app.services.final_prompt import execute_prompt_refinement_job
        with patch("app.services.final_prompt.refine_prompt", return_value=broken):
            try:
                execute_prompt_refinement_job(session, job, Settings(comfly_api_key="test-comfly-api-key"))
            except ValueError as error:
                assert "不完整" in str(error)
            else:
                assert False, "精修结果缺块/缺栏目时必须失败"
        session.refresh(new_revision)
        assert new_revision.status != "completed"


def test_segment_manual_save_missing_heading_returns_422() -> None:
    """完整提示词人工保存（use_ai=False）缺少栏目时应返回 422，不调用 GPT 修复。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    # 人工保存模式缺“禁止”栏目。
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={
            "visual_direction": (
                "全局规则：原参考视频是时间轴、动作、构图、运镜、节奏和镜头顺序的最高优先级参考。\n"
                "保持原产品不变，禁止替换、删除或重新设计原产品。\n\n"
                "00:00.00–00:04.00\n保持：a\n修改：无。\n删除：无。"
            ),
            "use_ai": False,
        },
    )
    assert response.status_code == 422


def test_product_image_purposes_enter_deterministic_rules() -> None:
    """完整提示词：多张产品图的用途名称必须由服务端确定性写入全局规则。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client, mode="replace_product")
    with SessionLocal() as session:
        # 造两张已确认产品图，带不同用途。
        product_path = __import__("pathlib").Path("C:/p.png")
        for name, view_label in (("正面瓶身", "front"), ("侧面瓶身", "side")):
            session.add(Asset(
                project_id=UUID(project["id"]),
                kind="product_reference_image",
                original_path=str(product_path),
                original_filename=f"{name}.png",
                content_type="image/png",
                profile_text=f"{name} 可见事实",
                profile_json=__import__("json").dumps({"view_label": view_label, "display_name": name, "note": "注意瓶盖结构", "summary_confirmed": True}),
                analysis_status="succeeded",
            ))
        session.commit()
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "只修改产品", "use_ai": True, "replace_product": True},
    )
    assert response.status_code == 202
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == response.json()["version"],
        ))
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "final_prompt_generation",
            Job.provider_input_id == str(revision.id),
        ))
        # 模型只返回时间块正文；前缀由服务端重建并含图片用途。
        model_text = "00:00.00–00:04.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。\n\n00:04.00–00:09.50\n保持：b\n修改：无。\n删除：无。\n禁止：无。"
        with patch("app.services.final_prompt._chat", return_value=model_text):
            execute_final_prompt_job(session, job, Settings(comfly_api_key="test-comfly-api-key"))
        session.refresh(revision)
        assert revision.status == "completed"
        assert "产品参考图用途" in revision.text
        assert "正面瓶身" in revision.text
        assert "侧面瓶身" in revision.text


def test_preserve_mode_has_explicit_original_product_lock_rule() -> None:
    """完整提示词：保留产品模式必须在确定性全局规则中明确锁定原产品。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "只修改光线", "use_ai": True},
    )
    assert response.status_code == 202
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == response.json()["version"],
        ))
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "final_prompt_generation",
            Job.provider_input_id == str(revision.id),
        ))
        model_text = "00:00.00–00:04.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。\n\n00:04.00–00:09.50\n保持：b\n修改：无。\n删除：无。\n禁止：无。"
        with patch("app.services.final_prompt._chat", return_value=model_text):
            execute_final_prompt_job(session, job, Settings(comfly_api_key="test-comfly-api-key"))
        session.refresh(revision)
        assert revision.status == "completed"
        assert "保持原产品不变" in revision.text


def test_preserve_mode_rejects_replacement_in_actionable_modify_column() -> None:
    """完整提示词：保留模式结构完整但“修改”栏目输出产品替换时，任务不能完成。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "只修改光线", "use_ai": True},
    )
    assert response.status_code == 202
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == response.json()["version"],
        ))
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "final_prompt_generation",
            Job.provider_input_id == str(revision.id),
        ))
        # 四栏目齐全，但“修改”栏目写入产品替换。
        malicious = "00:00.00–00:04.00\n保持：a\n修改：将原产品替换为新产品。\n删除：无。\n禁止：无。\n\n00:04.00–00:09.50\n保持：b\n修改：无。\n删除：无。\n禁止：无。"
        from app.services.final_prompt import execute_final_prompt_job
        with patch("app.services.final_prompt._chat", return_value=malicious):
            try:
                execute_final_prompt_job(session, job, Settings(comfly_api_key="test-comfly-api-key"))
            except ValueError as error:
                assert "不能替换产品" in str(error)
            else:
                assert False, "保留模式必须拒绝修改栏目中的产品替换"
        session.refresh(revision)
        assert revision.status != "completed"

def test_segment_manual_save_missing_entire_block_returns_422() -> None:
    """完整提示词人工保存删除整个第二时间块时，必须从数据库预期标签识别并返回 422。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    # 完整文本只保留第一个绝对时间块（缺第二块）。
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={
            "visual_direction": (
                "全局规则：原参考视频是时间轴、动作、构图、运镜、节奏和镜头顺序的最高优先级参考。\n"
                "保持原产品不变，禁止替换、删除或重新设计原产品。\n\n"
                "00:00.00–00:04.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"
            ),
            "use_ai": False,
        },
    )
    assert response.status_code == 422

def test_preserve_segment_manual_save_rejects_replacement_in_modify() -> None:
    """完整提示词保留产品人工版本：修改栏目替换产品必须被拒绝。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={
            "visual_direction": (
                "全局规则：原参考视频是时间轴、动作、构图、运镜、节奏和镜头顺序的最高优先级参考。\n"
                "保持原产品不变，禁止替换、删除或重新设计原产品。\n\n"
                "00:00.00–00:04.00\n保持：a\n修改：将原产品替换为新产品。\n删除：无。\n禁止：无。\n\n"
                "00:04.00–00:09.50\n保持：b\n修改：无。\n删除：无。\n禁止：无。"
            ),
            "use_ai": False,
        },
    )
    assert response.status_code == 422


def test_replace_segment_refinement_restores_deterministic_prefix() -> None:
    """完整提示词精修：GPT 保留源前缀时，最终版本前缀与绝对时间块完整。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client, mode="replace_product")
    v1 = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": _full_prompt_text(project), "use_ai": False},
    )
    assert v1.status_code == 201
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == v1.json()["version"],
        ))
        revision.status = "completed"
        session.commit()
        source_prefix = _full_prompt_text(project).split("00:00.00–00:04.00")[0]
    refined = client.post(
        f"/api/projects/{project['id']}/prompts/refine",
        json={"instruction": "增强光线", "source_version": v1.json()["version"]},
    )
    assert refined.status_code == 202
    with SessionLocal() as session:
        new_revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == refined.json()["version"],
        ))
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "prompt_refinement",
            Job.provider_input_id == str(new_revision.id),
        ))
        from app.services.final_prompt import execute_prompt_refinement_job
        # GPT 返回含源前缀的完整精修文本（legacy 路径直接保留）。
        full = _full_prompt_text(project).replace("镜头二保持正文", "镜头二保持正文，增强光线")
        with patch("app.services.final_prompt.refine_prompt", return_value=full):
            execute_prompt_refinement_job(session, job, Settings(comfly_api_key="test-comfly-api-key"))
        session.refresh(new_revision)
        assert new_revision.status == "completed"
        # 前缀保留、绝对时间块保留。
        assert new_revision.text.startswith(source_prefix)
        assert "00:00.00–00:04.00" in new_revision.text
        assert "00:04.00–00:09.50" in new_revision.text


def test_segment_ai_generation_rejects_product_replacement_in_direction() -> None:
    """保留产品完整提示词 AI 生成带替换改编要求：入口立即 422，且不创建版本或任务。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "把原产品替换成新产品", "use_ai": True},
    )
    assert response.status_code == 422
    with SessionLocal() as session:
        revisions = session.scalars(select(PromptRevision).where(PromptRevision.project_id == UUID(project["id"]))).all()
        jobs = session.scalars(select(Job).where(Job.project_id == UUID(project["id"]), Job.kind == "final_prompt_generation")).all()
        assert revisions == []
        assert jobs == []

def _full_prompt_project(client: TestClient, mode: str = "preserve_product") -> dict:
    """带当前时间轴 + 确认镜头 + 可选已确认目标产品的完整提示词项目。"""
    project = client.post("/api/projects", json={"name": "full-prompt", "mode": mode}).json()
    _accepted_video_upload(client, project["id"])
    with SessionLocal() as session:
        video = session.scalar(select(Asset).where(Asset.project_id == UUID(project["id"]), Asset.kind == "reference_video"))
        video.duration_sec = 9.5
        revision = TimelineRevision(project_id=UUID(project["id"]), version=1, source="human")
        session.add(revision)
        session.flush()
        for index, (start, end) in enumerate([(0.0, 4.0), (4.0, 9.5)]):
            shot = Shot(timeline_revision_id=revision.id, position=index, start_sec=start, end_sec=end, analysis_status="succeeded")
            session.add(shot)
            session.flush()
            session.add(ShotEdit(project_id=UUID(project["id"]), shot_id=shot.id, action="展示产品", confirmed=True))
        if mode == "replace_product":
            session.add(Asset(
                project_id=UUID(project["id"]), kind="product_reference_image",
                original_path="C:/p.png", original_filename="正面.png", content_type="image/png",
                profile_text="核心卖点：轻薄、不黏腻",
                profile_json=__import__("json").dumps({"view_label": "front", "display_name": "正面", "note": "注意瓶盖", "summary_confirmed": True}),
                analysis_status="succeeded",
            ))
        session.commit()
        revision_id = str(revision.id)
    return {"id": project["id"], "revision_id": revision_id, "mode": mode}


def _full_prompt_text(project: dict) -> str:
    prefix = "全局规则：原参考视频是时间轴、动作、构图、运镜、节奏和镜头顺序的最高优先级参考。\n"
    if project["mode"] == "replace_product":
        prefix += "目标产品必须匹配已确认产品档案：核心卖点：轻薄、不黏腻\n产品参考图用途：\n- 正面：锁定该角度结构（注意瓶盖）\n"
    else:
        prefix += "保持原产品不变，禁止替换、删除或重新设计原产品。\n"
    return (
        prefix + "\n"
        "00:00.00–00:04.00\n保持：人物身份、动作节奏、手部位置、背景、构图、镜头运动不变。\n修改：无。\n删除：无。\n禁止：不得新增文字或改变动作。\n\n"
        "00:04.00–00:09.50\n保持：镜头二保持正文。\n修改：无。\n删除：无。\n禁止：不得新增文字。"
    )


def test_full_prompt_ai_creation_queues_full_reference_video_edit() -> None:
    """无 generation_segment_id 的 AI 请求创建 full_reference_video_edit 版本。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={
            "product_profile": "核心卖点：轻薄、不黏腻",
            "visual_direction": "只修改产品，其他画面保持",
            "audio_mode": "keep_original",
            "audio_style": "",
            "replace_product": False,
            "replace_person": False,
            "use_ai": True,
        },
    )
    assert response.status_code == 202
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == response.json()["version"],
        ))
        assert revision is not None
        assert revision.prompt_mode == "full_reference_video_edit"
        assert revision.generation_segment_id is None
        assert revision.text == ""


def test_full_prompt_manual_save_accepts_all_blocks_and_prefix() -> None:
    """人工保存含全部绝对时间块和确定性前缀 → 201/completed。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": _full_prompt_text(project), "use_ai": False},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == response.json()["version"],
        ))
        assert revision.prompt_mode == "full_reference_video_edit"
        assert revision.generation_segment_id is None
        assert revision.text == _full_prompt_text(project)


@pytest.mark.parametrize("mutate", [
    lambda text: text.replace("00:04.00–00:09.50", ""),  # 删掉整个第二块
    lambda text: text.replace("删除：无", ""),  # 删栏目
    lambda text: text.replace("目标产品必须匹配已确认产品档案：核心卖点：轻薄、不黏腻", ""),  # 删产品档案
    lambda text: text.replace("产品参考图用途", "参考资料"),  # 删用途规则
])
def test_full_prompt_manual_save_rejects_incomplete(mutate) -> None:
    client = TestClient(create_app())
    project = _full_prompt_project(client, mode="replace_product")
    text = mutate(_full_prompt_text(project))
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": text, "use_ai": False},
    )
    assert response.status_code == 422
    with SessionLocal() as session:
        revisions = session.scalars(select(PromptRevision).where(PromptRevision.project_id == UUID(project["id"]))).all()
        assert revisions == []


def test_full_prompt_legacy_segment_revisions_remain_readable() -> None:
    """历史 reference_video_edit 分段提示词保持可读、不被改写。"""
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    # 先建一个分段提示词作为历史记录。
    created = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": _full_prompt_text(project), "use_ai": False},
    )
    assert created.status_code == 201
    with SessionLocal() as session:
        old = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == created.json()["version"],
        ))
        old.prompt_mode = "reference_video_edit"
        old.generation_segment_id = None
        old.text = "00:00.00–00:08.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"
        session.commit()
        old_id = old.id
    # 历史记录仍可读取。
    history = client.get(f"/api/projects/{project['id']}/prompts")
    assert any(item["id"] == str(old_id) for item in history.json())
    # 不创建新 reference_video_edit。
    with SessionLocal() as session:
        revision = session.get(PromptRevision, old_id)
        assert revision.prompt_mode == "reference_video_edit"
        assert revision.text == "00:00.00–00:08.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"


def test_full_prompt_worker_repairs_missing_column_per_block() -> None:
    """完整提示词 worker：第一块缺栏目时逐块定向补全，最终版本 completed。"""
    from app.services.final_prompt import execute_final_prompt_job
    client = TestClient(create_app())
    project = _full_prompt_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "保持节奏", "use_ai": True},
    )
    assert response.status_code == 202
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"]),
            PromptRevision.version == response.json()["version"],
        ))
        job = session.scalar(select(Job).where(
            Job.project_id == UUID(project["id"]),
            Job.kind == "final_prompt_generation",
            Job.provider_input_id == str(revision.id),
        ))
        calls = {"n": 0}
        def fake_chat(settings, messages, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                # 第一块缺“禁止”栏目；第二块完整。
                return (
                    "00:00.00–00:04.00\n保持：a\n修改：无。\n删除：无。\n"
                    "00:04.00–00:09.50\n保持：b\n修改：无。\n删除：无。\n禁止：无。"
                )
            # 补全第一块。
            return "00:00.00–00:04.00\n保持：a\n修改：无。\n删除：无。\n禁止：无。"
        with patch("app.services.final_prompt._chat", side_effect=fake_chat):
            execute_final_prompt_job(session, job, Settings(comfly_api_key="test-comfly-api-key"))
        session.refresh(revision)
        assert revision.status == "completed"
        assert "00:00.00–00:04.00" in revision.text
        assert "00:04.00–00:09.50" in revision.text


def test_full_prompt_ai_creation_rejects_missing_timeline() -> None:
    """完整提示词 AI 生成：无时间轴必须立即 422，不创建 queued Job。"""
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "no-timeline", "mode": "preserve_product"}).json()
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "保持节奏", "use_ai": True},
    )
    assert response.status_code == 422
    with SessionLocal() as session:
        jobs = session.scalars(select(Job).where(Job.project_id == UUID(project["id"]), Job.kind == "final_prompt_generation")).all()
        assert jobs == []


def test_full_prompt_ai_creation_rejects_unconfirmed_shot() -> None:
    """完整提示词 AI 生成：镜头未确认必须 422（不创建 queued Job）。"""
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "no-confirm", "mode": "preserve_product"}).json()
    _accepted_video_upload(client, project["id"])
    client.put(f"/api/projects/{project['id']}/timeline", json={"shots": [{"start_sec": 0, "end_sec": 3}]})
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "保持节奏", "use_ai": True},
    )
    assert response.status_code == 422
    with SessionLocal() as session:
        jobs = session.scalars(select(Job).where(Job.project_id == UUID(project["id"]), Job.kind == "final_prompt_generation")).all()
        assert jobs == []
