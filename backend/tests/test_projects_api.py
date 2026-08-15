from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from unittest.mock import patch
from uuid import UUID, uuid4

from app.db.models import Asset, Job, PromptRevision
from app.db.session import SessionLocal
from app.main import create_app
from app.services.media import VideoMetadata



def _accepted_video_upload(client: TestClient, project_id: str, name: str = "reference.mp4", content: bytes = b"video-bytes"):
    with patch("app.api.routes.projects.probe_video", return_value=VideoMetadata(8, 1280, 720, 30)):
        return client.post(
            f"/api/projects/{project_id}/reference-video",
            files={"file": (name, content, "video/mp4")},
        )


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
        "replace_product", "replace_person", "created_at",
    }

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


def test_expired_reference_video_link_is_republished_before_generation() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "expired material"}).json()
    _accepted_video_upload(client, project["id"])
    client.post(f"/api/projects/{project['id']}/prompts", json={"product_profile": "bottle", "visual_direction": "keep lighting"})

    with patch("app.api.routes.generations._provider_gateway"), patch("app.api.routes.generations.TempfilePublisher.publish") as publish:
        publish.return_value = type("Published", (), {"url": "https://tempfile.org/new/download", "expires_at": __import__("datetime").datetime(2026, 8, 13, tzinfo=__import__("datetime").UTC)})()
        from app.db.models import Asset
        from app.db.session import SessionLocal
        with SessionLocal() as session:
            asset = session.query(Asset).filter_by(project_id=UUID(project["id"]), kind="reference_video").first()
            asset.public_url, asset.public_url_expires_at = "https://tempfile.org/old/download", "2020-01-01T00:00:00+00:00"
            session.commit()
        response = client.post(f"/api/projects/{project['id']}/generations", json={"provider": "volcengine", "prompt_version": 1})

    assert response.status_code == 202
    assert publish.called


def test_generation_rejects_missing_published_material_or_provider_config() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "generation"}).json()

    response = client.post(f"/api/projects/{project['id']}/generations", json={"provider": "volcengine", "prompt_version": 1})

    assert response.status_code == 422


def test_generation_api_queues_work_without_calling_provider() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "queued generation"}).json()
    _accepted_video_upload(client, project["id"])
    client.post(f"/api/projects/{project['id']}/prompts", json={"product_profile": "bottle", "visual_direction": "keep lighting"})

    with patch("app.api.routes.generations._provider_gateway"):
        from app.db.session import SessionLocal
        from app.db.models import Asset
        with SessionLocal() as session:
            asset = session.query(Asset).filter_by(project_id=UUID(project["id"]), kind="reference_video").first()
            asset.public_url = "https://tempfile.org/test/download"
            session.commit()
        response = client.post(
            f"/api/projects/{project['id']}/generations",
            json={"provider": "volcengine", "prompt_version": 1, "ratio": "16:9", "duration": 8, "generate_audio": True},
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


def test_generation_blocks_reference_video_longer_than_30_seconds() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "long reference"}).json()
    with patch("app.api.routes.projects.probe_video", return_value=VideoMetadata(31, 1280, 720, 30)):
        uploaded = client.post(
            f"/api/projects/{project['id']}/reference-video",
            files={"file": ("long.mp4", b"video-bytes", "video/mp4")},
        )
    assert uploaded.status_code == 202
    prompt = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"product_profile": "原产品瓶身和 Logo", "visual_direction": "只修改背景"},
    )
    assert prompt.status_code == 201

    with patch("app.api.routes.generations._provider_gateway"):
        response = client.post(
            f"/api/projects/{project['id']}/generations",
            json={"provider": "volcengine", "prompt_version": prompt.json()["version"]},
        )

    assert response.status_code == 422
    assert "30" in response.json()["detail"]


def test_generation_saves_reference_image_urls_only_when_requested() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "image option"}).json()
    _accepted_video_upload(client, project["id"])
    client.post(f"/api/projects/{project['id']}/prompts", json={"product_profile": "bottle", "visual_direction": "keep lighting"})

    with patch("app.api.routes.generations._provider_gateway"), patch("app.api.routes.generations.TempfilePublisher.publish") as publish:
        publish.return_value = type("Published", (), {"url": "https://tempfile.org/image/download", "expires_at": __import__("datetime").datetime(2026, 8, 13, tzinfo=__import__("datetime").UTC)})()
        from app.db.models import Asset
        from app.db.session import SessionLocal
        with SessionLocal() as session:
            video = session.query(Asset).filter_by(project_id=UUID(project["id"]), kind="reference_video").first()
            video.public_url = "https://tempfile.org/video/download"
            session.add(Asset(project_id=UUID(project["id"]), kind="person_reference_image", original_path="C:/person.jpg", original_filename="person.jpg", content_type="image/jpeg"))
            session.commit()
        response = client.post(f"/api/projects/{project['id']}/generations", json={"provider": "volcengine", "prompt_version": 1, "include_person_reference": False})

    from app.db.models import Generation
    with SessionLocal() as session:
        generation = session.get(Generation, UUID(response.json()["id"]))
        assert generation.reference_image_urls == "[]"
