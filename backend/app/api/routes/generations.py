from uuid import UUID
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Asset, Generation, Project, PromptRevision, Shot, ShotEdit, TimelineRevision
from app.db.session import get_session
from app.services.product_compatibility import check_product_compatibility
from app.services.reference_profiles import load_structure
from app.services.seedance import JsonTaskGateway, build_seedance_request

router = APIRouter(prefix="/api/projects/{project_id}/generations", tags=["generations"])


class CreateGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(pattern="^(volcengine|comfly)$")
    prompt_version: int = Field(gt=0)
    generate_audio: bool = False
    include_person_reference: bool = False
    include_background_reference: bool = False


class GenerationResponse(BaseModel):
    id: UUID
    version: int
    prompt_version: int
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


def _person_profile_confirmed(asset: Asset | None) -> bool:
    return bool(asset and asset.profile_user_edited)


def _asset_ids(assets: list[Asset]) -> str:
    return json.dumps([str(asset.id) for asset in assets], ensure_ascii=False)


@router.post("", response_model=GenerationResponse, status_code=status.HTTP_202_ACCEPTED)
def create_generation(project_id: UUID, payload: CreateGenerationRequest, session: Session = Depends(get_session)) -> GenerationResponse:
    project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(status_code=404, detail="Project does not exist")
    prompt = session.scalar(select(PromptRevision).where(PromptRevision.project_id == project_id, PromptRevision.version == payload.prompt_version))
    if prompt is None or prompt.status != "completed" or not (prompt.text or "").strip():
        raise HTTPException(status_code=422, detail="请先生成或保存一份完整提示词")
    current_revision = _current_timeline_revision(session, project_id)
    if current_revision is None or prompt.source_timeline_revision_id != current_revision.id:
        raise HTTPException(status_code=422, detail="提示词来自旧时间轴，请重新生成提示词")
    shots = _current_shots(session, current_revision)
    if not shots:
        raise HTTPException(status_code=422, detail="当前时间轴没有镜头")
    unconfirmed = [shot for shot in shots if not session.scalar(select(ShotEdit).where(ShotEdit.project_id == project_id, ShotEdit.shot_id == shot.id, ShotEdit.confirmed.is_(True)))]
    if unconfirmed:
        raise HTTPException(status_code=422, detail="时间轴存在未确认镜头，请先确认全部镜头")

    video = session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc()))
    if video is None:
        raise HTTPException(status_code=422, detail="请先上传参考视频")
    if video.duration_sec is not None and video.duration_sec > 30:
        raise HTTPException(status_code=422, detail="参考视频超过 30 秒，Seedance 提交前请先裁剪或更换视频")

    _require_provider_key(payload.provider)

    assets: list[Asset] = [video]
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
    if prompt.replace_person:
        person = session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == "person_reference_image").order_by(Asset.id.desc()))
        if person is None or not _person_profile_confirmed(person):
            raise HTTPException(status_code=422, detail="选择替换人物前，请先上传并确认人物图片档案")
        if not payload.include_person_reference:
            raise HTTPException(status_code=422, detail="提示词已开启人物替换，必须包含人物参考图")
        assets.append(person)
    elif payload.include_person_reference:
        raise HTTPException(status_code=422, detail="提示词未开启人物替换，不能包含人物参考图")
    if payload.include_background_reference:
        background = session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == "background_reference_image").order_by(Asset.id.desc()))
        if background is None:
            raise HTTPException(status_code=422, detail="请求包含背景参考图，但项目没有背景参考图素材")
        assets.append(background)

    fingerprint_payload = {
        "project_id": str(project.id),
        "timeline_revision_id": str(current_revision.id),
        "prompt_revision_id": str(prompt.id),
        "provider": payload.provider,
        "generate_audio": payload.generate_audio,
        "asset_ids": [str(item.id) for item in assets],
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    active_statuses = ("queued", "processing", "retryable", "submission_uncertain")
    duplicate = session.scalar(select(Generation).where(Generation.project_id == project_id, Generation.submission_fingerprint == fingerprint, Generation.status.in_(active_statuses)))
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="相同输入和设置的生成任务已存在")

    version = (session.scalar(select(func.max(Generation.version)).where(Generation.project_id == project_id)) or 0) + 1
    generation = Generation(
        project_id=project_id,
        version=version,
        prompt_version=payload.prompt_version,
        provider=payload.provider,
        ratio="adaptive",
        duration=-1,
        generate_audio=payload.generate_audio,
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
    version = (session.scalar(select(func.max(Generation.version)).where(Generation.project_id == project_id)) or 0) + 1
    retried = Generation(
        project_id=project_id,
        version=version,
        prompt_version=generation.prompt_version,
        provider=generation.provider,
        ratio="adaptive",
        duration=-1,
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
def get_generated_video(project_id: UUID, generation_id: UUID, session: Session = Depends(get_session)) -> FileResponse:
    generation = session.get(Generation, generation_id)
    if generation is None or generation.project_id != project_id or not generation.result_path:
        raise HTTPException(status_code=404, detail="Generated video is not available")
    from pathlib import Path
    path = Path(generation.result_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Generated video file is missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
