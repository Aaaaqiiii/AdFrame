"""Atomic generation-batch creation and derived batch status.

A batch is a UUID shared by ordinary ``Generation`` rows. The create endpoint
validates the whole batch before inserting its first row; any invalid segment
causes zero rows to be created. Worker leasing stays per row.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Asset, Generation, GenerationSegment, Project, PromptRevision, Shot, ShotEdit, TimelineRevision
from app.db.session import get_session
from app.services.full_prompt import FullPromptValidationError, validate_full_prompt
from app.services.final_prompt import generation_prompt_contract_ready, normalize_full_prompt_contract, person_replacement_contract_ready
from app.services.product_rules import confirmed_generation_product_assets
from app.services.reference_profiles import load_structure
from app.services.media import MediaToolUnavailableError, concat_videos_lossless
from app.api.routes.generations import generation_response

router = APIRouter(prefix="/api/projects/{project_id}/generation-batches", tags=["generation-batches"])


class CreateGenerationBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(pattern="^(volcengine|comfly)$")
    prompt_version: int = Field(ge=1)
    ratio: Literal["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"] = "adaptive"
    generate_audio: bool = False
    # 兼容旧客户端；服务端始终根据提示词和已上传素材自动附加参考图。
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
    project = session.get(Project, project_id)
    return confirmed_generation_product_assets(session, project) if project else []


def derive_batch_status(statuses: list[str]) -> str:
    """从各行的持久化状态派生批次状态。

    优先级：
    1. uncertain：任一 submission_uncertain；
    2. queued：全部 queued；
    3. processing：任一 queued/processing/retryable（非全 queued 时）；
    4. complete：全部 completed；
    5. partial：至少一个 completed 且至少一个 failed；
    6. failed：全部 failed。
    """
    if not statuses:
        return "failed"
    if any(status == "submission_uncertain" for status in statuses):
        return "uncertain"
    if all(status == "queued" for status in statuses):
        return "queued"
    if any(status in {"queued", "processing", "retryable"} for status in statuses):
        return "processing"
    if all(status == "completed" for status in statuses):
        return "complete"
    if any(status == "completed" for status in statuses) and any(status == "failed" for status in statuses):
        return "partial"
    if all(status == "failed" for status in statuses):
        return "failed"
    return "failed"


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
    edits = [session.scalar(select(ShotEdit).where(ShotEdit.project_id == project_id, ShotEdit.shot_id == shot.id)) for shot in shots]
    if any(edit is None or not edit.confirmed for edit in edits):
        raise HTTPException(status_code=422, detail="时间轴存在未确认镜头，请先确认全部镜头")
    prompt = session.scalar(select(PromptRevision).where(
        PromptRevision.project_id == project_id, PromptRevision.version == prompt_version,
    ))
    if prompt is None or prompt.status != "completed" or not (prompt.text or "").strip():
        raise HTTPException(status_code=422, detail="请先生成或保存一份完整提示词")
    if prompt.prompt_mode != "full_reference_video_edit" or prompt.generation_segment_id is not None:
        raise HTTPException(status_code=422, detail="批次生成必须使用完整视频编辑提示词")
    if prompt.replace_person and not person_replacement_contract_ready(prompt.text):
        raise HTTPException(status_code=422, detail="当前人物替换提示词仍使用旧版冲突规则，请重新生成提示词后再提交视频")
    if not generation_prompt_contract_ready(prompt.text, prompt.visual_direction):
        raise HTTPException(status_code=422, detail="当前提示词仍使用旧版参考范围或文字清理规则，请重新生成提示词后再提交视频")
    if prompt.source_timeline_revision_id != timeline.id:
        raise HTTPException(status_code=422, detail="提示词来自旧时间轴，请重新生成提示词")
    # 完整提示词结构校验。
    shot_ranges = [(shot.start_sec, shot.end_sec) for shot in shots]
    try:
        validate_full_prompt(prompt.text, shot_ranges)
        normalized = normalize_full_prompt_contract(
            prompt.text,
            shot_ranges,
            [{"facts": {"people": edit.people}} for edit in edits if edit is not None],
            project_mode=project.mode,
            replace_person=prompt.replace_person,
        )
    except FullPromptValidationError as exc:
        raise HTTPException(status_code=422, detail=f"完整提示词结构无效：{exc}") from exc
    if normalized.strip() != prompt.text.strip():
        raise HTTPException(status_code=422, detail="当前提示词缺少人物替换或画面文字硬约束，请重新保存后再提交视频")
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
    current_reference_assets: list[Asset] = []
    if project.mode == "replace_product":
        current_reference_assets.extend(_confirmed_product_assets(session, project_id))
        if not current_reference_assets:
            raise HTTPException(status_code=422, detail="请先确认目标产品档案")
    person_asset = session.scalar(select(Asset).where(
        Asset.project_id == project_id, Asset.kind == "person_reference_image",
    ).order_by(Asset.id.desc()))
    if prompt.replace_person:
        if person_asset is None or not person_asset.profile_user_edited:
            raise HTTPException(status_code=422, detail="选择替换人物前，请先确认人物文字档案或人物图片档案")
    if person_asset is not None:
        current_reference_assets.append(person_asset)
    background = session.scalar(select(Asset).where(
        Asset.project_id == project_id, Asset.kind == "background_reference_image",
    ).order_by(Asset.id.desc()))
    if background is not None:
        current_reference_assets.append(background)
    reference_assets = current_reference_assets
    if prompt.reference_asset_ids:
        try:
            frozen_ids = [UUID(value) for value in json.loads(prompt.reference_asset_ids)]
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="提示词参考素材快照损坏，请重新生成提示词") from exc
        frozen_assets = [asset for asset_id in frozen_ids if (asset := session.get(Asset, asset_id)) is not None and asset.project_id == project_id]
        if len(frozen_assets) != len(frozen_ids):
            raise HTTPException(status_code=422, detail="提示词引用的参考素材已不存在，请重新生成提示词")
        if {asset.id for asset in frozen_assets} != {asset.id for asset in current_reference_assets}:
            raise HTTPException(status_code=422, detail="人物、产品或背景参考素材已更新，请重新生成或保存提示词后再提交视频")
        reference_assets = frozen_assets
    reference_assets = [asset for asset in reference_assets if (asset.original_path or "").strip()]
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
    # 锁内读取当前时间轴、提示词与方案，避免校验后到插入前被其他标签页换版。
    session.execute(select(Project).where(Project.id == project_id).with_for_update())
    inputs = _require_batch_inputs(
        session, project_id=project_id, prompt_version=payload.prompt_version,
        provider=payload.provider, settings=settings,
    )
    asset_ids = [str(inputs.original_video_asset.id)] + [str(asset.id) for asset in inputs.reference_assets]
    fingerprints: list[str] = []
    generate_audio = inputs.prompt_revision.audio_mode in {"auto", "custom", "add_style"}
    for segment in inputs.segments:
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
            "ratio": payload.ratio,
            "generate_audio": generate_audio,
            "asset_ids": asset_ids,
        }
        fingerprints.append(hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest())
    # 平台可能已经接单但本地超时的任务也必须参与去重，否则再次提交可能重复计费。
    active_statuses = ("queued", "processing", "retryable", "submission_uncertain")
    active = session.scalar(select(Generation).where(
        Generation.project_id == project_id,
        Generation.submission_fingerprint.in_(fingerprints),
        Generation.status.in_(active_statuses),
    ).limit(1))
    if active is not None:
        if active.generation_batch_id is not None:
            return get_generation_batch(project_id, active.generation_batch_id, session)
        raise HTTPException(status_code=409, detail="相同输入和设置的活动生成任务已存在")
    segment_ids = [segment.id for segment in inputs.segments]
    conflicting = session.scalar(select(Generation).where(
        Generation.project_id == project_id,
        Generation.generation_segment_id.in_(segment_ids),
        Generation.status.in_(active_statuses),
    ).limit(1))
    if conflicting is not None:
        batch_id = str(conflicting.generation_batch_id or "")
        raise HTTPException(status_code=409, detail=f"这些分段已有活动批次 {batch_id}；请先查看现有批次，避免重复计费")
    max_version = session.scalar(select(func.max(Generation.version)).where(Generation.project_id == project_id)) or 0
    batch_id = uuid4()
    batch_size = len(inputs.segments)
    rows: list[Generation] = []
    for index, (segment, fingerprint) in enumerate(zip(inputs.segments, fingerprints, strict=True)):
        version = max_version + index + 1
        rows.append(Generation(
            project_id=project_id, version=version, prompt_version=payload.prompt_version,
            generation_segment_id=segment.id, generation_batch_id=batch_id,
            batch_position=index + 1, batch_size=batch_size,
            provider=payload.provider, ratio=payload.ratio, duration=-1,
            generate_audio=generate_audio, status="queued",
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


def _batch_generation_rows(session: Session, project_id: UUID, batch_id: UUID) -> list[Generation]:
    """取批次全部行，每个位置取最新 Generation.version（retry 后旧版本被新版本替代）。"""
    rows = list(session.scalars(select(Generation).where(
        Generation.project_id == project_id,
        Generation.generation_batch_id == batch_id,
    ).order_by(Generation.batch_position, Generation.version.desc())))
    latest: dict[int, Generation] = {}
    for row in rows:
        latest.setdefault(row.batch_position or 0, row)
    return [latest[pos] for pos in sorted(latest)]


@router.get("", response_model=list[GenerationBatchResponse])
def list_generation_batches(project_id: UUID, session: Session = Depends(get_session)) -> list[GenerationBatchResponse]:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    batch_ids = session.scalars(
        select(Generation.generation_batch_id)
        .where(Generation.project_id == project_id, Generation.generation_batch_id.is_not(None))
        .distinct()
    ).all()
    batches: list[GenerationBatchResponse] = []
    for batch_id in batch_ids:
        rows = _batch_generation_rows(session, project_id, batch_id)
        if not rows:
            continue
        first = rows[0]
        batches.append(GenerationBatchResponse(
            generation_batch_id=batch_id, project_id=project_id, provider=first.provider,
            prompt_version=first.prompt_version, batch_size=len(rows),
            status=derive_batch_status([row.status for row in rows]),
            generations=[generation_response(project_id, row) for row in rows],
        ))
    # 最新批次优先：按任一行的 created_at 排序。
    batches.sort(key=lambda batch: batch.generations[0].created_at, reverse=True)
    return batches


@router.get("/{batch_id}", response_model=GenerationBatchResponse)
def get_generation_batch(project_id: UUID, batch_id: UUID, session: Session = Depends(get_session)) -> GenerationBatchResponse:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    rows = _batch_generation_rows(session, project_id, batch_id)
    if not rows:
        raise HTTPException(status_code=404, detail="批次不存在")
    first = rows[0]
    return GenerationBatchResponse(
        generation_batch_id=batch_id, project_id=project_id, provider=first.provider,
        prompt_version=first.prompt_version, batch_size=len(rows),
        status=derive_batch_status([row.status for row in rows]),
        generations=[generation_response(project_id, row) for row in rows],
    )


def _merged_generation_batch_path(
    project_id: UUID,
    batch_id: UUID,
    session: Session,
    *,
    prepare: bool,
) -> tuple[Path, Generation]:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    rows = _batch_generation_rows(session, project_id, batch_id)
    if not rows:
        raise HTTPException(status_code=404, detail="批次不存在")
    expected_positions = list(range(1, (rows[0].batch_size or len(rows)) + 1))
    if [row.batch_position for row in rows] != expected_positions or any(row.status != "completed" for row in rows):
        raise HTTPException(status_code=409, detail="全部片段完成后才能合并下载")
    if any(not row.result_path for row in rows):
        raise HTTPException(status_code=404, detail="部分生成片段文件不存在")

    allowed_root = (Settings().media_root / str(project_id) / "generated").resolve()
    sources = [Path(row.result_path).resolve() for row in rows if row.result_path]
    if any((allowed_root not in path.parents and path != allowed_root) or not path.is_file() for path in sources):
        raise HTTPException(status_code=404, detail="部分生成片段文件不可访问")
    destination = allowed_root / "batches" / str(batch_id) / f"complete-v{rows[0].prompt_version}.mp4"
    needs_merge = not destination.is_file() or destination.stat().st_mtime < max(path.stat().st_mtime for path in sources)
    if needs_merge:
        if not prepare:
            raise HTTPException(status_code=409, detail="完整视频尚未准备，请重新点击合成下载")
        try:
            concat_videos_lossless(sources, destination)
        except MediaToolUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
            raise HTTPException(status_code=422, detail=f"无损合并失败：{exc}") from exc
    return destination, rows[0]


@router.post("/{batch_id}/merged-content")
def prepare_merged_generation_batch(
    project_id: UUID,
    batch_id: UUID,
    session: Session = Depends(get_session),
) -> dict[str, bool]:
    _merged_generation_batch_path(project_id, batch_id, session, prepare=True)
    return {"ready": True}


@router.get("/{batch_id}/merged-content")
def get_merged_generation_batch(
    project_id: UUID,
    batch_id: UUID,
    session: Session = Depends(get_session),
) -> FileResponse:
    destination, first = _merged_generation_batch_path(project_id, batch_id, session, prepare=False)
    return FileResponse(
        destination,
        media_type="video/mp4",
        filename=f"complete-video-v{first.prompt_version}.mp4",
        content_disposition_type="attachment",
    )
