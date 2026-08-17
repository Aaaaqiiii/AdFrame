import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select

from app.db.models import Asset, Generation, PromptRevision, Shot, ShotEdit, TimelineRevision
from app.db.session import SessionLocal
from app.services.tempfile_publisher import TempfilePublisher


@pytest.fixture(autouse=True)
def configured_generation_provider(monkeypatch):
    monkeypatch.setenv("VOLCENGINE_API_KEY", "test-key")


def _ready_project(client, tmp_path, mode="preserve_product", replace_person=False):
    project_body = client.post(
        "/api/projects", json={"name": f"ready-{mode}", "mode": mode}
    ).json()
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
            duration_sec=8,
        )
        revision = TimelineRevision(project_id=project_id, version=1, source="human")
        session.add_all([video, revision])
        session.flush()
        shot = Shot(timeline_revision_id=revision.id, position=0, start_sec=0, end_sec=8, analysis_status="succeeded")
        session.add(shot)
        session.flush()
        session.add(ShotEdit(project_id=project_id, shot_id=shot.id, action="展示产品", confirmed=True))
        prompt = PromptRevision(
            project_id=project_id,
            version=1,
            text="保持当前镜头和产品",
            replace_product=mode == "replace_product",
            replace_person=replace_person,
            source_timeline_revision_id=revision.id,
            status="completed",
        )
        session.add(prompt)
        if mode == "replace_product":
            product_path = tmp_path / "product.png"
            product_path.write_bytes(b"product")
            session.add(Asset(
                project_id=project_id,
                kind="product_reference_image",
                original_path=str(product_path),
                original_filename="product.png",
                content_type="image/png",
                profile_text="已确认目标产品",
                profile_json='{"summary_confirmed": true}',
                profile_user_edited=True,
                analysis_status="succeeded",
            ))
        if replace_person:
            person_path = tmp_path / "person.png"
            person_path.write_bytes(b"person")
            session.add(Asset(
                project_id=project_id,
                kind="person_reference_image",
                original_path=str(person_path),
                original_filename="person.png",
                content_type="image/png",
                profile_text="已确认人物",
                profile_user_edited=True,
                analysis_status="succeeded",
            ))
        session.commit()
        return SimpleNamespace(
            project_id=project_id,
            video_asset_id=video.id,
            prompt_version=prompt.version,
            generations_url=f"/api/projects/{project_id}/generations",
            payload={
                "provider": "volcengine",
                "prompt_version": prompt.version,
                "generate_audio": False,
                "include_person_reference": replace_person,
                "include_background_reference": False,
            },
        )


def test_generation_list_is_version_descending_and_builds_local_url(client, generation_factory, tmp_path) -> None:
    old = generation_factory(version=1, status="failed")
    local_file = tmp_path / "v2.mp4"
    local_file.write_bytes(b"video")
    latest = generation_factory(version=2, status="completed", result_path=str(local_file), project_id=old.project_id)
    response = client.get(f"/api/projects/{old.project_id}/generations")
    assert response.status_code == 200
    assert [item["version"] for item in response.json()] == [2, 1]
    assert response.json()[0]["local_video_url"].endswith(f"/{latest.id}/content")


def test_generation_detail_builds_local_url_instead_of_returning_raw_orm(client, generation_factory, tmp_path) -> None:
    local_file = tmp_path / "v1.mp4"
    local_file.write_bytes(b"video")
    generation = generation_factory(status="completed", result_path=str(local_file))
    body = client.get(f"/api/projects/{generation.project_id}/generations/{generation.id}").json()
    assert body["local_video_url"].endswith(f"/{generation.id}/content")


def test_create_rejects_client_ratio_and_duration(client, tmp_path) -> None:
    ready_project = _ready_project(client, tmp_path)
    response = client.post(ready_project.generations_url, json={
        "provider": "volcengine", "prompt_version": 1,
        "ratio": "16:9", "duration": 8,
        "generate_audio": True,
        "include_person_reference": False,
        "include_background_reference": False,
    })
    assert response.status_code == 422


def test_create_only_queues_and_stores_asset_ids(client, tmp_path, monkeypatch) -> None:
    ready_project = _ready_project(client, tmp_path)
    monkeypatch.setattr(TempfilePublisher, "publish", lambda *_: (_ for _ in ()).throw(AssertionError("published in HTTP")))
    response = client.post(ready_project.generations_url, json=ready_project.payload)
    assert response.status_code == 202
    with SessionLocal() as session:
        generation = session.get(Generation, UUID(response.json()["id"]))
        assert generation.status == "queued"
        assert json.loads(generation.reference_asset_ids)[0] == str(ready_project.video_asset_id)
        assert generation.reference_image_urls is None
        assert generation.ratio == "adaptive"
        assert generation.duration == -1


def test_create_rejects_old_timeline_prompt(client, tmp_path) -> None:
    ready_project = _ready_project(client, tmp_path)
    with SessionLocal() as session:
        revision = session.scalar(
            select(TimelineRevision).where(TimelineRevision.project_id == ready_project.project_id).order_by(TimelineRevision.version.desc())
        )
        prompt = session.scalar(
            select(PromptRevision).where(PromptRevision.project_id == ready_project.project_id, PromptRevision.status == "completed").order_by(PromptRevision.version.desc())
        )
        # Create a second (newer) timeline revision the prompt does not reference.
        newer = TimelineRevision(project_id=ready_project.project_id, version=revision.version + 1, source="human")
        session.add(newer)
        session.commit()
        prompt.source_timeline_revision_id = revision.id
        session.commit()
    response = client.post(ready_project.generations_url, json={**ready_project.payload, "prompt_version": prompt.version})
    assert response.status_code == 422


def test_create_rejects_non_completed_prompt(client, tmp_path) -> None:
    ready_project = _ready_project(client, tmp_path)
    with SessionLocal() as session:
        prompt = session.scalar(
            select(PromptRevision).where(PromptRevision.project_id == ready_project.project_id)
        )
        prompt.status = "queued"
        prompt.text = ""
        session.commit()
    response = client.post(ready_project.generations_url, json=ready_project.payload)
    assert response.status_code == 422


def test_create_rejects_unconfirmed_shot(client, tmp_path) -> None:
    ready_project = _ready_project(client, tmp_path)
    with SessionLocal() as session:
        edit = session.scalar(
            select(ShotEdit).where(ShotEdit.project_id == ready_project.project_id)
        )
        edit.confirmed = False
        session.commit()
    response = client.post(ready_project.generations_url, json=ready_project.payload)
    assert response.status_code == 422


def test_create_rejects_missing_provider_key(client, tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace as _NS
    fake_settings = _NS(volcengine_api_key="", volcengine_seedance_base_url="https://x", volcengine_seedance_task_path="/t", comfly_api_key="", comfly_base_url="https://y", comfly_seedance_task_path="/t")
    monkeypatch.setattr("app.api.routes.generations.Settings", lambda: fake_settings)
    ready_project = _ready_project(client, tmp_path)
    response = client.post(ready_project.generations_url, json=ready_project.payload)
    assert response.status_code == 409


def test_create_rejects_reference_video_longer_than_30_seconds(client, tmp_path) -> None:
    ready_project = _ready_project(client, tmp_path)
    with SessionLocal() as session:
        video = session.scalar(
            select(Asset).where(Asset.project_id == ready_project.project_id, Asset.kind == "reference_video")
        )
        video.duration_sec = 31
        session.commit()
    response = client.post(ready_project.generations_url, json=ready_project.payload)
    assert response.status_code == 422


def test_create_duplicate_fingerprint_returns_conflict(client, tmp_path) -> None:
    ready_project = _ready_project(client, tmp_path)
    first = client.post(ready_project.generations_url, json=ready_project.payload)
    assert first.status_code == 202
    duplicate = client.post(ready_project.generations_url, json=ready_project.payload)
    assert duplicate.status_code == 409


def test_create_replace_mode_includes_confirmed_product_images(client, tmp_path) -> None:
    ready_project = _ready_project(client, tmp_path, mode="replace_product")
    response = client.post(ready_project.generations_url, json=ready_project.payload)
    assert response.status_code == 202
    with SessionLocal() as session:
        generation = session.get(Generation, UUID(response.json()["id"]))
        asset_ids = json.loads(generation.reference_asset_ids)
        assert len(asset_ids) == 2  # video + confirmed product
        assert asset_ids[0] == str(ready_project.video_asset_id)


def test_retry_creates_new_version_without_mutating_failed_row(client, failed_generation) -> None:
    response = client.post(f"/api/projects/{failed_generation.project_id}/generations/{failed_generation.id}/retry")
    assert response.status_code == 202
    assert response.json()["version"] == failed_generation.version + 1
    assert response.json()["status"] == "queued"
    with SessionLocal() as session:
        original = session.get(Generation, failed_generation.id)
        assert original.status == "failed"


def test_retry_rejects_active_and_completed_states(client, generation_factory, queued_generation, processing_generation, uncertain_generation) -> None:
    for generation in (queued_generation, processing_generation, uncertain_generation):
        response = client.post(f"/api/projects/{generation.project_id}/generations/{generation.id}/retry")
        assert response.status_code == 422
    completed = generation_factory(status="completed")
    response = client.post(f"/api/projects/{completed.project_id}/generations/{completed.id}/retry")
    assert response.status_code == 422


def test_uncertain_task_requires_explicit_resolution(client, uncertain_generation) -> None:
    attach = client.post(f"/api/projects/{uncertain_generation.project_id}/generations/{uncertain_generation.id}/resolve", json={
        "action": "attach_task", "external_task_id": "provider-123"
    })
    assert attach.status_code == 200
    assert attach.json()["status"] == "processing"
    with SessionLocal() as session:
        resolved = session.get(Generation, uncertain_generation.id)
        assert resolved.external_task_id == "provider-123"
        assert resolved.next_attempt_at is None
        assert resolved.leased_at is None


def test_uncertain_confirm_not_created_sets_failed(client, uncertain_generation) -> None:
    response = client.post(f"/api/projects/{uncertain_generation.project_id}/generations/{uncertain_generation.id}/resolve", json={
        "action": "confirm_not_created"
    })
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    with SessionLocal() as session:
        resolved = session.get(Generation, uncertain_generation.id)
        assert resolved.error_message == "用户确认供应商未创建任务"


def test_resolve_rejects_non_uncertain_states(client, failed_generation, queued_generation) -> None:
    for generation in (failed_generation, queued_generation):
        response = client.post(f"/api/projects/{generation.project_id}/generations/{generation.id}/resolve", json={
            "action": "confirm_not_created"
        })
        assert response.status_code == 422


def test_attach_task_requires_nonblank_id(client, uncertain_generation) -> None:
    response = client.post(f"/api/projects/{uncertain_generation.project_id}/generations/{uncertain_generation.id}/resolve", json={
        "action": "attach_task", "external_task_id": "  "
    })
    assert response.status_code == 422
