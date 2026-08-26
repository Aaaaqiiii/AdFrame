from uuid import UUID
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Asset, Generation, GenerationSegment, Project, PromptRevision, Shot, ShotEdit, TimelineRevision
from app.db.session import get_session
from app.services.product_compatibility import check_product_compatibility
from app.services.final_prompt import generation_prompt_contract_ready, person_replacement_contract_ready
from app.services.product_rules import confirmed_generation_product_assets
from app.services.reference_profiles import load_structure
from app.services.seedance import JsonTaskGateway, build_seedance_request

router = APIRouter(prefix="/api/projects/{project_id}/generations", tags=["generations"])


class CreateGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(pattern="^(volcengine|comfly)$")
    prompt_version: int = Field(gt=0)
    generation_segment_id: UUID
    generate_audio: bool = False
    # 兼容旧客户端；服务端始终根据提示词和已上传素材自动附加参考图。
    include_person_reference: bool = False
    include_background_reference: bool = False


class GenerationResponse(BaseModel):
    id: UUID
    version: int
    prompt_version: int
    generation_segment_id: UUID | None
    generation_batch_id: UUID | None = None
    batch_position: int | None = None
    batch_size: int | None = None
    provider: str
    status: str
    generate_audio: bool
    external_task_id: str | None
    attempts: int
    next_attempt_at: datetime | None
    result_url: str | None
    local_video_url: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


def generation_response(project_id: UUID, generation: Generation) -> GenerationResponse:
    return GenerationResponse(
        id=generation.id,
        version=generation.version,
        prompt_version=generation.prompt_version,
        generation_segment_id=generation.generation_segment_id,
        generation_batch_id=generation.generation_batch_id,
        batch_position=generation.batch_position,
        batch_size=generation.batch_size,
        provider=generation.provider,
        status=generation.status,
        generate_audio=generation.generate_audio,
        external_task_id=generation.external_task_id,
        attempts=generation.attempts,
        next_attempt_at=generation.next_attempt_at,
        result_url=generation.result_url,
        local_video_url=(
            f"/api/projects/{project_id}/generations/{generation.id}/content"
            if generation.result_path and Path(generation.result_path).is_file() else None
        ),
        error_message=generation.error_message,
        created_at=generation.created_at,
        completed_at=generation.completed_at,
    )


def _provider_gateway(settings: Settings, provider: str) -> tuple[JsonTaskGateway, str]:
    if provider == "volcengine":
        return JsonTaskGateway(settings.volcengine_seedance_base_url, settings.volcengine_api_key, settings.volcengine_seedance_task_path), settings.volcengine_seedance_model
    return JsonTaskGateway(settings.comfly_base_url, settings.comfly_api_key, settings.comfly_seedance_task_path), settings.comfly_seedance_model


def _require_provider_key(provider: str) -> None:
    settings = Settings()
    try:
        _provider_gateway(settings, provider)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _current_timeline_revision(session: Session, project_id: UUID) -> TimelineRevision | None:
    return session.scalar(select(TimelineRevision).where(TimelineRevision.project_id == project_id).order_by(TimelineRevision.version.desc()))


def _current_shots(session: Session, revision: TimelineRevision) -> list[Shot]:
    return list(session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.id).order_by(Shot.position)))


def _confirmed_product_assets(session: Session, project_id: UUID) -> list[Asset]:
    project = session.get(Project, project_id)
    return confirmed_generation_product_assets(session, project) if project else []


def _person_profile_confirmed(asset: Asset | None) -> bool:
    return bool(asset and asset.profile_user_edited)


def _asset_ids(assets: list[Asset]) -> str:
    return json.dumps([str(asset.id) for asset in assets], ensure_ascii=False)


def _require_batch_retry_inputs(session: Session, project_id: UUID, generation: Generation) -> None:
    """批次行重试校验：full prompt 当前有效 + 该段属于当前最新方案，不查活动重复（单段重试不受兄弟影响）。"""
    from app.services.full_prompt import FullPromptValidationError, validate_full_prompt
    current_revision = _current_timeline_revision(session, project_id)
    if current_revision is None or generation.generation_segment_id is None:
        raise HTTPException(status_code=422, detail="生成分段已过期，请重新确认分段")
    segment = session.get(GenerationSegment, generation.generation_segment_id)
    if segment is None or segment.project_id != project_id or segment.source_timeline_revision_id != current_revision.id:
        raise HTTPException(status_code=422, detail="生成分段已过期，请重新确认分段")
    latest_plan = session.scalar(
        select(func.max(GenerationSegment.plan_version)).where(
            GenerationSegment.project_id == project_id,
            GenerationSegment.source_timeline_revision_id == current_revision.id,
        )
    )
    if latest_plan is None or segment.plan_version != latest_plan:
        raise HTTPException(status_code=422, detail="生成分段已过期，请重新确认分段")
    if segment.source_end_sec - segment.source_start_sec > Settings().effective_segment_limit_seconds:
        raise HTTPException(status_code=422, detail="生成片段超过安全时长上限")
    prompt = session.scalar(select(PromptRevision).where(
        PromptRevision.project_id == project_id, PromptRevision.version == generation.prompt_version,
    ))
    if prompt is None or prompt.status != "completed" or not (prompt.text or "").strip():
        raise HTTPException(status_code=422, detail="请先生成或保存一份完整提示词")
    if prompt.prompt_mode != "full_reference_video_edit" or prompt.generation_segment_id is not None:
        raise HTTPException(status_code=422, detail="批次生成必须使用完整视频编辑提示词")
    if prompt.replace_person and not person_replacement_contract_ready(prompt.text):
        raise HTTPException(status_code=422, detail="当前人物替换提示词仍使用旧版冲突规则，请重新生成提示词后再提交视频")
    if not generation_prompt_contract_ready(prompt.text, prompt.visual_direction):
        raise HTTPException(status_code=422, detail="当前提示词仍使用旧版参考范围或文字清理规则，请重新生成提示词后再提交视频")
    if prompt.source_timeline_revision_id != current_revision.id:
        raise HTTPException(status_code=422, detail="提示词来自旧时间轴，请重新生成提示词")
    shots = _current_shots(session, current_revision)
    if not shots:
        raise HTTPException(status_code=422, detail="当前时间轴没有镜头")
    unconfirmed = [shot for shot in shots if not session.scalar(
        select(ShotEdit).where(ShotEdit.project_id == project_id, ShotEdit.shot_id == shot.id, ShotEdit.confirmed.is_(True))
    )]
    if unconfirmed:
        raise HTTPException(status_code=422, detail="时间轴存在未确认镜头，请先确认全部镜头")
    shot_ranges = [(shot.start_sec, shot.end_sec) for shot in shots]
    try:
        validate_full_prompt(prompt.text, shot_ranges)
    except FullPromptValidationError as exc:
        raise HTTPException(status_code=422, detail=f"完整提示词结构无效：{exc}") from exc


def _require_current_segment_prompt(session: Session, project_id: UUID, generation_segment_id: UUID, prompt_version: int):
    """创建与重试共用的只读校验：片段/提示词/时间轴/方案必须仍是当前最新且互相匹配。"""
    current_revision = _current_timeline_revision(session, project_id)
    segment = session.get(GenerationSegment, generation_segment_id)
    if segment is None or segment.project_id != project_id:
        raise HTTPException(status_code=422, detail="生成分段已过期，请重新确认分段")
    if current_revision is None or segment.source_timeline_revision_id != current_revision.id:
        raise HTTPException(status_code=422, detail="生成分段已过期，请重新确认分段")
    latest_plan = session.scalar(
        select(func.max(GenerationSegment.plan_version)).where(
            GenerationSegment.project_id == project_id,
            GenerationSegment.source_timeline_revision_id == current_revision.id,
        )
    )
    if latest_plan is None or segment.plan_version != latest_plan:
        raise HTTPException(status_code=422, detail="生成分段已过期，请重新确认分段")
    prompt = session.scalar(select(PromptRevision).where(PromptRevision.project_id == project_id, PromptRevision.version == prompt_version))
    if prompt is None or prompt.status != "completed" or not (prompt.text or "").strip():
        raise HTTPException(status_code=422, detail="请先生成或保存一份完整提示词")
    if prompt.prompt_mode != "reference_video_edit" or prompt.generation_segment_id != segment.id:
        raise HTTPException(status_code=422, detail="编辑指令与生成片段不匹配")
    if current_revision is None or prompt.source_timeline_revision_id != current_revision.id:
        raise HTTPException(status_code=422, detail="提示词来自旧时间轴，请重新生成提示词")
    if segment.source_end_sec - segment.source_start_sec > Settings().effective_segment_limit_seconds:
        raise HTTPException(status_code=422, detail="生成片段超过安全时长上限")
    shots = _current_shots(session, current_revision)
    if not shots:
        raise HTTPException(status_code=422, detail="当前时间轴没有镜头")
    unconfirmed = [shot for shot in shots if not session.scalar(select(ShotEdit).where(ShotEdit.project_id == project_id, ShotEdit.shot_id == shot.id, ShotEdit.confirmed.is_(True)))]
    if unconfirmed:
        raise HTTPException(status_code=422, detail="时间轴存在未确认镜头，请先确认全部镜头")
    return segment, prompt, current_revision, shots


@router.post("", response_model=GenerationResponse, status_code=status.HTTP_202_ACCEPTED)
def create_generation(project_id: UUID, payload: CreateGenerationRequest, session: Session = Depends(get_session)) -> GenerationResponse:
    project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(status_code=404, detail="Project does not exist")
    # 稳定校验顺序（含片段/时间轴/方案/提示词/镜头确认）。
    segment, prompt, current_revision, shots = _require_current_segment_prompt(
        session, project_id, payload.generation_segment_id, payload.prompt_version,
    )
    if prompt.replace_person and not person_replacement_contract_ready(prompt.text):
        raise HTTPException(status_code=422, detail="当前人物替换提示词仍使用旧版冲突规则，请重新生成提示词后再提交视频")
    video = session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc()))
    if video is None:
        raise HTTPException(status_code=422, detail="请先上传参考视频")
    # 6. provider key 和模式相关引用有效。
    _require_provider_key(payload.provider)

    assets: list[Asset] = [video]
    prompt_reference_assets: list[Asset] = []
    if project.mode == "replace_product":
        product_assets = _confirmed_product_assets(session, project_id)
        if not product_assets:
            raise HTTPException(status_code=422, detail="页面二生成前必须上传目标产品图，并确认完整的目标产品文字档案。")
        profile = product_assets[-1].profile_text.strip()
        _, conflicts = check_product_compatibility(profile, shots)
        if conflicts:
            raise HTTPException(status_code=422, detail={
                "message": "目标产品形态与部分原镜头动作不兼容，请先修正这些镜头",
                "shot_ids": [conflict["shot_id"] for conflict in conflicts],
                "conflicts": conflicts,
            })
        assets.extend(product_assets)
        prompt_reference_assets.extend(product_assets)
    person = session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == "person_reference_image").order_by(Asset.id.desc()))
    if prompt.replace_person:
        if person is None or not _person_profile_confirmed(person):
            raise HTTPException(status_code=422, detail="选择替换人物前，请先确认人物文字档案或人物图片档案")
    if person is not None:
        prompt_reference_assets.append(person)
        if (person.original_path or "").strip():
            assets.append(person)
    background = session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == "background_reference_image").order_by(Asset.id.desc()))
    if background is not None:
        assets.append(background)
        prompt_reference_assets.append(background)
    if prompt.reference_asset_ids:
        try:
            frozen_ids = [UUID(value) for value in json.loads(prompt.reference_asset_ids)]
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="提示词参考素材快照损坏，请重新生成提示词") from exc
        frozen_assets = [asset for asset_id in frozen_ids if (asset := session.get(Asset, asset_id)) is not None and asset.project_id == project_id]
        if len(frozen_assets) != len(frozen_ids):
            raise HTTPException(status_code=422, detail="提示词引用的参考素材已不存在，请重新生成提示词")
        if {asset.id for asset in frozen_assets} != {asset.id for asset in prompt_reference_assets}:
            raise HTTPException(status_code=422, detail="人物、产品或背景参考素材已更新，请重新生成或保存提示词后再提交视频")
        assets = [video, *(asset for asset in frozen_assets if (asset.original_path or "").strip())]

    generate_audio = prompt.audio_mode in {"auto", "custom", "add_style"}
    fingerprint_payload = {
        "project_id": str(project.id),
        "timeline_revision_id": str(current_revision.id),
        "segment_id": str(segment.id),
        "segment_plan_version": segment.plan_version,
        "segment_start_sec": segment.source_start_sec,
        "segment_end_sec": segment.source_end_sec,
        "prompt_revision_id": str(prompt.id),
        "provider": payload.provider,
        "generate_audio": generate_audio,
        "asset_ids": [str(item.id) for item in assets],
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    active_statuses = ("queued", "processing", "retryable", "submission_uncertain")
    duplicate = session.scalar(select(Generation).where(Generation.project_id == project_id, Generation.submission_fingerprint == fingerprint, Generation.status.in_(active_statuses)))
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="相同输入和设置的生成任务已存在")
    # 旧单端点不得与活动批次任务冲突：该段已有活动 batch 行 → 409。
    active_batch = session.scalar(select(Generation).where(
        Generation.project_id == project_id,
        Generation.generation_segment_id == segment.id,
        Generation.generation_batch_id.is_not(None),
        Generation.status.in_(active_statuses),
    ).limit(1))
    if active_batch is not None:
        raise HTTPException(status_code=409, detail="该生成片段已有活动批次任务")

    version = (session.scalar(select(func.max(Generation.version)).where(Generation.project_id == project_id)) or 0) + 1
    generation = Generation(
        project_id=project_id,
        version=version,
        prompt_version=payload.prompt_version,
        generation_segment_id=segment.id,
        provider=payload.provider,
        ratio="adaptive",
        duration=-1,
        generate_audio=generate_audio,
        status="queued",
        reference_asset_ids=_asset_ids(assets),
        submission_fingerprint=fingerprint,
    )
    session.add(generation)
    session.commit()
    return generation_response(project_id, generation)


@router.get("", response_model=list[GenerationResponse])
def list_generations(project_id: UUID, session: Session = Depends(get_session)) -> list[GenerationResponse]:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project does not exist")
    generations = session.scalars(
        select(Generation).where(Generation.project_id == project_id).order_by(Generation.version.desc())
    ).all()
    return [generation_response(project_id, generation) for generation in generations]


@router.get("/{generation_id}", response_model=GenerationResponse)
def get_generation(project_id: UUID, generation_id: UUID, session: Session = Depends(get_session)) -> GenerationResponse:
    generation = session.get(Generation, generation_id)
    if generation is None or generation.project_id != project_id:
        raise HTTPException(status_code=404, detail="Generation does not exist")
    return generation_response(project_id, generation)


class ResolveGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["attach_task", "confirm_not_created"]
    external_task_id: str | None = Field(default=None, max_length=255)


@router.post("/{generation_id}/retry", response_model=GenerationResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_generation(project_id: UUID, generation_id: UUID, session: Session = Depends(get_session)) -> GenerationResponse:
    project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(status_code=404, detail="Project does not exist")
    generation = session.get(Generation, generation_id)
    if generation is None or generation.project_id != project_id:
        raise HTTPException(status_code=404, detail="Generation does not exist")
    if generation.status not in {"failed"}:
        raise HTTPException(status_code=422, detail="只有失败的生成任务可以重试")
    # 分段任务重试必须重新验证片段/提示词/时间轴/方案仍是当前最新且互相匹配，
    # 避免时间轴更新后仍提交旧裁片。
    if generation.generation_batch_id is not None:
        # 批次行重试：校验 full prompt 仍是当前完整提示词 + 该段属于当前最新方案。
        _require_batch_retry_inputs(session, project_id, generation)
    elif generation.generation_segment_id:
        _require_current_segment_prompt(
            session, project_id, generation.generation_segment_id, generation.prompt_version,
        )
    if "reference video duration could not be read" in (generation.error_message or "").lower():
        # 已被供应商拒绝的公网对象不能在新任务中继续复用；片段和整段两条路径都清理。
        segment = session.get(GenerationSegment, generation.generation_segment_id) if generation.generation_segment_id else None
        if segment is not None:
            segment.public_url = None
            segment.public_url_expires_at = None
        try:
            video_id = UUID(json.loads(generation.reference_asset_ids or "[]")[0])
        except (IndexError, TypeError, ValueError, json.JSONDecodeError):
            video_id = None
        video = session.get(Asset, video_id) if video_id else None
        if video is not None and video.kind == "reference_video":
            video.public_url = None
            video.public_url_expires_at = None
    version = (session.scalar(select(func.max(Generation.version)).where(Generation.project_id == project_id)) or 0) + 1
    retried = Generation(
        project_id=project_id,
        version=version,
        prompt_version=generation.prompt_version,
        generation_segment_id=generation.generation_segment_id,
        generation_batch_id=generation.generation_batch_id,
        batch_position=generation.batch_position,
        batch_size=generation.batch_size,
        provider=generation.provider,
        ratio=generation.ratio,
        duration=generation.duration,
        generate_audio=generation.generate_audio,
        status="queued",
        reference_asset_ids=generation.reference_asset_ids,
        request_snapshot=generation.request_snapshot,
        submission_fingerprint=generation.submission_fingerprint,
    )
    session.add(retried)
    session.commit()
    return generation_response(project_id, retried)


@router.post("/{generation_id}/resolve", response_model=GenerationResponse)
def resolve_generation(project_id: UUID, generation_id: UUID, payload: ResolveGenerationRequest, session: Session = Depends(get_session)) -> GenerationResponse:
    generation = session.get(Generation, generation_id)
    if generation is None or generation.project_id != project_id:
        raise HTTPException(status_code=404, detail="Generation does not exist")
    if generation.status != "submission_uncertain":
        raise HTTPException(status_code=422, detail="只有不确定的提交任务可以人工解析")
    if payload.action == "attach_task":
        task_id = (payload.external_task_id or "").strip()
        if not task_id:
            raise HTTPException(status_code=422, detail="必须提供非空的供应商任务 ID")
        generation.external_task_id = task_id
        generation.status = "processing"
    else:
        generation.status = "failed"
        generation.error_message = "用户确认供应商未创建任务"
    generation.leased_at = None
    generation.leased_by = None
    generation.next_attempt_at = None
    session.commit()
    return generation_response(project_id, generation)


@router.get("/{generation_id}/content")
def get_generated_video(
    project_id: UUID,
    generation_id: UUID,
    download: bool = Query(False),
    session: Session = Depends(get_session),
) -> FileResponse:
    generation = session.get(Generation, generation_id)
    if generation is None or generation.project_id != project_id:
        raise HTTPException(status_code=404, detail="Generation does not exist")
    if generation.status != "completed" or not generation.result_path:
        raise HTTPException(status_code=404, detail="Generated video is not available")
    path = Path(generation.result_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Generated video file is missing")
    allowed_root = (Settings().media_root / str(project_id) / "generated").resolve()
    resolved = path.resolve()
    if allowed_root not in resolved.parents and resolved != allowed_root:
        raise HTTPException(status_code=404, detail="Generated video file is not accessible")
    if download:
        # attachment：文件名含批次位置，便于按段下载。
        position = generation.batch_position or generation.version
        filename = f"segment-{position}.mp4"
        return FileResponse(path, media_type="video/mp4", filename=filename, content_disposition_type="attachment")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
