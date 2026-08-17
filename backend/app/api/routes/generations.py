from uuid import UUID
import json
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Asset, Generation, Project, PromptRevision
from app.db.session import get_session
from app.services.seedance import JsonTaskGateway, build_seedance_request
from app.services.tempfile_publisher import TempfilePublisher

router = APIRouter(prefix="/api/projects/{project_id}/generations", tags=["generations"])


class CreateGenerationRequest(BaseModel):
    provider: str = Field(pattern="^(volcengine|comfly)$")
    prompt_version: int = Field(gt=0)
    ratio: str = Field(default="adaptive")
    duration: int = Field(default=-1, ge=-1, le=30)
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


def _publish_if_expired(asset: Asset) -> str:
    expires_at = datetime.fromisoformat(asset.public_url_expires_at) if asset.public_url_expires_at else None
    # Older local projects predate expiry metadata. Keep their existing URL usable;
    # only a known expired link is republished.
    if asset.public_url and (expires_at is None or expires_at > datetime.now(timezone.utc)):
        return asset.public_url
    published = TempfilePublisher().publish(__import__("pathlib").Path(asset.original_path), asset.content_type or "application/octet-stream")
    asset.public_url, asset.public_url_expires_at = published.url, published.expires_at.isoformat()
    return asset.public_url


@router.post("", response_model=GenerationResponse, status_code=status.HTTP_202_ACCEPTED)
def create_generation(project_id: UUID, payload: CreateGenerationRequest, session: Session = Depends(get_session)) -> Generation:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project does not exist")
    if project.mode == "replace_product":
        product = session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == "product_reference_image").order_by(Asset.id.desc()))
        if product is None or not product.profile_text or product.analysis_status not in {"succeeded", "completed"}:
            raise HTTPException(status_code=422, detail="页面二生成前必须上传目标产品图，并确认完整的目标产品文字档案。")
    prompt = session.scalar(select(PromptRevision).where(PromptRevision.project_id == project_id, PromptRevision.version == payload.prompt_version))
    asset = session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc()))
    if prompt is None or asset is None:
        raise HTTPException(status_code=422, detail="Save a prompt and upload the reference video before generation")
    if asset.duration_sec is not None and asset.duration_sec > 30:
        raise HTTPException(status_code=422, detail="参考视频超过 30 秒，Seedance 提交前请先裁剪或更换视频")
    try:
        _provider_gateway(Settings(), payload.provider)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        _publish_if_expired(asset)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Reference video publishing failed") from exc
    version = (session.scalar(select(func.max(Generation.version)).where(Generation.project_id == project_id)) or 0) + 1
    image_urls: list[str] = []
    selected_kinds = []
    if payload.include_person_reference:
        selected_kinds.append("person_reference_image")
    if payload.include_background_reference:
        selected_kinds.append("background_reference_image")
    for kind in selected_kinds:
        image = session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == kind).order_by(Asset.id.desc()))
        if image is None:
            continue
        try:
            image_urls.append(_publish_if_expired(image))
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Reference image publishing failed") from exc
    generation = Generation(
        project_id=project_id,
        version=version,
        prompt_version=payload.prompt_version,
        provider=payload.provider,
        ratio="adaptive",
        duration=-1,
        generate_audio=payload.generate_audio,
        reference_image_urls=json.dumps(image_urls),
        status="queued",
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
