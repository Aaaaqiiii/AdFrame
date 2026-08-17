import json
from types import SimpleNamespace
from uuid import UUID

import pytest

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
