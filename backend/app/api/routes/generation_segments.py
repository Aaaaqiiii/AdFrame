from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Asset, GenerationSegment, Project, Shot, ShotEdit, TimelineRevision
from app.db.session import get_session
from app.services.generation_segments import SegmentDraft, SegmentValidationError, plan_segments, validate_segments

router = APIRouter(prefix="/api/projects/{project_id}/generation-segments", tags=["generation-segments"])


class GenerationSegmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_start_sec: float = Field(ge=0)
    source_end_sec: float = Field(gt=0)
    start_boundary_type: str
    end_boundary_type: str
    short_segment_accepted: bool = False


class GenerationSegmentOutput(BaseModel):
    id: UUID
    position: int
    source_start_sec: float
    source_end_sec: float
    start_boundary_type: str
    end_boundary_type: str
    short_segment_accepted: bool
    clip_path: str | None = None
    public_url: str | None = None
    public_url_expires_at: str | None = None


class GenerationSegmentPlanResponse(BaseModel):
    plan_version: int
    timeline_revision_id: UUID
    max_segment_seconds: float
    recommended_min_seconds: float
    segments: list[GenerationSegmentOutput]


class GenerationSegmentPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segments: list[GenerationSegmentInput] = Field(min_length=1)


def _current_timeline_revision(session: Session, project_id: UUID) -> TimelineRevision | None:
    return session.scalar(
        select(TimelineRevision).where(TimelineRevision.project_id == project_id).order_by(TimelineRevision.version.desc())
    )


def _current_plan_version(session: Session, project_id: UUID) -> int:
    return session.scalar(
        select(func.max(GenerationSegment.plan_version)).where(GenerationSegment.project_id == project_id)
    ) or 0


def _segment_output(segment: GenerationSegment) -> GenerationSegmentOutput:
    return GenerationSegmentOutput(
        id=segment.id, position=segment.position,
        source_start_sec=segment.source_start_sec, source_end_sec=segment.source_end_sec,
        start_boundary_type=segment.start_boundary_type, end_boundary_type=segment.end_boundary_type,
        short_segment_accepted=segment.short_segment_accepted,
        clip_path=segment.clip_path, public_url=segment.public_url, public_url_expires_at=segment.public_url_expires_at,
    )


def _plan_response(session: Session, project_id: UUID, revision: TimelineRevision, plan_version: int, segments: list[GenerationSegment]) -> GenerationSegmentPlanResponse:
    settings = Settings()
    return GenerationSegmentPlanResponse(
        plan_version=plan_version,
        timeline_revision_id=revision.id,
        max_segment_seconds=settings.effective_segment_limit_seconds,
        recommended_min_seconds=settings.recommended_min_segment_seconds,
        segments=[_segment_output(segment) for segment in sorted(segments, key=lambda item: item.position)],
    )


def _validated_current_plan_context(session: Session, project_id: UUID) -> tuple[TimelineRevision, list[Shot], float]:
    """Return the current timeline, its shots, and the reference video duration."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    revision = _current_timeline_revision(session, project_id)
    if revision is None:
        raise HTTPException(status_code=422, detail="请先确认时间轴")
    shots = list(session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.id).order_by(Shot.position)))
    unconfirmed = [shot for shot in shots if not session.scalar(
        select(ShotEdit).where(ShotEdit.project_id == project_id, ShotEdit.shot_id == shot.id, ShotEdit.confirmed.is_(True))
    )]
    if unconfirmed:
        raise HTTPException(status_code=422, detail="时间轴存在未确认镜头，请先确认全部镜头")
    video = session.scalar(
        select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc())
    )
    if video is None or video.duration_sec is None:
        raise HTTPException(status_code=422, detail="请先上传参考视频")
    return revision, shots, video.duration_sec


def _persist_plan(session: Session, project_id: UUID, revision: TimelineRevision, drafts: list[SegmentDraft]) -> GenerationSegmentPlanResponse:
    """Lock the project row, bump plan_version, and persist one immutable plan."""
    session.execute(select(Project).where(Project.id == project_id).with_for_update())
    plan_version = _current_plan_version(session, project_id) + 1
    rows = [
        GenerationSegment(
            project_id=project_id, plan_version=plan_version, position=index,
            source_start_sec=draft.start_sec, source_end_sec=draft.end_sec,
            start_boundary_type=draft.start_boundary_type, end_boundary_type=draft.end_boundary_type,
            source_timeline_revision_id=revision.id,
            short_segment_accepted=draft.short_segment_accepted,
        )
        for index, draft in enumerate(drafts)
    ]
    session.add_all(rows)
    session.commit()
    return _plan_response(session, project_id, revision, plan_version, rows)


@router.get("", response_model=GenerationSegmentPlanResponse)
def get_generation_segments(project_id: UUID, session: Session = Depends(get_session)) -> GenerationSegmentPlanResponse:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    revision = _current_timeline_revision(session, project_id)
    if revision is None:
        # 无时间轴时返回空计划，前端显示自动规划入口。
        settings = Settings()
        return GenerationSegmentPlanResponse(
            plan_version=0, timeline_revision_id=UUID(int=0),
            max_segment_seconds=settings.effective_segment_limit_seconds,
            recommended_min_seconds=settings.recommended_min_segment_seconds,
            segments=[],
        )
    plan_version = _current_plan_version(session, project_id)
    if plan_version == 0:
        return GenerationSegmentPlanResponse(
            plan_version=0, timeline_revision_id=revision.id,
            max_segment_seconds=Settings().effective_segment_limit_seconds,
            recommended_min_seconds=Settings().recommended_min_segment_seconds,
            segments=[],
        )
    segments = list(session.scalars(
        select(GenerationSegment).where(
            GenerationSegment.project_id == project_id,
            GenerationSegment.plan_version == plan_version,
        ).order_by(GenerationSegment.position)
    ))
    return _plan_response(session, project_id, revision, plan_version, segments)


@router.post("/auto", response_model=GenerationSegmentPlanResponse, status_code=status.HTTP_201_CREATED)
def auto_plan_generation_segments(project_id: UUID, session: Session = Depends(get_session)) -> GenerationSegmentPlanResponse:
    revision, shots, duration_sec = _validated_current_plan_context(session, project_id)
    settings = Settings()
    drafts = plan_segments(
        duration_sec,
        [(str(shot.id), shot.start_sec, shot.end_sec) for shot in shots],
        settings.effective_segment_limit_seconds,
        settings.recommended_min_segment_seconds,
    )
    return _persist_plan(session, project_id, revision, drafts)


@router.put("", response_model=GenerationSegmentPlanResponse, status_code=status.HTTP_201_CREATED)
def save_manual_generation_segments(project_id: UUID, payload: GenerationSegmentPlanInput, session: Session = Depends(get_session)) -> GenerationSegmentPlanResponse:
    revision, _, duration_sec = _validated_current_plan_context(session, project_id)
    settings = Settings()
    drafts = [
        SegmentDraft(
            start_sec=item.source_start_sec, end_sec=item.source_end_sec,
            start_boundary_type=item.start_boundary_type, end_boundary_type=item.end_boundary_type,
            short_segment_accepted=item.short_segment_accepted,
        )
        for item in payload.segments
    ]
    try:
        validate_segments(duration_sec, drafts, settings.effective_segment_limit_seconds, settings.recommended_min_segment_seconds)
    except SegmentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _persist_plan(session, project_id, revision, drafts)
