"""Atomic generation-batch creation and derived batch status.

A batch is a UUID shared by ordinary ``Generation`` rows. The create endpoint
validates the whole batch before inserting its first row; any invalid segment
causes zero rows to be created. Worker leasing stays per row.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Asset, Generation, GenerationSegment, Project, PromptRevision, Shot, ShotEdit, TimelineRevision
from app.db.session import get_session
from app.services.full_prompt import FullPromptValidationError, validate_full_prompt
from app.services.product_rules import confirmed_target_product_assets
from app.services.reference_profiles import load_structure
from app.api.routes.generations import generation_response

router = APIRouter(prefix="/api/projects/{project_id}/generation-batches", tags=["generation-batches"])


class CreateGenerationBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(pattern="^(volcengine|comfly)$")
    prompt_version: int = Field(ge=1)
    generate_audio: bool = False
    include_person_reference: bool = False
    include_background_reference: bool = False


class GenerationBatchResponse(BaseModel):
    generation_batch_id: UUID
    project_id: UUID
    provider: str
    prompt_version: int
    batch_size: int
    status: str
    generations: list[object]


@dataclass(frozen=True)
class BatchInputs:
    project: Project
    timeline_revision: TimelineRevision
    prompt_revision: PromptRevision
    segments: tuple[GenerationSegment, ...]
    original_video_asset: Asset
    reference_assets: tuple[Asset, ...]


def _current_timeline_revision(session: Session, project_id: UUID) -> TimelineRevision | None:
    return session.scalar(
        select(TimelineRevision).where(TimelineRevision.project_id == project_id).order_by(TimelineRevision.version.desc())
    )


def _current_shots(session: Session, revision: TimelineRevision) -> list[Shot]:
    return list(session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.id).order_by(Shot.position)))


def _confirmed_product_assets(session: Session, project_id: UUID) -> list[Asset]:
    assets = list(session.scalars(select(Asset).where(Asset.project_id == project_id, Asset.kind == "product_reference_image").order_by(Asset.id)))
    confirmed = []
    for asset in assets:
        if not asset.profile_text or not asset.profile_text.strip():
            continue
        if asset.analysis_status not in {"succeeded", "completed"}:
            continue
        if not load_structure(asset).get("summary_confirmed"):
            continue
        confirmed.append(asset)
    return confirmed


def _provider_key_available(settings: Settings, provider: str) -> bool:
    if provider == "volcengine":
        return bool(settings.volcengine_api_key)
    return bool(settings.comfly_api_key)


def _require_batch_inputs(
    session: Session,
    *,
    project_id: UUID,
    prompt_version: int,
    provider: str,
    settings: Settings,
    include_person_reference: bool,
    include_background_reference: bool,
) -> BatchInputs:
    """只读 preflight：任何校验失败都抛 HTTPException，不写入任何行。"""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    timeline = _current_timeline_revision(session, project_id)
    if timeline is None:
        raise HTTPException(status_code=422, detail="请先确认时间轴")
    shots = _current_shots(session, timeline)
    if not shots:
        raise HTTPException(status_code=422, detail="当前时间轴没有镜头")
    unconfirmed = [shot for shot in shots if not session.scalar(
        select(ShotEdit).where(ShotEdit.project_id == project_id, ShotEdit.shot_id == shot.id, ShotEdit.confirmed.is_(True))
    )]
    if unconfirmed:
        raise HTTPException(status_code=422, detail="时间轴存在未确认镜头，请先确认全部镜头")
    prompt = session.scalar(select(PromptRevision).where(
        PromptRevision.project_id == project_id, PromptRevision.version == prompt_version,
    ))
    if prompt is None or prompt.status != "completed" or not (prompt.text or "").strip():
        raise HTTPException(status_code=422, detail="请先生成或保存一份完整提示词")
    if prompt.prompt_mode != "full_reference_video_edit" or prompt.generation_segment_id is not None:
        raise HTTPException(status_code=422, detail="批次生成必须使用完整视频编辑提示词")
    if prompt.source_timeline_revision_id != timeline.id:
        raise HTTPException(status_code=422, detail="提示词来自旧时间轴，请重新生成提示词")
    # 完整提示词结构校验。
    shot_ranges = [(shot.start_sec, shot.end_sec) for shot in shots]
    try:
        validate_full_prompt(prompt.text, shot_ranges)
    except FullPromptValidationError as exc:
        raise HTTPException(status_code=422, detail=f"完整提示词结构无效：{exc}") from exc
    # 当前/最新不可变方案。
    latest_plan = session.scalar(
        select(func.max(GenerationSegment.plan_version)).where(
            GenerationSegment.project_id == project_id,
            GenerationSegment.source_timeline_revision_id == timeline.id,
        )
    )
    if latest_plan is None:
        raise HTTPException(status_code=422, detail="请先生成分段方案")
    segments = tuple(session.scalars(select(GenerationSegment).where(
        GenerationSegment.project_id == project_id,
        GenerationSegment.plan_version == latest_plan,
    ).order_by(GenerationSegment.position)))
    if not segments:
        raise HTTPException(status_code=422, detail="分段方案为空")
    # 方案覆盖/时长/短段确认。
    if abs(segments[0].source_start_sec) > 0.001:
        raise HTTPException(status_code=422, detail="分段方案必须从 0 开始")
    for index, segment in enumerate(segments):
        duration = segment.source_end_sec - segment.source_start_sec
        if duration <= 0:
            raise HTTPException(status_code=422, detail="分段时长必须大于 0")
        if duration > settings.effective_segment_limit_seconds:
            raise HTTPException(status_code=422, detail="生成片段超过安全时长上限")
        if index and abs(segment.source_start_sec - segments[index - 1].source_end_sec) > 0.001:
            raise HTTPException(status_code=422, detail="分段方案存在空缺或重叠")
        if index != len(segments) - 1 and abs(segment.source_end_sec - segments[index + 1].source_start_sec) > 0.001:
            raise HTTPException(status_code=422, detail="分段方案存在空缺或重叠")
        if duration < settings.recommended_min_segment_seconds - 0.001 and not segment.short_segment_accepted:
            covers_full = abs(segment.source_start_sec) <= 0.001 and abs(segment.source_end_sec - (timeline_shots_duration(shots))) <= 0.001
            if not covers_full:
                raise HTTPException(status_code=422, detail="短片段需要明确确认")
    if abs(segments[-1].source_end_sec - shots[-1].end_sec) > 0.05:
        raise HTTPException(status_code=422, detail="分段方案必须覆盖完整视频")
    # 原视频资产。
    video = session.scalar(select(Asset).where(
        Asset.project_id == project_id, Asset.kind == "reference_video",
    ).order_by(Asset.id.desc()))
    if video is None:
        raise HTTPException(status_code=422, detail="请先上传参考视频")
    reference_assets: list[Asset] = []
    # 替换产品模式：必须包含全部已确认产品图。
    if project.mode == "replace_product":
        product_assets = _confirmed_product_assets(session, project_id)
        if not product_assets:
            raise HTTPException(status_code=422, detail="请先确认目标产品档案")
        reference_assets.extend(product_assets)
    # 人物/背景参考。
    person_asset = session.scalar(select(Asset).where(
        Asset.project_id == project_id, Asset.kind == "person_reference_image",
    ).order_by(Asset.id.desc()))
    if prompt.replace_person:
        if person_asset is None or not person_asset.profile_user_edited:
            raise HTTPException(status_code=422, detail="选择替换人物前，请先上传并确认人物图片档案")
        if not include_person_reference:
            raise HTTPException(status_code=422, detail="提示词已开启人物替换，必须包含人物参考图")
        reference_assets.append(person_asset)
    elif include_person_reference:
        raise HTTPException(status_code=422, detail="提示词未开启人物替换，不能包含人物参考图")
    if include_background_reference:
        background = session.scalar(select(Asset).where(
            Asset.project_id == project_id, Asset.kind == "background_reference_image",
        ).order_by(Asset.id.desc()))
        if background is None:
            raise HTTPException(status_code=422, detail="请求包含背景参考图，但项目没有背景参考图素材")
        reference_assets.append(background)
    # provider key。
    if not _provider_key_available(settings, provider):
        raise HTTPException(status_code=409, detail="供应商 API Key 未配置")
    return BatchInputs(
        project=project, timeline_revision=timeline, prompt_revision=prompt,
        segments=segments, original_video_asset=video, reference_assets=tuple(reference_assets),
    )


def timeline_shots_duration(shots: list[Shot]) -> float:
    return shots[-1].end_sec if shots else 0.0


@router.post("", response_model=GenerationBatchResponse, status_code=status.HTTP_201_CREATED)
def create_generation_batch(
    project_id: UUID,
    payload: CreateGenerationBatchRequest,
    session: Session = Depends(get_session),
) -> GenerationBatchResponse:
    settings = Settings()
    inputs = _require_batch_inputs(
        session, project_id=project_id, prompt_version=payload.prompt_version,
        provider=payload.provider, settings=settings,
        include_person_reference=payload.include_person_reference,
        include_background_reference=payload.include_background_reference,
    )
    # 单事务插入：先锁项目行，锁内重新检查活动重复与版本分配，避免并发创建双批次。
    session.execute(select(Project).where(Project.id == project_id).with_for_update())
    active_statuses = ("queued", "processing", "retryable")
    segment_ids = [segment.id for segment in inputs.segments]
    active = session.scalar(select(Generation.id).where(
        Generation.project_id == project_id,
        Generation.generation_segment_id.in_(segment_ids),
        Generation.status.in_(active_statuses),
    ).limit(1))
    if active is not None:
        raise HTTPException(status_code=409, detail="相同输入和设置的活动生成任务已存在")
    max_version = session.scalar(select(func.max(Generation.version)).where(Generation.project_id == project_id)) or 0
    batch_id = uuid4()
    batch_size = len(inputs.segments)
    asset_ids = [str(inputs.original_video_asset.id)] + [str(asset.id) for asset in inputs.reference_assets]
    rows: list[Generation] = []
    for index, segment in enumerate(inputs.segments):
        version = max_version + index + 1
        fingerprint_payload = {
            "project_id": str(project_id),
            "timeline_revision_id": str(inputs.timeline_revision.id),
            "plan_version": segment.plan_version,
            "segment_id": str(segment.id),
            "segment_start_sec": segment.source_start_sec,
            "segment_end_sec": segment.source_end_sec,
            "prompt_revision_id": str(inputs.prompt_revision.id),
            "prompt_version": payload.prompt_version,
            "provider": payload.provider,
            "generate_audio": payload.generate_audio,
            "asset_ids": asset_ids,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        rows.append(Generation(
            project_id=project_id, version=version, prompt_version=payload.prompt_version,
            generation_segment_id=segment.id, generation_batch_id=batch_id,
            batch_position=index + 1, batch_size=batch_size,
            provider=payload.provider, ratio="adaptive", duration=-1,
            generate_audio=payload.generate_audio, status="queued",
            reference_asset_ids=json.dumps(asset_ids, ensure_ascii=False),
            submission_fingerprint=fingerprint,
        ))
    session.add_all(rows)
    session.flush()
    serialized = [generation_response(project_id, row) for row in rows]
    session.commit()
    return GenerationBatchResponse(
        generation_batch_id=batch_id, project_id=project_id, provider=payload.provider,
        prompt_version=payload.prompt_version, batch_size=batch_size, status="queued",
        generations=serialized,
    )
