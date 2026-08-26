import shutil
import mimetypes
import json
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Asset, Generation, GenerationSegment, Job, Project, PromptRevision, Shot, ShotAISummary, ShotEdit, ShotEvidence, TimelineRevision, VideoAnalysis
from app.services.final_prompt import actionable_segment_text, build_full_prompt_prefix, expected_segment_labels, missing_segment_prompt_blocks, normalize_full_prompt_contract, validate_recreation_prompt
from app.services.full_prompt import FullPromptValidationError, parse_full_prompt, validate_full_prompt
from app.db.session import get_session
from app.services.media import MediaToolUnavailableError, probe_video
from app.services.tempfile_publisher import TempfilePublisher
from app.services.reference_profiles import (
    build_selling_point_cards,
    load_structure,
    product_prompt_profile,
    product_selling_point_cards,
    queue_profile_job,
)
from app.services.product_compatibility import check_product_compatibility
from app.services.product_rules import confirmed_target_product_assets, contains_product_replacement

router = APIRouter(prefix="/api/projects", tags=["projects"])
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
PRODUCT_VIEW_LABELS = {"front", "left", "right", "back", "top", "bottom", "packaging", "logo", "opening", "detail", "other"}


def _next_prompt_version(session: Session, project_id: UUID) -> int:
    session.execute(select(Project).where(Project.id == project_id).with_for_update())
    return (session.scalar(
        select(func.max(PromptRevision.version)).where(PromptRevision.project_id == project_id)
    ) or 0) + 1


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mode: str = Field(default="preserve_product", pattern="^(preserve_product|replace_product)$")


class UpdateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    mode: str


class ProjectListResponse(ProjectResponse):
    reference_video_name: str


class ReferenceImageDetails(BaseModel):
    id: UUID
    filename: str
    image_url: str
    status: str
    view_label: str = "other"
    display_name: str = ""
    note: str = ""


class UpdateReferenceImageRequest(BaseModel):
    view_label: str
    display_name: str = Field(min_length=1, max_length=40)


@router.get("", response_model=list[ProjectListResponse])
def list_projects(
    mode: str | None = None,
    session: Session = Depends(get_session),
) -> list[ProjectListResponse]:
    # 历史项目只展示真实视频；旧测试占位文件和已知测试项目仍保留在数据库中。
    has_reference_video = select(Asset.id).where(
        Asset.project_id == Project.id,
        Asset.kind == "reference_video",
        Asset.original_filename.is_not(None),
        Asset.size_bytes > 100_000,
    ).exists()
    query = select(Project).where(
        has_reference_video,
        Project.name.not_in(["image reference", "??????"]),
    )
    if mode is not None:
        if mode not in {"preserve_product", "replace_product"}:
            raise HTTPException(status_code=422, detail="未知的项目模式")
        query = query.where(Project.mode == mode)
    projects = list(session.scalars(query.order_by(Project.created_at.desc()).limit(50)))
    return [ProjectListResponse(
        id=project.id,
        name=project.name,
        mode=project.mode,
        reference_video_name=session.scalar(select(Asset.original_filename).where(
            Asset.project_id == project.id,
            Asset.kind == "reference_video",
            Asset.size_bytes > 100_000,
        ).order_by(Asset.id.desc())) or "参考视频",
    ) for project in projects]


class ProjectDetailsResponse(ProjectResponse):
    reference_video_name: str | None
    reference_video_url: str | None
    person_reference_image_name: str | None
    person_profile: str | None = None
    person_analysis_status: str | None = None
    person_analysis_error: str | None = None
    background_reference_image_name: str | None
    product_reference_image_name: str | None
    product_reference_image_url: str | None
    product_reference_images: list[ReferenceImageDetails] = Field(default_factory=list)
    product_profile: str | None
    product_analysis_status: str | None
    product_analysis_error: str | None
    product_name: str = ""
    product_category: str = ""
    product_package_form: str = ""
    product_selling_points: str = ""
    product_profile_confirmed: bool = False
    target_product_reference_image_name: str | None
    target_product_reference_images: list[ReferenceImageDetails] = Field(default_factory=list)
    target_product_profile: str | None
    target_product_analysis_status: str | None
    target_product_analysis_error: str | None
    latest_prompt_version: int = 0
    latest_prompt_text: str = ""
    prompt_visual_direction: str = ""
    prompt_audio_mode: str = "keep_original"
    prompt_audio_style: str = ""
    prompt_replace_product: bool = False
    prompt_replace_person: bool = False
    timeline: "TimelineDetails | None"
    generations: list["GenerationDetails"]


class TimelineShotDetails(BaseModel):
    id: UUID
    start_sec: float
    end_sec: float
    people: str | None
    action: str | None
    product: str | None
    product_interaction: str | None
    background: str | None
    camera: str | None
    lighting: str | None
    visual_style: str | None
    keep_unchanged: str | None
    on_screen_text: str | None
    observations: str | None
    inferences: str | None
    uncertainties: str | None
    analysis_status: str
    analysis_error: str | None
    evidence: list["EvidenceDetails"]
    edit: "ShotEditResponse | None"
    ai_summary_version: int = 0
    has_new_ai_summary: bool = False


class EvidenceDetails(BaseModel):
    timestamp_sec: float
    image_url: str


class TimelineDetails(BaseModel):
    revision_id: UUID
    source: str
    shots: list[TimelineShotDetails]


class GenerationDetails(BaseModel):
    id: UUID
    version: int
    status: str
    provider: str
    result_url: str | None
    local_video_url: str | None


class UploadReferenceVideoResponse(BaseModel):
    asset_kind: str
    job_kind: str


class PublishReferenceResponse(BaseModel):
    url: str
    expires_at: str
    notice: str


class PromptValidationRequest(BaseModel):
    text: str = Field(min_length=1)


class CreatePromptRequest(BaseModel):
    visual_direction: str = Field(default="", max_length=8000)
    prompt_text: str = Field(default="", max_length=200_000)
    audio_mode: str = "keep_original"
    audio_style: str = Field(default="", max_length=500)
    replace_product: bool = False
    replace_person: bool = False
    use_ai: bool = True
    prompt_mode: Literal["full_reference_video_edit", "standalone_video_recreation"] = "full_reference_video_edit"
    generation_segment_id: UUID | None = None


class RefinePromptRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=8000)
    source_version: int | None = Field(default=None, ge=1)


class ShotEditRequest(BaseModel):
    people: str = Field(default="", max_length=2000)
    action: str = Field(default="", max_length=2000)
    product: str = Field(default="", max_length=2000)
    product_interaction: str = Field(default="", max_length=2000)
    background: str = Field(default="", max_length=2000)
    camera: str = Field(default="", max_length=2000)
    lighting: str = Field(default="", max_length=2000)
    visual_style: str = Field(default="", max_length=2000)
    visible_text: str = Field(default="", max_length=2000)
    uncertainties: str = Field(default="", max_length=2000)
    keep_unchanged: list[str] = Field(default_factory=list, max_length=20)
    confirmed: bool = False


class ShotEditResponse(ShotEditRequest):
    shot_id: UUID
    version: int
    ai_summary_version: int = 0


class PromptResponse(BaseModel):
    version: int
    text: str
    status: str = "completed"
    adaptation_count: int = 0
    adaptation_conflicts: list[dict] = Field(default_factory=list)


class PromptRevisionSummary(BaseModel):
    id: UUID
    version: int
    text: str
    status: str
    source_timeline_revision_id: UUID | None
    prompt_mode: str = "full_video_description"
    generation_segment_id: UUID | None = None
    replace_product: bool
    replace_person: bool
    visual_direction: str
    audio_mode: str
    audio_style: str
    created_at: datetime


class AdoptAISummaryResponse(BaseModel):
    version: int
    content: dict


class ReferenceProfileUpdate(BaseModel):
    profile: str = Field(min_length=1, max_length=12000)
    structure: dict = Field(default_factory=dict)


class ReferenceProfileResponse(BaseModel):
    asset_id: UUID | None
    kind: str
    status: str
    profile: str | None
    structure: dict
    error: str | None
    user_edited: bool


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(payload: CreateProjectRequest, session: Session = Depends(get_session)) -> Project:
    project = Project(name=payload.name.strip(), mode=payload.mode)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectDetailsResponse)
def get_project(project_id: UUID, session: Session = Depends(get_session)) -> ProjectDetailsResponse:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    asset = session.scalar(
        select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc())
    )
    revision = session.scalar(
        select(TimelineRevision)
        .where(TimelineRevision.project_id == project_id)
        .order_by(TimelineRevision.version.desc())
    )
    if revision and revision.source == "reference_replaced":
        revision = None
    timeline = None
    if revision:
        shots = session.scalars(
            select(Shot).where(Shot.timeline_revision_id == revision.id).order_by(Shot.position)
        ).all()
        shot_details = []
        for shot in shots:
            edit = _shot_edit_response(session, project_id, shot.id)
            latest_ai = session.scalar(select(func.max(ShotAISummary.version)).where(ShotAISummary.shot_id == shot.id)) or 0
            shot_details.append(TimelineShotDetails(
                id=shot.id, start_sec=shot.start_sec, end_sec=shot.end_sec,
                people=shot.people, action=shot.action, product=shot.product, product_interaction=shot.product_interaction,
                background=shot.background, camera=shot.camera, lighting=shot.lighting,
                visual_style=shot.visual_style, keep_unchanged=shot.keep_unchanged,
                on_screen_text=shot.on_screen_text, observations=shot.observations,
                inferences=shot.inferences, uncertainties=shot.uncertainties,
                analysis_status=shot.analysis_status or "pending", analysis_error=shot.analysis_error,
                evidence=[EvidenceDetails(timestamp_sec=item.timestamp_sec, image_url=f"/api/projects/{project_id}/evidence/{item.id}/content") for item in shot.evidence],
                edit=edit, ai_summary_version=latest_ai,
                has_new_ai_summary=bool(edit and latest_ai > edit.ai_summary_version),
            ))
        timeline = TimelineDetails(
            revision_id=revision.id,
            source=revision.source,
            shots=shot_details,
        )
    person_asset = _reference_image(session, project_id, "person_reference_image")
    product_assets = _reference_images(session, project_id, "product_reference_image")
    product_asset = product_assets[-1] if product_assets else None
    target_product_assets = _reference_images(session, project_id, "target_product_reference_image")
    target_product_asset = target_product_assets[-1] if target_product_assets else None
    latest_prompt = session.scalar(select(PromptRevision).where(
        PromptRevision.project_id == project_id,
        PromptRevision.source_timeline_revision_id == revision.id,
        PromptRevision.status == "completed",
        PromptRevision.text != "",
    ).order_by(PromptRevision.version.desc())) if revision else None
    return ProjectDetailsResponse(
        id=project.id,
        name=project.name,
        mode=project.mode or "preserve_product",
        reference_video_name=asset.original_filename if asset else None,
        reference_video_url=f"/api/projects/{project.id}/reference-video/content" if asset else None,
        person_reference_image_name=_reference_image_name(session, project_id, "person_reference_image"),
        person_profile=person_asset.profile_text if person_asset else None,
        person_analysis_status=person_asset.analysis_status if person_asset else None,
        person_analysis_error=person_asset.analysis_error if person_asset else None,
        background_reference_image_name=_reference_image_name(session, project_id, "background_reference_image"),
        product_reference_image_name=_reference_image_name(session, project_id, "product_reference_image"),
        product_reference_image_url=f"/api/projects/{project_id}/reference-images/product/content" if product_asset else None,
        product_reference_images=[ReferenceImageDetails(
            id=item.id,
            filename=item.original_filename or Path(item.original_path).name,
            image_url=f"/api/projects/{project_id}/reference-images/product/content/{item.id}",
            status=_api_analysis_status(item.analysis_status),
            view_label=str(load_structure(item).get("view_label") or "other"),
            display_name=str(load_structure(item).get("display_name") or ""),
            note=str(load_structure(item).get("note") or ""),
        ) for item in product_assets],
        product_profile=_combined_reference_profile(product_assets),
        product_analysis_status=_combined_analysis_status(product_assets),
        product_analysis_error="\n".join(item.analysis_error for item in product_assets if item.analysis_error) or None,
        product_name=str((load_structure(product_asset) if product_asset else {}).get("product_name") or ""),
        product_category=str((load_structure(product_asset) if product_asset else {}).get("product_category") or ""),
        product_package_form=str((load_structure(product_asset) if product_asset else {}).get("package_form") or ""),
        product_selling_points=str((load_structure(product_asset) if product_asset else {}).get("selling_points") or ""),
        product_profile_confirmed=_product_profile_confirmed(product_assets),
        target_product_reference_image_name=target_product_asset.original_filename if target_product_asset else None,
        target_product_reference_images=[ReferenceImageDetails(
            id=item.id,
            filename=item.original_filename or Path(item.original_path).name,
            image_url=f"/api/projects/{project_id}/reference-images/target_product/content/{item.id}",
            status=_api_analysis_status(item.analysis_status),
            view_label=str(load_structure(item).get("view_label") or "other"),
            display_name=str(load_structure(item).get("display_name") or ""),
            note=str(load_structure(item).get("note") or ""),
        ) for item in target_product_assets],
        target_product_profile=_combined_reference_profile(target_product_assets),
        target_product_analysis_status=_combined_analysis_status(target_product_assets),
        target_product_analysis_error="\n".join(item.analysis_error for item in target_product_assets if item.analysis_error) or None,
        latest_prompt_version=latest_prompt.version if latest_prompt else 0,
        latest_prompt_text=latest_prompt.text if latest_prompt else "",
        prompt_visual_direction=latest_prompt.visual_direction if latest_prompt else "",
        prompt_audio_mode=latest_prompt.audio_mode if latest_prompt else "keep_original",
        prompt_audio_style=latest_prompt.audio_style if latest_prompt else "",
        prompt_replace_product=bool(latest_prompt and latest_prompt.replace_product),
        prompt_replace_person=bool(latest_prompt and latest_prompt.replace_person),
        timeline=timeline,
        generations=[GenerationDetails(id=item.id, version=item.version, status=item.status, provider=item.provider, result_url=item.result_url, local_video_url=f"/api/projects/{project.id}/generations/{item.id}/content" if item.result_path else None) for item in sorted(project.generations, key=lambda item: item.version, reverse=True)],
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    payload: UpdateProjectRequest,
    session: Session = Depends(get_session),
) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="项目名称不能为空")
    project.name = name
    session.commit()
    session.refresh(project)
    return project


@router.delete("/{project_id}")
def delete_project(project_id: UUID, session: Session = Depends(get_session)) -> dict[str, bool]:
    project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    active_job = session.scalar(select(Job.id).where(
        Job.project_id == project_id,
        Job.kind != "extract_media",
        Job.status.in_(["queued", "uploaded", "processing", "retryable"]),
    ).limit(1))
    active_generation = session.scalar(select(Generation.id).where(
        Generation.project_id == project_id,
        Generation.status.in_(["queued", "processing", "retryable", "submission_uncertain"]),
    ).limit(1))
    if active_job is not None or active_generation is not None:
        raise HTTPException(status_code=409, detail="项目仍有活动任务，请等待任务结束或处理状态不确定的生成任务后再删除")

    media_root = Settings().media_root.resolve()
    project_dir = (media_root / str(project_id)).resolve()
    if project_dir.parent != media_root:
        raise HTTPException(status_code=500, detail="项目媒体目录不安全，已拒绝删除")
    trash_dir: Path | None = None
    if project_dir.is_dir():
        trash_dir = media_root / ".trash" / f"{project_id}-{uuid4().hex}"
        trash_dir.parent.mkdir(parents=True, exist_ok=True)
        project_dir.replace(trash_dir)

    timeline_ids = select(TimelineRevision.id).where(TimelineRevision.project_id == project_id)
    shot_ids = select(Shot.id).where(Shot.timeline_revision_id.in_(timeline_ids))
    try:
        session.execute(delete(VideoAnalysis).where(VideoAnalysis.project_id == project_id))
        session.execute(delete(Generation).where(Generation.project_id == project_id))
        session.execute(delete(Job).where(Job.project_id == project_id))
        session.execute(delete(PromptRevision).where(PromptRevision.project_id == project_id))
        session.execute(delete(GenerationSegment).where(GenerationSegment.project_id == project_id))
        session.execute(delete(ShotAISummary).where(ShotAISummary.shot_id.in_(shot_ids)))
        session.execute(delete(ShotEvidence).where(ShotEvidence.shot_id.in_(shot_ids)))
        session.execute(delete(ShotEdit).where(ShotEdit.project_id == project_id))
        session.execute(delete(Shot).where(Shot.timeline_revision_id.in_(timeline_ids)))
        session.execute(delete(TimelineRevision).where(TimelineRevision.project_id == project_id))
        session.execute(delete(Asset).where(Asset.project_id == project_id))
        session.execute(delete(Project).where(Project.id == project_id))
        session.commit()
    except Exception:
        session.rollback()
        if trash_dir and trash_dir.exists() and not project_dir.exists():
            trash_dir.replace(project_dir)
        raise

    media_deleted = True
    if trash_dir and trash_dir.exists():
        try:
            shutil.rmtree(trash_dir)
        except OSError:
            media_deleted = False
    return {"deleted": True, "media_deleted": media_deleted}


@router.get("/{project_id}/prompts", response_model=list[PromptRevisionSummary])
def list_prompt_revisions(
    project_id: UUID,
    current_timeline_only: bool = False,
    status_filter: str | None = Query(default=None, alias="status"),
    generation_segment_id: UUID | None = None,
    prompt_mode: Literal[
        "full_reference_video_edit",
        "standalone_video_recreation",
        "reference_video_edit",
        "full_video_description",
    ] | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[PromptRevision]:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if status_filter not in {None, "completed"}:
        raise HTTPException(status_code=422, detail="提示词状态筛选只支持 completed")
    query = select(PromptRevision).where(PromptRevision.project_id == project_id)
    if status_filter == "completed":
        query = query.where(PromptRevision.status == "completed", PromptRevision.text != "")
    if current_timeline_only:
        current = session.scalar(select(TimelineRevision).where(
            TimelineRevision.project_id == project_id
        ).order_by(TimelineRevision.version.desc()))
        if current is None:
            return []
        query = query.where(PromptRevision.source_timeline_revision_id == current.id)
    if generation_segment_id is not None:
        query = query.where(PromptRevision.generation_segment_id == generation_segment_id)
    if prompt_mode is not None:
        query = query.where(PromptRevision.prompt_mode == prompt_mode)
    return list(session.scalars(query.order_by(PromptRevision.version.desc())))


def _reference_image_name(session: Session, project_id: UUID, kind: str) -> str | None:
    asset = _reference_image(session, project_id, kind)
    return asset.original_filename if asset else None


def _reference_image(session: Session, project_id: UUID, kind: str) -> Asset | None:
    return session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == kind).order_by(Asset.id.desc()))


def _reference_images(session: Session, project_id: UUID, kind: str) -> list[Asset]:
    return list(session.scalars(select(Asset).where(Asset.project_id == project_id, Asset.kind == kind).order_by(Asset.id)))


def _combined_reference_profile(assets: list[Asset]) -> str | None:
    profiled = [item for item in assets if item.profile_text and item.profile_text.strip()]
    if not profiled:
        return None
    latest_structure = load_structure(assets[-1]) if assets else {}
    # 页面二只读取统一档案；逐图原文仍保存在每个素材的后台结构中。
    if latest_structure.get("summary_generated") or latest_structure.get("summary_confirmed") or assets[-1].profile_user_edited:
        return assets[-1].profile_text.strip()
    name = str(latest_structure.get("product_name") or "").strip()
    selling_points = str(latest_structure.get("selling_points") or "").strip()
    header = [value for value in [f"产品名称：{name}" if name else "", f"用户提供的产品卖点：{selling_points}" if selling_points else ""] if value]
    facts = []
    for index, item in enumerate(profiled, 1):
        metadata = load_structure(item)
        label = str(metadata.get("display_name") or metadata.get("view_label") or "其他")
        note = str(metadata.get("note") or "").strip()
        annotation = f"；人工备注：{note}" if note else ""
        facts.append(f"参考图 {index}（{label}{annotation}）：\n{item.profile_text.strip()}")
    return "\n".join(header + (["\n综合图片事实："] if header else [])) + "\n" + "\n\n".join(facts)


def _product_profile_confirmed(assets: list[Asset]) -> bool:
    return bool(assets and load_structure(assets[-1]).get("summary_confirmed"))


def _combined_analysis_status(assets: list[Asset]) -> str | None:
    if not assets:
        return None
    statuses = [_api_analysis_status(item.analysis_status) for item in assets]
    if any(item in {"queued", "running", "processing", "retryable"} for item in statuses):
        return "queued"
    if all(item in {"succeeded", "completed"} for item in statuses):
        return "succeeded"
    return "failed"


def _actionable_bodies(text: str) -> str:
    """提取完整提示词所有时间块的 修改/删除 正文，供替换检测（不含前缀与禁止栏目）。"""
    try:
        document = parse_full_prompt(text)
    except FullPromptValidationError:
        return text
    parts = []
    for block in document.blocks:
        parts.append(block.modify)
        parts.append(block.delete)
    return "\n".join(parts)


def _asset_kind(reference_kind: str) -> str:
    kind = {"person": "person_reference_image", "background": "background_reference_image", "product": "product_reference_image", "target_product": "target_product_reference_image"}.get(reference_kind)
    if kind is None:
        raise HTTPException(status_code=422, detail="未知的参考图片类型")
    return kind


def _api_analysis_status(value: str | None) -> str:
    return {"pending": "queued", "processing": "running", "completed": "succeeded", "retryable": "queued"}.get(value or "", value or "queued")


def _profile_response(asset: Asset | None, reference_kind: str) -> ReferenceProfileResponse:
    if asset is None:
        return ReferenceProfileResponse(asset_id=None, kind=reference_kind, status="missing", profile=None, structure={}, error=None, user_edited=False)
    return ReferenceProfileResponse(
        asset_id=asset.id, kind=reference_kind, status=_api_analysis_status(asset.analysis_status),
        profile=asset.profile_text, structure=load_structure(asset), error=asset.analysis_error,
        user_edited=bool(asset.profile_user_edited),
    )


@router.get("/{project_id}/reference-video/content")
def get_reference_video_content(project_id: UUID, session: Session = Depends(get_session)) -> FileResponse:
    asset = session.scalar(
        select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc())
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="参考视频不存在")
    path = Path(asset.original_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="参考视频文件不存在")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream", filename=path.name)


@router.get("/{project_id}/reference-images/{reference_kind}/content")
def get_reference_image_content(project_id: UUID, reference_kind: str, session: Session = Depends(get_session)) -> FileResponse:
    asset = _reference_image(session, project_id, _asset_kind(reference_kind))
    if asset is None:
        raise HTTPException(status_code=404, detail="参考图片不存在")
    path = Path(asset.original_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="参考图片文件不存在")
    return FileResponse(path, media_type=asset.content_type or mimetypes.guess_type(path.name)[0] or "image/jpeg", filename=asset.original_filename or path.name)


@router.get("/{project_id}/reference-images/{reference_kind}/content/{asset_id}")
def get_reference_image_asset_content(project_id: UUID, reference_kind: str, asset_id: UUID, session: Session = Depends(get_session)) -> FileResponse:
    asset = session.get(Asset, asset_id)
    if asset is None or asset.project_id != project_id or asset.kind != _asset_kind(reference_kind):
        raise HTTPException(status_code=404, detail="参考图片不存在")
    path = Path(asset.original_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="参考图片文件不存在")
    return FileResponse(path, media_type=asset.content_type or mimetypes.guess_type(path.name)[0] or "image/jpeg", filename=asset.original_filename or path.name)


@router.delete("/{project_id}/reference-images/{reference_kind}")
def delete_reference_image(project_id: UUID, reference_kind: str, session: Session = Depends(get_session)) -> dict[str, int | bool]:
    if reference_kind not in {"person", "background"}:
        raise HTTPException(status_code=422, detail="这里只能删除人物或背景参考图")
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assets = _reference_images(session, project_id, _asset_kind(reference_kind))
    if not assets:
        return {"deleted": 0, "media_deleted": True}

    asset_ids = {str(asset.id) for asset in assets}
    active_generation = None
    for item in session.scalars(select(Generation).where(
        Generation.project_id == project_id,
        Generation.status.in_(["queued", "processing", "retryable", "submission_uncertain"]),
    )):
        try:
            referenced_ids = json.loads(item.reference_asset_ids or "[]")
        except json.JSONDecodeError:
            continue
        if asset_ids.intersection(referenced_ids):
            active_generation = item
            break
    if active_generation is not None:
        raise HTTPException(status_code=409, detail="该参考图正在用于视频生成，请等待当前生成任务结束后再删除")

    profile_jobs = list(session.scalars(select(Job).where(
        Job.project_id == project_id,
        Job.kind == "reference_profile_analysis",
        Job.provider_input_id.in_(asset_ids),
        Job.status.in_(["pending", "queued", "uploaded", "processing", "retryable"]),
    )))
    if any(job.leased_at is not None or job.status in {"uploaded", "processing"} for job in profile_jobs):
        raise HTTPException(status_code=409, detail="该参考图仍在分析中，请等待分析结束后再删除")
    for job in profile_jobs:
        job.status, job.error_message = "superseded", "参考图已删除，旧分析任务已作废"

    paths = [Path(asset.original_path) for asset in assets if asset.original_path]
    session.execute(delete(Asset).where(Asset.id.in_([asset.id for asset in assets])))
    session.commit()

    media_root = Settings().media_root.resolve()
    media_deleted = True
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_relative_to(media_root):
            media_deleted = False
            continue
        try:
            resolved.unlink(missing_ok=True)
        except OSError:
            media_deleted = False
    return {"deleted": len(assets), "media_deleted": media_deleted}


@router.patch("/{project_id}/reference-images/{reference_kind}/{asset_id}", response_model=ReferenceImageDetails)
def update_reference_image(
    project_id: UUID,
    reference_kind: str,
    asset_id: UUID,
    payload: UpdateReferenceImageRequest,
    session: Session = Depends(get_session),
) -> ReferenceImageDetails:
    if reference_kind not in {"product", "target_product"}:
        raise HTTPException(status_code=422, detail="只能修改产品图片名称")
    if payload.view_label not in PRODUCT_VIEW_LABELS:
        raise HTTPException(status_code=422, detail="未知的产品图片角度")
    display_name = payload.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="图片名称不能为空")
    asset = session.get(Asset, asset_id)
    if asset is None or asset.project_id != project_id or asset.kind != _asset_kind(reference_kind):
        raise HTTPException(status_code=404, detail="参考图片不存在")
    metadata = load_structure(asset)
    metadata.update({"view_label": payload.view_label, "display_name": display_name})
    asset.profile_json = json.dumps(metadata, ensure_ascii=False)
    session.commit()
    return ReferenceImageDetails(
        id=asset.id,
        filename=asset.original_filename or Path(asset.original_path).name,
        image_url=f"/api/projects/{project_id}/reference-images/{reference_kind}/content/{asset.id}",
        status=_api_analysis_status(asset.analysis_status),
        view_label=payload.view_label,
        display_name=display_name,
        note=str(metadata.get("note") or ""),
    )


@router.get("/{project_id}/evidence/{evidence_id}/content")
def get_evidence_content(project_id: UUID, evidence_id: UUID, session: Session = Depends(get_session)) -> FileResponse:
    evidence = session.get(ShotEvidence, evidence_id)
    if evidence is None or evidence.shot.timeline_revision.project_id != project_id:
        raise HTTPException(status_code=404, detail="Evidence frame does not exist")
    path = Path(evidence.image_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Evidence frame file does not exist")
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@router.post("/{project_id}/reference-video", response_model=UploadReferenceVideoResponse, status_code=status.HTTP_202_ACCEPTED)
def upload_reference_video(
    project_id: UUID,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> UploadReferenceVideoResponse:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(status_code=422, detail="请上传视频文件")
    if not filename or Path(filename).name != filename or suffix not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=422, detail="仅支持安全的 MP4、MOV 或 WebM 视频文件名")
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    settings = Settings()
    destination = settings.media_root / str(project_id) / "original" / f"{uuid4().hex}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    try:
        with destination.open("xb") as output:
            while chunk := file.file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > settings.max_reference_video_bytes:
                    raise HTTPException(status_code=413, detail="Reference video exceeds the upload size limit")
                output.write(chunk)
        metadata = probe_video(destination)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except MediaToolUnavailableError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="视频文件无法解码，请确认文件能正常播放，并优先使用 H.264 编码的 MP4") from exc

    session.execute(select(Project).where(Project.id == project_id).with_for_update())
    previous_assets = list(session.scalars(select(Asset).where(
        Asset.project_id == project_id,
        Asset.kind == "reference_video",
    ).with_for_update()))
    previous_asset = previous_assets[0] if previous_assets else None
    active_video_job_kinds = {
        "extract_media", "vision_analysis", "vision_shot_analysis",
        "final_prompt_generation", "prompt_refinement", "prompt_selling_point_optimization",
    }
    active_jobs = list(session.scalars(select(Job).where(
        Job.project_id == project_id,
        Job.kind.in_(active_video_job_kinds),
        Job.status.in_(["pending", "queued", "uploaded", "processing", "retryable"]),
    ).with_for_update()))
    blocking_jobs = [job for job in active_jobs if job.leased_at is not None or job.status in {"uploaded", "processing"}]
    if previous_asset is not None and blocking_jobs:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="当前仍有参考视频任务正在处理，请等待任务结束后再替换视频")
    if previous_asset is not None:
        for old_job in active_jobs:
            old_job.status, old_job.error_message = "superseded", "参考视频已替换，旧任务已作废"
            old_job.leased_at, old_job.leased_by = None, None
            if old_job.provider_input_id and old_job.kind in {
                "final_prompt_generation", "prompt_refinement", "prompt_selling_point_optimization",
            }:
                old_prompt = session.get(PromptRevision, UUID(old_job.provider_input_id))
                if old_prompt:
                    old_prompt.status, old_prompt.error_message = "superseded", "参考视频已替换，旧提示词任务已作废"
        for old_asset in previous_assets:
            old_asset.kind = "reference_video_history"

    asset = Asset(
        project_id=project_id, kind="reference_video", original_path=str(destination), original_filename=filename,
        content_type=mimetypes.guess_type(filename)[0] or file.content_type, size_bytes=total_bytes,
        duration_sec=metadata.duration_sec, width=metadata.width, height=metadata.height, fps=metadata.fps,
    )
    job = Job(project_id=project_id, kind="extract_media", status="pending")
    session.add_all([asset, job])
    if previous_asset is not None:
        latest_timeline = session.scalar(select(TimelineRevision).where(
            TimelineRevision.project_id == project_id,
        ).order_by(TimelineRevision.version.desc()))
        if latest_timeline is not None:
            session.add(TimelineRevision(
                project_id=project_id,
                version=latest_timeline.version + 1,
                source="reference_replaced",
            ))
    session.commit()
    return UploadReferenceVideoResponse(asset_kind=asset.kind, job_kind=job.kind)


@router.post("/{project_id}/reference-images/{reference_kind}", status_code=status.HTTP_202_ACCEPTED)
def upload_reference_image(
    project_id: UUID,
    reference_kind: str,
    consent: bool = False,
    file: UploadFile = File(...),
    view_label: str = Form(default="other"),
    display_name: str = Form(default="", max_length=40),
    note: str = Form(default=""),
    product_name: str = Form(default=""),
    product_category: str = Form(default=""),
    package_form: str = Form(default=""),
    selling_points: str = Form(default=""),
    session: Session = Depends(get_session),
) -> dict[str, str | None]:
    kind = _asset_kind(reference_kind)
    if reference_kind == "person" and not consent:
        raise HTTPException(status_code=422, detail="Confirm you have permission to use this person's image")
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project does not exist")
    filename, suffix = file.filename or "", Path(file.filename or "").suffix.lower()
    if not filename or Path(filename).name != filename or suffix not in IMAGE_EXTENSIONS or not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=422, detail="Upload a JPG, PNG, or WebP image")
    destination = Settings().media_root / str(project_id) / "references" / f"{uuid4().hex}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with destination.open("xb") as output:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > 20 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Reference image exceeds the 20 MB limit")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    metadata = {}
    if reference_kind in {"product", "target_product"}:
        if view_label not in PRODUCT_VIEW_LABELS:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="未知的产品图片角度")
        metadata = {
            "view_label": view_label,
            "display_name": display_name.strip(),
            "note": note.strip()[:1000],
            "product_name": product_name.strip()[:200],
            "product_category": product_category.strip()[:100],
            "package_form": package_form.strip()[:30],
            "selling_points": selling_points.strip()[:3000],
            "selling_point_cards": build_selling_point_cards(selling_points.strip()[:3000]),
            "summary_confirmed": False,
        }
        # 新图片或产品信息变化会让旧总结失效，但不会删除旧图片和旧文字。
        for existing in _reference_images(session, project_id, kind):
            existing_metadata = load_structure(existing)
            existing_metadata.update({
                "product_name": metadata["product_name"],
                "product_category": metadata["product_category"],
                "package_form": metadata["package_form"],
                "selling_points": metadata["selling_points"],
                "selling_point_cards": metadata["selling_point_cards"],
                "summary_generated": False,
                "summary_confirmed": False,
            })
            existing.profile_json = json.dumps(existing_metadata, ensure_ascii=False)
            existing.profile_user_edited = False
    asset = Asset(project_id=project_id, kind=kind, original_path=str(destination), original_filename=filename, content_type=file.content_type, size_bytes=total, analysis_status="queued", profile_json=json.dumps(metadata, ensure_ascii=False))
    session.add(asset)
    session.flush()
    job = queue_profile_job(session, asset, settings=Settings())
    return {
        "asset_id": str(asset.id), "kind": kind, "filename": filename, "analysis_status": _api_analysis_status(asset.analysis_status),
        "job_id": str(job.id) if job else None,
    }


@router.post("/{project_id}/reference-images/{reference_kind}/analyze", status_code=status.HTTP_202_ACCEPTED)
def analyze_reference_image(project_id: UUID, reference_kind: str, session: Session = Depends(get_session)) -> dict[str, str | None]:
    assets = _reference_images(session, project_id, _asset_kind(reference_kind))
    if not assets:
        raise HTTPException(status_code=422, detail="请先上传参考图片")
    # 多图产品档案重试所有未成功图片；单图素材仍保持原行为。
    targets = assets if reference_kind in {"target_product", "product"} else [assets[-1]]
    jobs = []
    for asset in targets:
        if _api_analysis_status(asset.analysis_status) == "succeeded":
            continue
        job = queue_profile_job(session, asset, retry=True, settings=Settings())
        if job:
            jobs.append(job)
    if not jobs:
        errors = "\n".join(asset.analysis_error or "" for asset in targets).strip()
        return {"job_id": None, "status": "failed" if errors else "succeeded", "error": errors or None}
    return {"job_id": str(jobs[0].id), "status": "queued", "error": None}


@router.get("/{project_id}/reference-profiles/{reference_kind}", response_model=ReferenceProfileResponse)
def get_reference_profile(project_id: UUID, reference_kind: str, session: Session = Depends(get_session)) -> ReferenceProfileResponse:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    kind = _asset_kind(reference_kind)
    assets = _reference_images(session, project_id, kind)
    latest = assets[-1] if assets else None
    response = _profile_response(latest, reference_kind)
    if reference_kind in {"product", "target_product"} and latest:
        response.profile = _combined_reference_profile(assets)
        response.status = _combined_analysis_status(assets) or "missing"
        response.error = "\n".join(item.analysis_error for item in assets if item.analysis_error) or None
    return response


@router.put("/{project_id}/reference-profiles/{reference_kind}", response_model=ReferenceProfileResponse)
def update_reference_profile(project_id: UUID, reference_kind: str, payload: ReferenceProfileUpdate, session: Session = Depends(get_session)) -> ReferenceProfileResponse:
    kind = _asset_kind(reference_kind)
    asset = _reference_image(session, project_id, kind)
    if asset is None:
        # 人物允许纯文字档案；其他素材仍必须先上传图片，避免创建无意义的空素材。
        if reference_kind != "person":
            raise HTTPException(status_code=422, detail="请先上传参考图片")
        if session.get(Project, project_id) is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        asset = Asset(project_id=project_id, kind=kind, original_path="", analysis_status="succeeded")
        session.add(asset)
    asset.profile_text = payload.profile.strip()
    structure = load_structure(asset)
    structure.update(payload.structure)
    if reference_kind in {"product", "target_product"} and "selling_points" in payload.structure:
        structure["selling_point_cards"] = build_selling_point_cards(str(payload.structure.get("selling_points") or ""))
    asset.profile_json = json.dumps(structure, ensure_ascii=False)
    asset.profile_user_edited = True
    if reference_kind in {"product", "target_product"}:
        # This edit confirms the aggregate fact sheet; it must not launder a
        # failed per-image analysis into success. Copy only aggregate identity
        # flags so every usable view sees the same explicit confirmation.
        shared_keys = {
            "product_name", "product_category", "package_form", "selling_points",
            "selling_point_cards", "summary_confirmed",
        }
        shared_structure = {key: value for key, value in payload.structure.items() if key in shared_keys}
        product_assets = _reference_images(session, project_id, kind)
        for product_asset in product_assets:
            if product_asset.id == asset.id:
                continue
            product_structure = load_structure(product_asset)
            product_structure.update(shared_structure)
            product_asset.profile_json = json.dumps(product_structure, ensure_ascii=False)
        if len(product_assets) == 1:
            # A manually supplied single-image fact sheet is a complete escape
            # hatch when vision is unavailable, matching the existing workflow.
            asset.analysis_status, asset.analysis_error = "succeeded", None
    else:
        asset.analysis_status, asset.analysis_error = "succeeded", None
    session.commit()
    if reference_kind in {"product", "target_product"}:
        # A product card represents all uploaded views. Returning only the last
        # asset used to hide a failed sibling behind a false "分析成功" badge.
        return get_reference_profile(project_id, reference_kind, session)
    return _profile_response(asset, reference_kind)


@router.get("/{project_id}/product-profile", response_model=ReferenceProfileResponse)
def get_product_profile(project_id: UUID, session: Session = Depends(get_session)) -> ReferenceProfileResponse:
    return get_reference_profile(project_id, "product", session)


@router.put("/{project_id}/product-profile", response_model=ReferenceProfileResponse)
def update_product_profile(project_id: UUID, payload: ReferenceProfileUpdate, session: Session = Depends(get_session)) -> ReferenceProfileResponse:
    return update_reference_profile(project_id, "product", payload, session)


@router.post("/{project_id}/reference-video/publish", response_model=PublishReferenceResponse)
def publish_reference_video(project_id: UUID, session: Session = Depends(get_session)) -> PublishReferenceResponse:
    asset = session.scalar(select(Asset).where(Asset.project_id == project_id, Asset.kind == "reference_video").order_by(Asset.id.desc()))
    if asset is None:
        raise HTTPException(status_code=422, detail="Upload a reference video first")
    if asset.public_url and asset.public_url_expires_at:
        return PublishReferenceResponse(url=asset.public_url, expires_at=asset.public_url_expires_at, notice="Temporary public link is already available")
    try:
        published = TempfilePublisher().publish(Path(asset.original_path), asset.content_type or "application/octet-stream")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Temporary publishing failed") from exc
    asset.public_url, asset.public_url_expires_at = published.url, published.expires_at.isoformat()
    session.commit()
    return PublishReferenceResponse(url=asset.public_url, expires_at=asset.public_url_expires_at, notice="Uploaded to tempfile.org; it will be deleted automatically after about 24 hours")


@router.post("/{project_id}/prompt/validate", status_code=status.HTTP_204_NO_CONTENT)
def validate_locked_product(
    project_id: UUID,
    payload: PromptValidationRequest,
    session: Session = Depends(get_session),
) -> None:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.mode == "preserve_product" and contains_product_replacement(payload.text):
        raise HTTPException(status_code=422, detail="页面一固定保留原商品，不能在提示词中替换产品。")


def _shot_edit_response(session: Session, project_id: UUID, shot_id: UUID) -> ShotEditResponse | None:
    edit = session.scalar(select(ShotEdit).where(ShotEdit.project_id == project_id, ShotEdit.shot_id == shot_id))
    if edit is None:
        return None
    return ShotEditResponse(
        shot_id=shot_id, people=edit.people or "", action=edit.action or "", product=edit.product or "",
        product_interaction=edit.product_interaction or "", background=edit.background or "", camera=edit.camera or "",
        lighting=edit.lighting or "", visual_style=edit.visual_style or "", visible_text=edit.visible_text or "",
        uncertainties=edit.uncertainties or "", keep_unchanged=edit.keep_unchanged.splitlines() if edit.keep_unchanged else [],
        confirmed=bool(edit.confirmed), version=edit.version or 1, ai_summary_version=edit.ai_summary_version or 0,
    )


@router.put("/{project_id}/shots/{shot_id}/edit", response_model=ShotEditResponse)
def save_shot_edit(
    project_id: UUID,
    shot_id: UUID,
    payload: ShotEditRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    session: Session = Depends(get_session),
) -> ShotEditResponse:
    shot = session.get(Shot, shot_id)
    if shot is None or shot.timeline_revision.project_id != project_id:
        raise HTTPException(status_code=404, detail="Shot does not exist")
    project = session.get(Project, project_id)
    edited_text = "\n".join((payload.people, payload.action, payload.product, payload.product_interaction, payload.background, payload.camera, payload.lighting, payload.visual_style, payload.visible_text, payload.uncertainties, *payload.keep_unchanged))
    if project and project.mode == "preserve_product" and contains_product_replacement(edited_text):
        raise HTTPException(status_code=422, detail="页面一固定保留原商品，镜头修改不能替换产品。")
    edit = session.scalar(select(ShotEdit).where(ShotEdit.project_id == project_id, ShotEdit.shot_id == shot_id))
    if if_match is not None:
        try:
            expected_version = int(if_match.strip('"'))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="If-Match 必须是镜头编辑版本号") from exc
        current_version = edit.version or 0 if edit else 0
        if expected_version != current_version:
            raise HTTPException(status_code=409, detail={
                "message": "镜头已被其他页面更新，请刷新后再保存",
                "expected_version": expected_version,
                "current_version": current_version,
            })
    if edit is None:
        edit = ShotEdit(project_id=project_id, shot_id=shot_id)
        session.add(edit)
    edit.people = payload.people.strip()
    edit.action = payload.action.strip()
    edit.product = payload.product.strip()
    edit.product_interaction = payload.product_interaction.strip()
    edit.background = payload.background.strip()
    edit.camera = payload.camera.strip()
    edit.lighting = payload.lighting.strip()
    edit.visual_style = payload.visual_style.strip()
    edit.visible_text = payload.visible_text.strip()
    edit.uncertainties = payload.uncertainties.strip()
    edit.keep_unchanged = "\n".join(item.strip() for item in payload.keep_unchanged if item.strip())
    edit.confirmed = payload.confirmed
    edit.ai_summary_version = session.scalar(select(func.max(ShotAISummary.version)).where(ShotAISummary.shot_id == shot_id)) or 0
    edit.version = (edit.version or 0) + 1
    session.commit()
    return _shot_edit_response(session, project_id, shot_id)


@router.get("/{project_id}/shots/{shot_id}/ai-summary/latest", response_model=AdoptAISummaryResponse)
def latest_ai_summary(project_id: UUID, shot_id: UUID, session: Session = Depends(get_session)) -> AdoptAISummaryResponse:
    shot = session.get(Shot, shot_id)
    if shot is None or shot.timeline_revision.project_id != project_id:
        raise HTTPException(status_code=404, detail="镜头不存在")
    summary = session.scalar(select(ShotAISummary).where(ShotAISummary.shot_id == shot_id).order_by(ShotAISummary.version.desc()))
    if summary is None:
        raise HTTPException(status_code=404, detail="该镜头还没有AI总结")
    return AdoptAISummaryResponse(version=summary.version, content=json.loads(summary.content))


@router.post("/{project_id}/prompts", response_model=PromptResponse, status_code=status.HTTP_201_CREATED)
def create_prompt_revision(
    project_id: UUID,
    payload: CreatePromptRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> PromptResponse:
    project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    visual_direction = payload.visual_direction.strip()
    manual_text = (payload.prompt_text or payload.visual_direction).strip()
    if payload.use_ai and not visual_direction:
        raise HTTPException(status_code=422, detail="请填写提示词生成要求")
    if not payload.use_ai and not manual_text:
        raise HTTPException(status_code=422, detail="当前没有可保存的提示词")
    if project.mode == "preserve_product" and payload.replace_product:
        raise HTTPException(status_code=422, detail="保留产品模式不能开启产品替换。")
    replace_product = project.mode == "replace_product"
    prompt_mode = payload.prompt_mode
    product_assets = confirmed_target_product_assets(session, project)
    product_fact_sheet = _combined_reference_profile(product_assets)
    product_profile = product_prompt_profile(product_assets)
    selling_point_cards = product_selling_point_cards(product_assets)
    person_asset = _reference_image(session, project_id, "person_reference_image")
    if payload.replace_person and (person_asset is None or not person_asset.profile_text or _api_analysis_status(person_asset.analysis_status) != "succeeded"):
        raise HTTPException(status_code=422, detail="选择替换人物前，请先填写并确认人物文字档案，或上传并确认人物图片")
    audio_mode = {"keep_original": "none", "add_style": "custom"}.get(payload.audio_mode, payload.audio_mode)
    audio_style = payload.audio_style.strip()
    if audio_mode not in {"none", "auto", "custom"}:
        raise HTTPException(status_code=422, detail="音频只支持无音频、自动生成音频或用户填写音频要求")
    if audio_mode == "custom" and not audio_style:
        raise HTTPException(status_code=422, detail="请填写音频要求")
    revision = session.scalar(select(TimelineRevision).where(TimelineRevision.project_id == project_id).order_by(TimelineRevision.version.desc()))
    current_shots = list(session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.id).order_by(Shot.position))) if revision else []
    # 新工作流：一份覆盖完整时间轴的提示词。不接受生成片段绑定。
    segment: GenerationSegment | None = None
    if payload.generation_segment_id is not None:
        raise HTTPException(status_code=422, detail="已改为完整提示词工作流，不再按生成片段创建提示词")
    if payload.use_ai and revision is None:
        raise HTTPException(status_code=422, detail="请先确认时间轴")
    if payload.use_ai and not current_shots:
        raise HTTPException(status_code=422, detail="当前时间轴没有镜头")
    if project.mode == "preserve_product" and contains_product_replacement(payload.visual_direction):
        # AI 改编要求立即拒绝；人工保存完整文本延后到栏目级可执行正文检测（避免误伤锁定规则）。
        if payload.use_ai:
            raise HTTPException(status_code=422, detail="保留产品模式的提示词不能替换产品。")
    if replace_product:
        product_status = _combined_analysis_status(product_assets)
        product_confirmed = _product_profile_confirmed(product_assets)
        if not product_assets or not product_fact_sheet:
            raise HTTPException(status_code=422, detail="选择替换产品前，请先上传产品图片并生成产品档案")
        if product_status == "queued":
            raise HTTPException(status_code=422, detail="产品图片仍在理解或等待自动重试，请完成后再生成提示词")
        if product_status == "failed" and not product_confirmed:
            failed_labels = []
            for asset in product_assets:
                if _api_analysis_status(asset.analysis_status) == "failed":
                    structure = load_structure(asset)
                    failed_labels.append(str(structure.get("display_name") or structure.get("view_label") or asset.original_filename or "未命名图片"))
            label_text = "、".join(failed_labels) or "部分产品图"
            raise HTTPException(
                status_code=422,
                detail=f"{label_text}理解失败。请点击重试；若档案内容已经足够，也可以人工校对并确认后继续。",
            )
        if not product_confirmed:
            raise HTTPException(status_code=422, detail="请先校对并确认产品档案")
    # 服务端确定性前缀：用当前已确认资料生成，人工保存严格比对、AI 入队时冻结。
    product_purpose_lines = []
    if replace_product:
        for asset in product_assets:
            structure = load_structure(asset)
            name = str(structure.get("display_name") or structure.get("view_label") or "其他").strip()
            note = str(structure.get("note") or "").strip()
            product_purpose_lines.append(f"{name}：锁定该角度结构" + (f"（{note}）" if note else ""))
    people_reference = None
    person_asset = _reference_image(session, project_id, "person_reference_image")
    if person_asset and person_asset.profile_text:
        people_reference = person_asset.profile_text.strip()
    background_reference = None
    background_asset = _reference_image(session, project_id, "background_reference_image")
    if background_asset and background_asset.profile_text:
        background_reference = background_asset.profile_text.strip()
    # 旧客户端在人工保存时把完整正文放在 visual_direction；新客户端用
    # prompt_text 承载正文、visual_direction 单独承载最高优先级改编要求。
    prefix_user_direction = visual_direction if payload.use_ai or payload.prompt_text.strip() else ""
    expected_prefix = build_full_prompt_prefix(
        project_mode=project.mode,
        product_profile=product_profile,
        product_image_purposes=product_purpose_lines,
        selling_point_cards=selling_point_cards if replace_product else [],
        people_reference=people_reference if payload.replace_person else None,
        background_reference=background_reference,
        audio_mode=audio_mode,
        audio_style=audio_style,
        replace_person=payload.replace_person,
        user_direction=prefix_user_direction,
        people_reference_has_image=bool(person_asset and (person_asset.original_path or "").strip()),
    )
    version = _next_prompt_version(session, project_id)
    shot_instructions = []
    if revision:
        for shot in current_shots:
            edit = session.scalar(select(ShotEdit).where(ShotEdit.project_id == project_id, ShotEdit.shot_id == shot.id))
            # 最终提示词只使用用户确认版本，不读取模型中间结果。
            if edit is None or not edit.confirmed:
                raise HTTPException(status_code=422, detail=f"镜头 {shot.position + 1} 的事实尚未确认")
            shot_instructions.append({
                "shot_id": str(shot.id),
                "start_sec": shot.start_sec, "end_sec": shot.end_sec,
                "facts": {"people": edit.people, "action": edit.action, "product": edit.product, "product_interaction": edit.product_interaction, "background": edit.background, "camera": edit.camera, "lighting": edit.lighting, "visual_style": edit.visual_style, "visible_text": edit.visible_text, "uncertainties": edit.uncertainties},
                "changes": {},
                "keep": edit.keep_unchanged.splitlines() if edit.keep_unchanged else [],
                "has_product": bool(shot.product_interaction),
            })
    adaptation_conflicts: list[dict] = []
    if replace_product:
        _, conflicts = check_product_compatibility(product_profile or "", shot_instructions)
        blocking_conflicts = [item for item in conflicts if item.get("severity") == "blocked"]
        if blocking_conflicts:
            raise HTTPException(status_code=422, detail={
                "message": "目标产品与部分镜头动作根本不兼容，请先修正这些镜头",
                "shot_ids": [item["shot_id"] for item in blocking_conflicts],
                "conflicts": blocking_conflicts,
            })
        adaptation_conflicts = [item for item in conflicts if item.get("severity") == "adaptable"]
    text = expected_prefix if payload.use_ai and prompt_mode == "full_reference_video_edit" else "" if payload.use_ai else manual_text
    prompt_reference_assets = [*product_assets]
    if person_asset is not None:
        prompt_reference_assets.append(person_asset)
    if background_asset is not None:
        prompt_reference_assets.append(background_asset)
    # AI生成放到Worker，避免浏览器等待数分钟后超时；人工版本仍立即保存。
    # AI 入队时冻结服务端确定性前缀到 text，Worker 使用该快照，完成后再替换为完整提示词。
    if not payload.use_ai and prompt_mode == "full_reference_video_edit":
        # 人工保存：预期绝对时间标签来自数据库当前 Shot，而非待验证文本自身。
        if revision is None:
            raise HTTPException(status_code=422, detail="请先确认时间轴")
        shot_ranges = [(shot.start_sec, shot.end_sec) for shot in session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.id).order_by(Shot.position))]
        try:
            document = validate_full_prompt(text, shot_ranges)
        except FullPromptValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # 确定性前缀必须与数据库当前已确认资料生成的 expected_prefix 严格一致（换行归一化后）。
        if document.global_prefix != expected_prefix:
            raise HTTPException(status_code=422, detail="完整提示词的确定性前缀与当前已确认资料不一致，请重新生成或修正前缀")
        if project.mode == "preserve_product":
            # 只对修改/删除正文执行替换检测。
            if contains_product_replacement(_actionable_bodies(text)):
                raise HTTPException(status_code=422, detail="保留产品模式的提示词不能替换产品。")
        text = normalize_full_prompt_contract(
            text,
            shot_ranges,
            shot_instructions,
            project_mode=project.mode,
            replace_person=payload.replace_person,
        )
    if not payload.use_ai and prompt_mode == "standalone_video_recreation":
        try:
            validate_recreation_prompt(text, [(shot.start_sec, shot.end_sec) for shot in current_shots], project.mode)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    prompt_revision = PromptRevision(
        project_id=project_id, version=version, text=text,
        visual_direction=prefix_user_direction, audio_mode=audio_mode,
        reference_asset_ids=json.dumps([str(asset.id) for asset in prompt_reference_assets]),
        audio_style=audio_style, replace_product=replace_product,
        replace_person=payload.replace_person, source_timeline_revision_id=revision.id if revision else None,
        prompt_mode=prompt_mode,
        generation_segment_id=None,
        status="queued" if payload.use_ai else "completed",
    )
    session.add(prompt_revision)
    session.flush()
    if payload.use_ai:
        session.add(Job(project_id=project_id, kind="final_prompt_generation", status="queued", provider="comfly_gpt", provider_input_id=str(prompt_revision.id)))
        # 异步入队：AI 创建返回 202，人工保存返回 201。
        response.status_code = status.HTTP_202_ACCEPTED
    session.commit()
    return PromptResponse(
        version=version, text=text, status=prompt_revision.status,
        adaptation_count=len(adaptation_conflicts), adaptation_conflicts=adaptation_conflicts,
    )


@router.post("/{project_id}/prompts/refine", response_model=PromptResponse, status_code=status.HTTP_202_ACCEPTED)
def refine_prompt_revision(
    project_id: UUID,
    payload: RefinePromptRequest,
    session: Session = Depends(get_session),
) -> PromptResponse:
    """基于选定的完整提示词创建一个新的 GPT 修改版本，旧版本保持不变。"""
    project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    source_query = select(PromptRevision).where(
        PromptRevision.project_id == project_id,
        PromptRevision.status == "completed",
        PromptRevision.text != "",
    )
    if payload.source_version is not None:
        source_query = source_query.where(PromptRevision.version == payload.source_version)
    else:
        source_query = source_query.order_by(PromptRevision.version.desc())
    source = session.scalar(source_query)
    if source is None:
        raise HTTPException(status_code=422, detail="请先生成或保存一份完整提示词")
    # 精修只接受已完成的完整提示词。
    if source.prompt_mode not in {"full_reference_video_edit", "standalone_video_recreation"}:
        raise HTTPException(status_code=422, detail="只能精修完整提示词")
    current_revision = session.scalar(select(TimelineRevision).where(TimelineRevision.project_id == project_id).order_by(TimelineRevision.version.desc()))
    if current_revision is None or source.source_timeline_revision_id != current_revision.id:
        raise HTTPException(status_code=422, detail="提示词来自旧时间轴，请重新生成提示词")
    version = _next_prompt_version(session, project_id)
    revision = PromptRevision(
        project_id=project_id, version=version, text=source.text,
        visual_direction=source.visual_direction, operation_instruction=payload.instruction.strip(),
        reference_asset_ids=source.reference_asset_ids, audio_mode=source.audio_mode,
        audio_style=source.audio_style, replace_product=project.mode == "replace_product",
        replace_person=source.replace_person, source_timeline_revision_id=source.source_timeline_revision_id,
        prompt_mode=source.prompt_mode, generation_segment_id=source.generation_segment_id,
        status="queued",
    )
    session.add(revision)
    session.flush()
    session.add(Job(project_id=project_id, kind="prompt_refinement", status="queued", provider="comfly_gpt_5_6", provider_input_id=str(revision.id)))
    session.commit()
    return PromptResponse(version=version, text="", status="queued")


class OptimizeSellingPointsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_version: int = Field(ge=1)


@router.post(
    "/{project_id}/prompts/optimize-selling-points",
    response_model=PromptResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def optimize_prompt_selling_points(
    project_id: UUID,
    payload: OptimizeSellingPointsRequest,
    session: Session = Depends(get_session),
) -> PromptResponse:
    """基于选定的完整提示词创建卖点优化的新版本，源版本保持不变。"""
    project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    source = session.scalar(select(PromptRevision).where(
        PromptRevision.project_id == project_id,
        PromptRevision.version == payload.source_version,
        PromptRevision.status == "completed",
        PromptRevision.text != "",
    ))
    if source is None:
        raise HTTPException(status_code=422, detail="请选择一份已完成的完整提示词")
    if source.prompt_mode != "full_reference_video_edit":
        raise HTTPException(status_code=422, detail="只能优化完整视频编辑提示词")
    current_revision = session.scalar(select(TimelineRevision).where(TimelineRevision.project_id == project_id).order_by(TimelineRevision.version.desc()))
    if current_revision is None or source.source_timeline_revision_id != current_revision.id:
        raise HTTPException(status_code=422, detail="提示词来自旧时间轴，请重新生成提示词")
    # 替换产品模式：卖点优化需要目标产品档案与确认仍有效。
    if project.mode == "replace_product":
        product_assets = confirmed_target_product_assets(session, project)
        if not product_assets or not _product_profile_confirmed(product_assets):
            raise HTTPException(status_code=422, detail="卖点优化前请先确认目标产品档案")
    version = _next_prompt_version(session, project_id)
    revision = PromptRevision(
        project_id=project_id, version=version, text=source.text,
        visual_direction=source.visual_direction,
        reference_asset_ids=source.reference_asset_ids,
        audio_mode=source.audio_mode, audio_style=source.audio_style,
        replace_product=project.mode == "replace_product", replace_person=source.replace_person,
        source_timeline_revision_id=source.source_timeline_revision_id,
        prompt_mode=source.prompt_mode, generation_segment_id=None,
        status="queued",
    )
    session.add(revision)
    session.flush()
    session.add(Job(project_id=project_id, kind="prompt_selling_point_optimization", status="queued", provider="comfly_gpt_5_6", provider_input_id=str(revision.id)))
    session.commit()
    return PromptResponse(version=version, text="", status="queued")


@router.get("/{project_id}/product-compatibility")
def get_product_compatibility(
    project_id: UUID,
    prompt_mode: Literal["full_reference_video_edit", "standalone_video_recreation"] = Query(default="full_reference_video_edit"),
    session: Session = Depends(get_session),
) -> dict:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.mode == "preserve_product":
        return {"status": "compatible", "summary": "页面一锁定原产品，不执行目标产品替换。", "product_kind": None, "conflicts": []}
    product_assets = _reference_images(session, project_id, "product_reference_image")
    asset = product_assets[-1] if product_assets else None
    revision = session.scalar(select(TimelineRevision).where(TimelineRevision.project_id == project_id).order_by(TimelineRevision.version.desc()))
    if asset is None or not asset.profile_text or _api_analysis_status(asset.analysis_status) != "succeeded" or revision is None:
        return {"status": "pending", "summary": "请先上传并确认目标产品档案，然后完成镜头理解。", "product_kind": None, "conflicts": []}
    shots = session.scalars(select(Shot).where(Shot.timeline_revision_id == revision.id).order_by(Shot.position)).all()
    compatibility_shots = []
    for shot in shots:
        edit = session.scalar(select(ShotEdit).where(ShotEdit.project_id == project_id, ShotEdit.shot_id == shot.id))
        compatibility_shots.append({
            "shot_id": str(shot.id), "start_sec": shot.start_sec, "end_sec": shot.end_sec,
            "facts": {
                "action": edit.action if edit and edit.confirmed else shot.action,
                "product_interaction": edit.product_interaction if edit and edit.confirmed else shot.product_interaction,
            },
        })
    product_kind, conflicts = check_product_compatibility(product_prompt_profile(product_assets), compatibility_shots)
    if conflicts:
        blocking = [item for item in conflicts if item.get("severity") == "blocked"]
        if not blocking:
            return {"status": "warning", "summary": f"发现 {len(conflicts)} 个产品动作冲突，生成提示词时将做最小动作适配。", "product_kind": product_kind, "conflicts": conflicts}
        return {"status": "blocked", "summary": f"发现 {len(blocking)} 个无法局部修正的产品动作冲突镜头。", "product_kind": product_kind, "conflicts": conflicts}
    if product_kind == "unknown":
        return {"status": "warning", "summary": "无法自动归类目标产品形态，请人工确认每个产品动作。", "product_kind": product_kind, "conflicts": []}
    return {"status": "compatible", "summary": "已检查产品形态与当前镜头动作，未发现明确冲突。", "product_kind": product_kind, "conflicts": []}
