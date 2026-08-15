from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.timeline import TimelineOutput, store_timeline_revision
from app.core.config import Settings
from app.db.models import Asset, Job, Project, Shot, ShotEvidence, TimelineRevision
from app.db.session import get_session
from app.services.media import detect_candidate_cuts, extract_keyframes, probe_video
from app.services.comfly_frame_vision import validate_frame_vision_configuration
from app.services.dual_shot_vision import validate_dual_vision_configuration
from app.services.volcengine_vision import VisionConfigurationError

router = APIRouter(prefix="/api/projects/{project_id}/analysis", tags=["analysis"])


@router.post("/start", response_model=TimelineOutput, status_code=status.HTTP_202_ACCEPTED)
def start_analysis(project_id: UUID, session: Session = Depends(get_session)) -> TimelineOutput:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    asset = session.scalar(
        select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc())
    )
    if asset is None:
        raise HTTPException(status_code=422, detail="请先上传参考视频")

    source = Path(asset.original_path)
    try:
        metadata = probe_video(source)
        cuts = sorted({cut for cut in detect_candidate_cuts(source) if 0 < cut < metadata.duration_sec})
    except Exception as exc:
        raise HTTPException(status_code=422, detail="参考视频无法解码") from exc

    boundaries = [0.0, *cuts, metadata.duration_sec]
    ranges = list(zip(boundaries, boundaries[1:]))
    timeline = store_timeline_revision(session, project_id, ranges, source="ffmpeg_candidates")
    try:
        shots = session.scalars(
            select(Shot).where(Shot.timeline_revision_id == timeline.revision_id).order_by(Shot.position)
        ).all()
        timestamp_map: list[tuple[Shot, float, str]] = []
        for shot in shots:
            duration = shot.end_sec - shot.start_sec
            margin = min(0.05, duration / 10)
            timestamp_map.extend([
                (shot, shot.start_sec + margin, "start"),
                (shot, (shot.start_sec + shot.end_sec) / 2, "middle"),
                (shot, shot.end_sec - margin, "end"),
            ])
        timestamps = [timestamp for _, timestamp, _ in timestamp_map]
        evidence_dir = source.parent.parent / "evidence" / str(timeline.revision_id)
        frames = extract_keyframes(source, evidence_dir, timestamps)
        session.add_all(
            ShotEvidence(shot_id=shot.id, timestamp_sec=frame.timestamp_sec, image_path=str(frame.path), source=f"ffmpeg_{role}")
            for (shot, _, role), frame in zip(timestamp_map, frames)
        )
        session.commit()
    except Exception:
        # Candidate cuts remain usable if an optional preview frame cannot be extracted.
        session.rollback()
    return timeline


@router.post("/vision", status_code=status.HTTP_202_ACCEPTED)
def request_vision_analysis(project_id: UUID, session: Session = Depends(get_session)) -> dict[str, str]:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project does not exist")
    asset = session.scalar(
        select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc())
    )
    if asset is None:
        raise HTTPException(status_code=422, detail="Upload a reference video first")
    try:
        validate_frame_vision_configuration(Settings())
    except VisionConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    existing = session.scalar(select(Job).where(Job.project_id == project_id, Job.kind == "vision_analysis", Job.status.in_(["queued", "uploaded", "processing", "retryable"])).order_by(Job.id.desc()))
    if existing:
        return {"job_id": str(existing.id), "status": existing.status}
    job = Job(project_id=project_id, kind="vision_analysis", status="queued", provider="comfly_gpt_frames")
    session.add(job)
    session.commit()
    return {"job_id": str(job.id), "status": job.status}


@router.post("/vision-shots", status_code=status.HTTP_202_ACCEPTED)
def request_shot_vision_analysis(project_id: UUID, session: Session = Depends(get_session)) -> dict[str, int]:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project does not exist")
    if session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc())) is None:
        raise HTTPException(status_code=422, detail="Upload a reference video first")
    try:
        validate_dual_vision_configuration(Settings())
    except VisionConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    revision = session.scalar(
        select(TimelineRevision).where(TimelineRevision.project_id == project_id).order_by(TimelineRevision.version.desc())
    )
    if revision is None:
        raise HTTPException(status_code=422, detail="Confirm a timeline before per-shot analysis")
    shots = session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.id).order_by(Shot.position)).all()
    # 当前仍在处理的镜头不重复入队；边界未变化且已成功的镜头直接复用结果。
    active_jobs = session.scalars(select(Job).where(
        Job.project_id == project_id,
        Job.kind == "vision_shot_analysis",
        Job.status.in_(["queued", "uploaded", "processing", "retryable"]),
    )).all()
    succeeded_shot_ids = {
        shot.id for shot in shots if shot.analysis_status in {"completed", "succeeded"}
    }
    # 修复前可能已经给成功镜头留下了排队任务；确认时一并作废，避免 Worker 继续扣费重跑。
    obsolete_jobs = [job for job in active_jobs if job.shot_id in succeeded_shot_ids]
    for job in obsolete_jobs:
        job.status = "cancelled"
        job.error_message = "Skipped because this unchanged shot already has a successful result."
    active_shot_ids = {
        job.shot_id for job in active_jobs if job.shot_id not in succeeded_shot_ids
    }
    shots_to_queue = [
        shot for shot in shots
        if shot.id not in active_shot_ids and shot.id not in succeeded_shot_ids
    ]
    jobs = [
        Job(
            project_id=project_id,
            shot_id=shot.id,
            kind="vision_shot_analysis",
            status="queued",
            provider="doubao_gpt_synthesis",
        )
        for shot in shots_to_queue
    ]
    for shot in shots_to_queue:
        shot.analysis_status, shot.analysis_error = "queued", None
    session.add_all(jobs)
    session.commit()
    return {
        "queued_shots": len(jobs),
        "skipped_succeeded": len(succeeded_shot_ids),
        "already_active": len(active_shot_ids),
        "cancelled_obsolete": len(obsolete_jobs),
    }


def _job_status(value: str) -> str:
    return {"uploaded": "processing", "retryable": "queued"}.get(value, value)


@router.get("/jobs")
def list_analysis_jobs(project_id: UUID, session: Session = Depends(get_session)) -> list[dict]:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    jobs = session.scalars(select(Job).where(
        Job.project_id == project_id,
        Job.kind.in_(["vision_analysis", "vision_shot_analysis", "reference_profile_analysis", "final_prompt_generation", "prompt_refinement"]),
    ).order_by(Job.created_at.desc(), Job.id.desc())).all()
    return [{
        "job_id": str(job.id), "kind": job.kind, "shot_id": str(job.shot_id) if job.shot_id else None,
        "status": _job_status(job.status), "error": job.error_message, "attempts": job.attempts,
    } for job in jobs]


@router.post("/shots/{shot_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_shot_analysis(project_id: UUID, shot_id: UUID, session: Session = Depends(get_session)) -> dict[str, str]:
    shot = session.get(Shot, shot_id)
    if shot is None or shot.timeline_revision.project_id != project_id:
        raise HTTPException(status_code=404, detail="镜头不存在")
    latest = session.scalar(select(TimelineRevision).where(TimelineRevision.project_id == project_id).order_by(TimelineRevision.version.desc()))
    if latest is None or shot.timeline_revision_id != latest.id:
        raise HTTPException(status_code=422, detail="只能重试当前时间轴中的镜头")
    active = session.scalar(select(Job).where(
        Job.project_id == project_id, Job.shot_id == shot_id, Job.kind == "vision_shot_analysis",
        Job.status.in_(["queued", "uploaded", "processing", "retryable"]),
    ).order_by(Job.id.desc()))
    if active:
        return {"job_id": str(active.id), "shot_id": str(shot_id), "status": _job_status(active.status)}
    shot.analysis_status, shot.analysis_error = "queued", None
    # 新任务产生新的AI总结；已保存的人工版本不会在这里被覆盖。
    job = Job(project_id=project_id, shot_id=shot_id, kind="vision_shot_analysis", status="queued", provider="doubao_gpt_synthesis")
    session.add(job)
    session.commit()
    return {"job_id": str(job.id), "shot_id": str(shot_id), "status": "queued"}


@router.get("/jobs/{job_id}")
def get_analysis_job(project_id: UUID, job_id: UUID, session: Session = Depends(get_session)) -> dict[str, str | None]:
    job = session.get(Job, job_id)
    if job is None or job.project_id != project_id or job.kind != "vision_analysis":
        raise HTTPException(status_code=404, detail="Analysis job does not exist")
    return {
        "job_id": str(job.id), "status": job.status, "provider": job.provider,
        "external_task_id": job.external_task_id, "error_message": job.error_message,
    }
