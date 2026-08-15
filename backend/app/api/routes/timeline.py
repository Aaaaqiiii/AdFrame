from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Asset, Job, Project, Shot, ShotEdit, ShotEvidence, TimelineRevision
from app.db.session import get_session

router = APIRouter(prefix="/api/projects/{project_id}/timeline", tags=["timeline"])


class ShotInput(BaseModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)


class TimelineInput(BaseModel):
    shots: list[ShotInput] = Field(min_length=1)


class SplitInput(BaseModel):
    shot_id: UUID
    at_sec: float = Field(gt=0)


class MergeInput(BaseModel):
    shot_ids: list[UUID] = Field(min_length=2)


class ShotOutput(BaseModel):
    id: UUID
    start_sec: float
    end_sec: float


class TimelineOutput(BaseModel):
    revision_id: UUID
    shots: list[ShotOutput]
    affected_shot_ids: list[UUID] = Field(default_factory=list)


def _next_version(session: Session, project_id: UUID) -> int:
    current = session.scalar(
        select(func.max(TimelineRevision.version)).where(TimelineRevision.project_id == project_id)
    )
    return (current or 0) + 1


def store_timeline_revision(
    session: Session, project_id: UUID, ranges: list[tuple[float, float]], source: str = "human", previous_shots: list[Shot] | None = None
) -> TimelineOutput:
    revision = TimelineRevision(project_id=project_id, version=_next_version(session, project_id), source=source)
    session.add(revision)
    session.flush()
    shots = [Shot(timeline_revision_id=revision.id, position=index, start_sec=start, end_sec=end)
             for index, (start, end) in enumerate(ranges)]
    session.add_all(shots)
    session.flush()
    affected: list[Shot] = []
    for new_shot in shots:
        if not previous_shots:
            if source == "human":
                affected.append(new_shot)
            continue
        source_shot = max(previous_shots, key=lambda old: max(0.0, min(old.end_sec, new_shot.end_sec) - max(old.start_sec, new_shot.start_sec)))
        overlap = max(0.0, min(source_shot.end_sec, new_shot.end_sec) - max(source_shot.start_sec, new_shot.start_sec))
        if not overlap:
            affected.append(new_shot)
            continue
        exact = abs(source_shot.start_sec - new_shot.start_sec) <= 0.001 and abs(source_shot.end_sec - new_shot.end_sec) <= 0.001
        for field in ("people", "action", "product", "product_interaction", "background", "camera", "lighting", "visual_style", "keep_unchanged", "on_screen_text", "observations", "inferences", "uncertainties"):
            setattr(new_shot, field, getattr(source_shot, field))
        new_shot.analysis_status = source_shot.analysis_status if exact else "queued"
        new_shot.analysis_error = None
        edit = session.scalar(select(ShotEdit).where(ShotEdit.project_id == project_id, ShotEdit.shot_id == source_shot.id))
        if edit:
            # 边界变化后的旧人工稿只作参考，不能继续视为对新时间范围的确认事实。
            session.add(ShotEdit(project_id=project_id, shot_id=new_shot.id, people=edit.people, action=edit.action, product=edit.product, product_interaction=edit.product_interaction, background=edit.background, camera=edit.camera, lighting=edit.lighting, visual_style=edit.visual_style, visible_text=edit.visible_text, keep_unchanged=edit.keep_unchanged, uncertainties=edit.uncertainties, confirmed=edit.confirmed if exact else False, version=edit.version, ai_summary_version=edit.ai_summary_version if exact else 0))
        for evidence in source_shot.evidence:
            if new_shot.start_sec <= evidence.timestamp_sec <= new_shot.end_sec:
                session.add(ShotEvidence(shot_id=new_shot.id, timestamp_sec=evidence.timestamp_sec, image_path=evidence.image_path, source=evidence.source, observation=evidence.observation))
        if not exact:
            affected.append(new_shot)
    # 保存只代表记录人工边界；模型任务必须由用户点击“确认并开始逐镜理解”后显式创建。
    if previous_shots:
        old_ids = [shot.id for shot in previous_shots]
        for job in session.scalars(select(Job).where(
            Job.project_id == project_id, Job.shot_id.in_(old_ids),
            Job.kind == "vision_shot_analysis",
            Job.status.in_(["queued", "uploaded", "processing", "retryable"]),
        )):
            job.status, job.error_message = "superseded", "时间轴已更新，旧镜头任务已作废"
            job.leased_at, job.leased_by = None, None
    session.commit()
    return TimelineOutput(revision_id=revision.id, shots=[ShotOutput(id=shot.id, start_sec=shot.start_sec, end_sec=shot.end_sec) for shot in shots], affected_shot_ids=[shot.id for shot in affected])


def _revision_shots(session: Session, project_id: UUID, revision_id: UUID) -> list[Shot]:
    revision = session.get(TimelineRevision, revision_id)
    if revision is None or revision.project_id != project_id:
        raise HTTPException(status_code=404, detail="时间轴版本不存在")
    return list(session.scalars(select(Shot).where(Shot.timeline_revision_id == revision_id).order_by(Shot.position)))


def _validate_contiguous_ranges(ranges: list[tuple[float, float]], duration_sec: float | None = None) -> None:
    if abs(ranges[0][0]) > 0.001:
        raise HTTPException(status_code=422, detail="Timeline must start at zero")
    for index, (start, end) in enumerate(ranges):
        if start >= end:
            raise HTTPException(status_code=422, detail="镜头结束时间必须大于开始时间")
        if index and abs(start - ranges[index - 1][1]) > 0.001:
            raise HTTPException(status_code=422, detail="时间轴不能有空缺或重叠")
    if duration_sec is not None and abs(ranges[-1][1] - duration_sec) > 0.05:
        raise HTTPException(status_code=422, detail="Timeline must cover the complete reference video")


@router.put("", response_model=TimelineOutput, status_code=status.HTTP_202_ACCEPTED)
def save_human_timeline(project_id: UUID, payload: TimelineInput, session: Session = Depends(get_session)) -> TimelineOutput:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    ranges = [(shot.start_sec, shot.end_sec) for shot in payload.shots]
    asset = session.scalar(
        select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc())
    )
    _validate_contiguous_ranges(ranges, asset.duration_sec if asset else None)
    previous_revision = session.scalar(select(TimelineRevision).where(TimelineRevision.project_id == project_id).order_by(TimelineRevision.version.desc()))
    previous_shots = list(session.scalars(select(Shot).where(Shot.timeline_revision_id == previous_revision.id).order_by(Shot.position))) if previous_revision else None
    result = store_timeline_revision(session, project_id, ranges, previous_shots=previous_shots)
    return result


@router.post("/{revision_id}/split", response_model=TimelineOutput, status_code=status.HTTP_202_ACCEPTED)
def split_shot(project_id: UUID, revision_id: UUID, payload: SplitInput, session: Session = Depends(get_session)) -> TimelineOutput:
    shots = _revision_shots(session, project_id, revision_id)
    target = next((shot for shot in shots if shot.id == payload.shot_id), None)
    if target is None or not target.start_sec < payload.at_sec < target.end_sec:
        raise HTTPException(status_code=422, detail="拆分点必须位于选中镜头内部")
    ranges = [(shot.start_sec, shot.end_sec) for shot in shots]
    index = shots.index(target)
    ranges[index:index + 1] = [(target.start_sec, payload.at_sec), (payload.at_sec, target.end_sec)]
    return store_timeline_revision(session, project_id, ranges, previous_shots=shots)


@router.post("/{revision_id}/merge", response_model=TimelineOutput, status_code=status.HTTP_202_ACCEPTED)
def merge_shots(project_id: UUID, revision_id: UUID, payload: MergeInput, session: Session = Depends(get_session)) -> TimelineOutput:
    shots = _revision_shots(session, project_id, revision_id)
    selected = [shot for shot in shots if shot.id in payload.shot_ids]
    if len(selected) != len(payload.shot_ids):
        raise HTTPException(status_code=422, detail="存在不属于该时间轴的镜头")
    positions = [shot.position for shot in selected]
    if positions != list(range(positions[0], positions[-1] + 1)):
        raise HTTPException(status_code=422, detail="只能合并连续镜头")
    ranges = [(shot.start_sec, shot.end_sec) for shot in shots]
    first, last = positions[0], positions[-1]
    ranges[first:last + 1] = [(ranges[first][0], ranges[last][1])]
    return store_timeline_revision(session, project_id, ranges, previous_shots=shots)


@router.post("/{revision_id}/restore-ai", response_model=TimelineOutput, status_code=status.HTTP_202_ACCEPTED)
def restore_ai_timeline(project_id: UUID, revision_id: UUID, session: Session = Depends(get_session)) -> TimelineOutput:
    revision = session.get(TimelineRevision, revision_id)
    if revision is None or revision.project_id != project_id or revision.source not in {"vision_hybrid", "ai"}:
        raise HTTPException(status_code=422, detail="只能恢复该项目的 AI 初始时间轴")
    source_shots = list(session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.id).order_by(Shot.position)))
    if not source_shots:
        raise HTTPException(status_code=422, detail="AI 初始时间轴没有镜头")
    restored = store_timeline_revision(session, project_id, [(shot.start_sec, shot.end_sec) for shot in source_shots], source="ai_restored", previous_shots=source_shots)
    return restored.model_copy(update={"affected_shot_ids": []})
