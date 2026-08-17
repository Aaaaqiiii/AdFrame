from app.db.models import Asset, Generation, Job, Project, Shot, TimelineRevision


def test_generation_versions_increment_within_project() -> None:
    project = Project(name="Spring campaign")
    first = Generation(project=project, version=1, prompt_version=1)
    second = Generation(project=project, version=2, prompt_version=1)

    assert [generation.version for generation in project.generations] == [1, 2]
    assert first.prompt_version == second.prompt_version == 1


def test_timeline_revisions_keep_prior_human_correction() -> None:
    project = Project(name="Spring campaign")
    initial = TimelineRevision(project=project, version=1, source="ai")
    Shot(timeline_revision=initial, position=1, start_sec=0, end_sec=3)
    corrected = TimelineRevision(project=project, version=2, source="human")
    Shot(timeline_revision=corrected, position=1, start_sec=0, end_sec=3.36)

    assert initial.shots[0].end_sec == 3
    assert corrected.shots[0].end_sec == 3.36


def test_asset_and_pending_job_belong_to_project() -> None:
    project = Project(name="Spring campaign")
    asset = Asset(project=project, kind="reference_video", original_path=r"D:\AdFlow\media\ad.mp4")
    job = Job(project=project, kind="extract_media", status="pending")

    assert project.assets == [asset]
    assert project.jobs == [job]


def test_generation_has_recovery_snapshot_fields() -> None:
    columns = Generation.__table__.columns
    assert {"request_snapshot", "reference_asset_ids", "provider_response_summary", "submission_fingerprint", "completed_at"} <= set(columns.keys())
    assert {tuple(index.columns.keys()) for index in Generation.__table__.indexes} >= {
        ("project_id", "status"),
        ("status", "next_attempt_at"),
        ("submission_fingerprint",),
    }


def test_shot_fact_fields_keep_observations_separate_from_inferences() -> None:
    project = Project(name="事实时间轴")
    revision = TimelineRevision(project=project, version=1, source="ai")
    shot = Shot(
        timeline_revision=revision,
        position=0,
        start_sec=0,
        end_sec=3,
        people="一只手",
        action="拿起瓶子",
        product_interaction="手持原商品",
        observations="画面中出现手与瓶子",
        inferences="可能是展示动作",
        uncertainties="瓶身文字不可辨认",
    )

    assert shot.observations != shot.inferences
    assert shot.uncertainties == "瓶身文字不可辨认"
