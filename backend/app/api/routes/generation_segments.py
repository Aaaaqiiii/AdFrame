from typing import Literal
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


BoundaryType = Literal["video_edge", "shot_boundary", "inside_shot"]


class GenerationSegmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_start_sec: float = Field(ge=0)
    source_end_sec: float = Field(gt=0)
    start_boundary_type: BoundaryType
    end_boundary_type: BoundaryType
    short_segment_accepted: bool = False


class GenerationSegmentOutput(BaseModel):
    id: UUID
    position: int
    source_start_sec: float
    source_end_sec: float
    start_boundary_type: BoundaryType
    end_boundary_type: BoundaryType
    short_segment_accepted: bool


class GenerationSegmentPlanResponse(BaseModel):
    plan_version: int
    timeline_revision_id: UUID | None
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


def _latest_current_plan_version(session: Session, project_id: UUID, revision_id: UUID) -> int:
    """GET 当前方案：只统计基于当前时间轴的方案，旧时间轴方案不算数。"""
    return session.scalar(
        select(func.max(GenerationSegment.plan_version)).where(
            GenerationSegment.project_id == project_id,
            GenerationSegment.source_timeline_revision_id == revision_id,
        )
    ) or 0


def _next_project_plan_version(session: Session, project_id: UUID) -> int:
    """创建新方案：项目全局 max(plan_version) + 1，避免跨时间轴重复版本撞唯一约束。"""
    return (session.scalar(
        select(func.max(GenerationSegment.plan_version)).where(GenerationSegment.project_id == project_id)
    ) or 0) + 1


def _segment_output(segment: GenerationSegment) -> GenerationSegmentOutput:
    # 传输元数据（本地裁切路径、临时公网 URL）仅属于 Worker 内部，不暴露给 API。
    return GenerationSegmentOutput(
        id=segment.id, position=segment.position,
        source_start_sec=segment.source_start_sec, source_end_sec=segment.source_end_sec,
        start_boundary_type=segment.start_boundary_type, end_boundary_type=segment.end_boundary_type,
        short_segment_accepted=segment.short_segment_accepted,
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
    current_revision = _current_timeline_revision(session, project_id)
    if current_revision is None or current_revision.id != revision.id:
        raise HTTPException(status_code=409, detail="时间轴刚刚被其他页面更新，请基于最新时间轴重新规划")
    plan_version = _next_project_plan_version(session, project_id)
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
        # 无时间轴时返回空计划（timeline_revision_id 为 null），前端显示自动规划入口。
        settings = Settings()
        return GenerationSegmentPlanResponse(
            plan_version=0, timeline_revision_id=None,
            max_segment_seconds=settings.effective_segment_limit_seconds,
            recommended_min_seconds=settings.recommended_min_segment_seconds,
            segments=[],
        )
    plan_version = _latest_current_plan_version(session, project_id, revision.id)
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
            GenerationSegment.source_timeline_revision_id == revision.id,
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


def _validate_boundary_types(items: list[GenerationSegmentInput], duration_sec: float, shots: list[Shot]) -> None:
    """校验边界标签与真实分镜位置一致，并保持相邻共享切点类型一致。

    - ``video_edge`` 只能用于 0 秒或视频结束；
    - ``shot_boundary`` 切点必须落在某个分镜结束点的 0.001 秒容差内；
    - ``inside_shot`` 切点不得位于任何分镜边界；
    - 相邻片段共享切点，前后类型必须一致；首尾必须是 ``video_edge``。
    """
    shot_ends = {round(shot.end_sec, 3) for shot in shots}
    epsilon = 0.001

    def _is_shot_boundary(value: float) -> bool:
        return any(abs(value - end) <= epsilon for end in shot_ends)

    def _check(boundary_type: str, value: float) -> None:
        if boundary_type == "video_edge":
            if not (abs(value) <= epsilon or abs(value - duration_sec) <= epsilon):
                raise HTTPException(status_code=422, detail="视频边缘只能用于 0 秒或视频结束")
        elif boundary_type == "shot_boundary":
            if not _is_shot_boundary(value):
                raise HTTPException(status_code=422, detail="声明为镜头边界的切点必须落在分镜结束点")
        elif boundary_type == "inside_shot":
            if _is_shot_boundary(value):
                raise HTTPException(status_code=422, detail="声明为镜头内部的切点不能落在分镜边界")

    if items[0].start_boundary_type != "video_edge":
        raise HTTPException(status_code=422, detail="第一段起点必须是视频边缘")
    if items[-1].end_boundary_type != "video_edge":
        raise HTTPException(status_code=422, detail="最后一段终点必须是视频边缘")
    for item in items:
        _check(item.start_boundary_type, item.source_start_sec)
        _check(item.end_boundary_type, item.source_end_sec)
    for previous, current in zip(items, items[1:]):
        if previous.end_boundary_type != current.start_boundary_type:
            raise HTTPException(status_code=422, detail="相邻片段共享切点类型必须一致")


@router.put("", response_model=GenerationSegmentPlanResponse, status_code=status.HTTP_201_CREATED)
def save_manual_generation_segments(project_id: UUID, payload: GenerationSegmentPlanInput, session: Session = Depends(get_session)) -> GenerationSegmentPlanResponse:
    revision, shots, duration_sec = _validated_current_plan_context(session, project_id)
    settings = Settings()
    _validate_boundary_types(payload.segments, duration_sec, shots)
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
