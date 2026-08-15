from pathlib import Path
from uuid import uuid4

from app.db.models import Asset, Job, Shot
from app.db.models import ShotEvidence, TimelineRevision
from app.db.session import SessionLocal
from app.services.analysis_persistence import persist_vision_result
from app.services.vision import AnalysisResult, AnalyzedShot
from app.services.volcengine_vision import VisionTaskResult
from app.services.vision_jobs import execute_vision_job


class FakeGateway:
    def upload_video(self, source: Path) -> str:
        return "vid-1"

    def submit_vision(self, vid: str) -> str:
        assert vid == "vid-1"
        return "task-1"

    def get_result(self, task_id: str) -> VisionTaskResult:
        assert task_id == "task-1"
        return VisionTaskResult(task_id, "Processing")


class FakeSession:
    def __init__(self, asset: Asset):
        self.asset = asset

    def scalar(self, _query):
        return self.asset

    def commit(self):
        pass


def test_execute_vision_job_uploads_once_and_persists_provider_ids() -> None:
    project_id = uuid4()
    asset = Asset(project_id=project_id, kind="reference_video", original_path="C:/video.mp4")
    vision_job = Job(project_id=project_id, kind="vision_analysis", status="queued")
    result = execute_vision_job(FakeSession(asset), vision_job, FakeGateway())

    assert result.status == "processing"
    assert result.provider_input_id == "vid-1"
    assert result.external_task_id == "task-1"


def test_shot_vision_job_uses_the_selected_shot_interval() -> None:
    project_id = uuid4()
    asset = Asset(project_id=project_id, kind="reference_video", original_path="C:/video.mp4")
    shot = Shot(id=uuid4(), timeline_revision_id=uuid4(), position=0, start_sec=2, end_sec=4)
    job = Job(project_id=project_id, kind="vision_shot_analysis", status="queued", shot_id=shot.id)

    assert (job.kind, job.shot_id, shot.start_sec, shot.end_sec) == ("vision_shot_analysis", shot.id, 2, 4)


def test_vision_hybrid_timeline_inherits_candidate_evidence() -> None:
    project_id = uuid4()
    with SessionLocal() as session:
        from app.db.models import Project
        session.add(Project(id=project_id, name="evidence inheritance"))
        session.add(Asset(project_id=project_id, kind="reference_video", original_path="C:/video.mp4", duration_sec=4))
        candidate = TimelineRevision(project_id=project_id, version=1, source="ffmpeg_candidates")
        session.add(candidate)
        session.flush()
        first = Shot(timeline_revision_id=candidate.id, position=0, start_sec=0, end_sec=2)
        second = Shot(timeline_revision_id=candidate.id, position=1, start_sec=2, end_sec=4)
        session.add_all([first, second])
        session.flush()
        session.add_all([
            ShotEvidence(shot_id=first.id, timestamp_sec=0.05, image_path="C:/a.jpg", source="ffmpeg_start"),
            ShotEvidence(shot_id=first.id, timestamp_sec=1, image_path="C:/b.jpg", source="ffmpeg_middle"),
            ShotEvidence(shot_id=second.id, timestamp_sec=3.95, image_path="C:/c.jpg", source="ffmpeg_end"),
        ])
        session.commit()

        _, hybrid = persist_vision_result(
            session,
            project_id,
            None,
            "{}",
            AnalysisResult("demo", None, None, None, [
                AnalyzedShot(0, 2, action="show"),
                AnalyzedShot(2, 4, action="close"),
            ]),
        )
        hybrid_shots = session.query(Shot).filter_by(timeline_revision_id=hybrid.id).order_by(Shot.position).all()

        assert [[e.source for e in shot.evidence] for shot in hybrid_shots] == [
            ["ffmpeg_start", "ffmpeg_middle"],
            ["ffmpeg_end"],
        ]
