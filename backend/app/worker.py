"""Run with: python -m app.worker"""
from __future__ import annotations

import time
import json
import socket
import re
from pathlib import Path
from uuid import UUID
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from requests.exceptions import ReadTimeout

from app.core.config import Settings
from app.db.models import Asset, Generation, GenerationSegment, Job, PromptRevision, Shot
from app.db.migrations import require_database_at_head
from app.db.session import SessionLocal
from app.services.generation_jobs import execute_generation_job
from app.services.full_prompt import FullPromptValidationError, derive_segment_prompt, validate_full_prompt
from PIL import UnidentifiedImageError

from app.services.media import ensure_image_within_dimensions, ensure_segment_clip
from app.services.seedance import JsonTaskGateway, build_seedance_request
from app.services.tempfile_publisher import TempfilePublisher
from app.services.comfly_frame_vision import ComflyFrameVisionGateway
from app.services.dual_shot_vision import DualShotVisionGateway
from app.services.vision_jobs import execute_vision_job
from app.services.reference_profiles import execute_profile_job, load_structure
from app.services.final_prompt import execute_final_prompt_job, execute_prompt_refinement_job, execute_selling_point_optimization_job
from app.services.worker_state import MAX_ATTEMPTS, next_poll_at, retry_at

LEASE_TIMEOUT = timedelta(minutes=20)


def redact_request_urls(payload: dict) -> dict:
    """Replace every URL's query string with a redacted marker for persisted snapshots."""
    def redact(value):
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str):
            match = re.match(r"^(https?://[^/?#]+[^?#]*)\?[^#]*", value)
            if match:
                return f"{match.group(1)}?[redacted]"
            return value
        return value
    return redact(payload)


def _resolve_asset_inputs(session, project_id: UUID, asset_ids: list[str]) -> list[Asset]:
    """Restore generation inputs strictly from persisted asset IDs, in order."""
    resolved = []
    for raw in asset_ids:
        try:
            asset_id = UUID(raw)
        except ValueError:
            return []
        asset = session.get(Asset, asset_id)
        if asset is None or asset.project_id != project_id:
            return []
        path = Path(asset.original_path)
        if not path.is_file():
            return []
        resolved.append(asset)
    return resolved


def build_reference_image_manifest(image_assets: list[Asset], *, replace_person: bool = False) -> str:
    """按实际 API content 顺序给通用 reference_image 建立不可歧义的语义编号。"""
    if not image_assets:
        return ""
    lines = ["【本次实际参考图片编号（严格对应 API 输入顺序）】"]
    person_number: int | None = None
    for number, asset in enumerate(image_assets, start=1):
        if asset.kind in {"product_reference_image", "target_product_reference_image"}:
            structure = load_structure(asset)
            name = str(structure.get("display_name") or structure.get("view_label") or asset.original_filename or "未命名产品图").strip()
            lines.append(f"图片{number}：目标产品参考图“{name}”，用于锁定该角度的包装结构、外形、材质、颜色和可见标签位置。")
        elif asset.kind == "person_reference_image":
            person_number = number
            lines.append(
                f"图片{number}：目标人物身份参考图，用于锁定同一人物的脸部、身份、发型和整体外观。"
                if replace_person
                else f"图片{number}：辅助人物参考图；不得据此替换或改变视频1中原人物的身份。"
            )
        elif asset.kind == "background_reference_image":
            lines.append(f"图片{number}：目标背景参考图，用于锁定空间、陈设、光线和氛围。")
        else:
            lines.append(f"图片{number}：辅助参考图。")
    if person_number is not None and replace_person:
        lines.append(
            f"人物替换硬约束：所有出现人物的镜头都必须使用图片{person_number}中的同一人物；"
            "原参考视频人物只提供人数、位置、姿态、动作和表情强度，不得继承其脸部、身份或整体外观。"
        )
    return "\n".join(lines)


def inject_reference_image_manifest(prompt_text: str, manifest: str) -> str:
    """Keep user direction first while placing actual API image numbering nearby."""
    if not manifest:
        return prompt_text
    marker = "【全局执行】"
    if marker in prompt_text:
        return prompt_text.replace(marker, f"{manifest}\n\n{marker}", 1)
    return f"{prompt_text.rstrip()}\n\n{manifest}"


def _publish_if_expired(session, asset: Asset, settings: Settings) -> str:
    """Republish an expired or missing temporary URL; otherwise reuse the stored one."""
    # 多个生成槽会复用同一组产品/人物图。锁住素材行，确保临时服务只收到一次上传，
    # 后来的槽等待首个上传完成后直接复用 URL。
    asset = session.scalar(select(Asset).where(Asset.id == asset.id).with_for_update()) or asset
    expires_at = None
    if asset.public_url_expires_at:
        try:
            expires_at = datetime.fromisoformat(asset.public_url_expires_at)
        except ValueError:
            expires_at = None
    if asset.public_url and (expires_at is None or expires_at > datetime.now(UTC)):
        return asset.public_url
    published = TempfilePublisher().publish(Path(asset.original_path), asset.content_type or "application/octet-stream")
    asset.public_url, asset.public_url_expires_at = published.url, published.expires_at.isoformat()
    session.commit()
    return asset.public_url


def _publish_segment_if_expired(session, segment: GenerationSegment) -> str:
    """Republish an expired or missing segment-clip URL; otherwise reuse the stored one."""
    expires_at = None
    if segment.public_url_expires_at:
        try:
            expires_at = datetime.fromisoformat(segment.public_url_expires_at)
        except ValueError:
            expires_at = None
    if segment.public_url and (expires_at is None or expires_at > datetime.now(UTC)):
        return segment.public_url
    if not segment.clip_path or not Path(segment.clip_path).is_file():
        raise RuntimeError("Segment clip is missing")
    published = TempfilePublisher().publish(Path(segment.clip_path), "video/mp4")
    segment.public_url, segment.public_url_expires_at = published.url, published.expires_at.isoformat()
    session.commit()
    return segment.public_url


def _publish_seedance_image(session, asset: Asset, settings: Settings) -> str:
    asset = session.scalar(select(Asset).where(Asset.id == asset.id).with_for_update()) or asset
    expires_at = None
    if asset.public_url_expires_at:
        try:
            expires_at = datetime.fromisoformat(asset.public_url_expires_at)
        except ValueError:
            expires_at = None
    if asset.public_url and (expires_at is None or expires_at > datetime.now(UTC)):
        return asset.public_url
    source = Path(asset.original_path)
    destination = settings.media_root / str(asset.project_id) / "seedance-references" / f"{asset.id}.png"
    try:
        prepared = ensure_image_within_dimensions(source, destination)
    except (UnidentifiedImageError, OSError, ValueError):
        # 兼容早期测试/遗留的不可探测素材；供应商仍会返回明确的格式错误。
        return _publish_if_expired(session, asset, settings)
    published = TempfilePublisher().publish(
        prepared,
        asset.content_type or "application/octet-stream" if prepared == source else "image/png",
    )
    asset.public_url, asset.public_url_expires_at = published.url, published.expires_at.isoformat()
    session.commit()
    return asset.public_url


def _generation_gateway(settings: Settings, provider: str):
    if provider == "volcengine":
        return JsonTaskGateway(settings.volcengine_seedance_base_url, settings.volcengine_api_key, settings.volcengine_seedance_task_path), settings.volcengine_seedance_model
    return JsonTaskGateway(settings.comfly_base_url, settings.comfly_api_key, settings.comfly_seedance_task_path), settings.comfly_seedance_model


def _mark_retryable(record: Job | Generation, exc: Exception) -> None:
    """Keep unexpected worker failures recoverable instead of hot-looping forever."""
    record.attempts += 1
    raw = str(exc)
    if "OperationDenied" in raw and "Service has not been activated" in raw:
        record.status = "failed"
        record.error_message = "火山 VOD 已连接，但当前账号尚未开通 Aideo 视频理解服务。请先在火山视频点播控制台开通智能应用 Aideo Agent。"
        return
    if isinstance(exc, FullPromptValidationError):
        record.status, record.error_message = "failed", raw
        return
    record.error_message = raw
    max_attempts = 2 if isinstance(record, Job) and record.kind in {
        "final_prompt_generation", "prompt_refinement", "prompt_selling_point_optimization",
    } and isinstance(exc, ReadTimeout) else MAX_ATTEMPTS
    if record.attempts >= max_attempts:
        record.status = "failed"
    else:
        record.status = "retryable"
        record.next_attempt_at = retry_at(datetime.now(UTC), record.attempts)


def _claim(
    session,
    model,
    kinds: list[str] | None,
    worker_id: str,
    now: datetime,
    *,
    statuses: tuple[str, ...],
    limit: int = 5,
):
    predicates = [
        model.status.in_(statuses),
        or_(model.next_attempt_at.is_(None), model.next_attempt_at <= now),
        or_(model.leased_at.is_(None), model.leased_at <= now - LEASE_TIMEOUT),
    ]
    if kinds:
        predicates.append(model.kind.in_(kinds))
    records = session.scalars(
        select(model)
        .where(*predicates)
        .order_by(model.created_at, *([model.batch_position] if model is Generation else []), model.id)
        .with_for_update(skip_locked=True)
        .limit(limit)
    ).all()
    for record in records:
        record.leased_at = now
        record.leased_by = worker_id
        if record.created_at is None:
            record.created_at = now
    session.commit()
    return records


def _release(record: Job | Generation, now: datetime) -> None:
    record.leased_at = None
    record.leased_by = None
    if record.status == "processing":
        record.next_attempt_at = next_poll_at(record.created_at or now, now, record.status)


def _refresh_lease(session, record: Job | Generation) -> None:
    record.leased_at = datetime.now(UTC)
    session.commit()


def run_once(queue: str = "all") -> int:
    if queue not in {"all", "ai", "generation"}:
        raise ValueError(f"Unknown worker queue: {queue}")
    settings = Settings()
    processed = 0
    now = datetime.now(UTC)
    worker_id = f"{socket.gethostname()}:{__import__('os').getpid()}"
    with SessionLocal() as session:
        # 多个 AI worker 各领一个任务；不能提前租住 5 个再串行执行，否则其他 worker 无事可做。
        vision_jobs = [] if queue == "generation" else _claim(session, Job, ["vision_analysis", "vision_shot_analysis", "reference_profile_analysis", "final_prompt_generation", "prompt_refinement", "prompt_selling_point_optimization"], worker_id, now, statuses=("queued", "uploaded", "processing", "retryable"), limit=1)
        for job in vision_jobs:
            try:
                if job.kind in {"final_prompt_generation", "prompt_refinement", "prompt_selling_point_optimization"}:
                    job.status = "processing"
                    if job.provider_input_id:
                        revision = session.get(PromptRevision, UUID(job.provider_input_id))
                        if revision:
                            revision.status = "processing"
                    session.commit()
                if job.kind == "reference_profile_analysis":
                    execute_profile_job(session, job, settings)
                elif job.kind == "final_prompt_generation":
                    execute_final_prompt_job(session, job, settings)
                elif job.kind == "prompt_refinement":
                    execute_prompt_refinement_job(session, job, settings)
                elif job.kind == "prompt_selling_point_optimization":
                    execute_selling_point_optimization_job(session, job, settings)
                else:
                    # 完整视频旧任务保持兼容；人工确认后的逐镜任务使用双模型链路。
                    gateway = DualShotVisionGateway(settings) if job.kind == "vision_shot_analysis" else ComflyFrameVisionGateway(settings)
                    execute_vision_job(session, job, gateway)
            except Exception as exc:
                # A cancellation can arrive while the worker is blocked on the provider request.
                session.refresh(job)
                if job.status == "cancelled":
                    _release(job, now)
                    session.commit()
                    processed += 1
                    continue
                _mark_retryable(job, exc)
                if job.kind == "reference_profile_analysis" and job.provider_input_id:
                    asset = session.get(Asset, UUID(job.provider_input_id))
                    if asset:
                        asset.analysis_status = job.status
                        asset.analysis_error = f"参考图片理解失败：{job.error_message}"
                if job.kind in {"final_prompt_generation", "prompt_refinement", "prompt_selling_point_optimization"} and job.provider_input_id:
                    revision = session.get(PromptRevision, UUID(job.provider_input_id))
                    if revision:
                        revision.status, revision.error_message = job.status, job.error_message
            _release(job, now)
            session.commit()
            processed += 1
        # 每个生成槽只领取一行；否则一次租住整个批次后仍会串行上传，
        # 任一临时文件服务超时都会让兄弟片段看似排队、实际无法被其他槽处理。
        generations = [] if queue == "ai" else _claim(
            session, Generation, None, worker_id, now,
            statuses=("queued", "processing", "retryable"), limit=1,
        )
        for generation in generations:
            try:
                # 发布参考素材也属于实际执行阶段。先持久化状态，前端才能区分“尚未领取”和“正在上传”。
                generation.status = "processing"
                generation.error_message = None
                session.commit()
                gateway, model = _generation_gateway(settings, generation.provider)
                if generation.external_task_id:
                    # 供应商已接单后只轮询结果；不要再次解析提示词、锁素材或上传参考文件。
                    execute_generation_job(session, generation, gateway, {})
                    _release(generation, now)
                    session.commit()
                    processed += 1
                    continue
                prompt = session.scalar(select(PromptRevision).where(PromptRevision.project_id == generation.project_id, PromptRevision.version == generation.prompt_version))
                if prompt is None or not (prompt.text or "").strip():
                    generation.status, generation.error_message = "failed", "Prompt revision is no longer available"
                    session.commit()
                    continue
                asset_ids = json.loads(generation.reference_asset_ids or "[]")
                assets = _resolve_asset_inputs(session, generation.project_id, asset_ids)
                if not assets:
                    generation.status, generation.error_message = "failed", "Generation input is no longer available"
                    session.commit()
                    continue
                # 按生成片段决定发布原视频还是物理裁片。
                segment = session.get(GenerationSegment, generation.generation_segment_id) if generation.generation_segment_id else None
                video_asset = assets[0]
                request_text = prompt.text
                if generation.generation_batch_id is not None:
                    # 批次任务：从冻结 full prompt 推导当前片段的相对时间提示词。
                    if segment is None or prompt.prompt_mode != "full_reference_video_edit" or prompt.generation_segment_id is not None:
                        generation.status, generation.error_message = "failed", "批次任务的片段或提示词不匹配"
                        session.commit()
                        continue
                    shot_ranges = [(shot.start_sec, shot.end_sec) for shot in session.scalars(select(Shot).where(Shot.timeline_revision_id == prompt.source_timeline_revision_id).order_by(Shot.position))]
                    try:
                        document = validate_full_prompt(prompt.text, shot_ranges)
                        request_text = derive_segment_prompt(
                            document,
                            segment_start_sec=segment.source_start_sec,
                            segment_end_sec=segment.source_end_sec,
                            batch_position=generation.batch_position or 0,
                            batch_size=generation.batch_size or 0,
                        )
                    except Exception as exc:
                        # 确定性错误（提示词/段不匹配），重试无意义，标 failed 而非 retryable。
                        generation.status, generation.error_message = "failed", f"批次提示词推导失败：{exc}"
                        session.commit()
                        continue
                if segment is not None:
                    covers_full_source = segment.source_start_sec <= 0.001 and abs(segment.source_end_sec - (video_asset.duration_sec or segment.source_end_sec)) <= 0.001
                    if not covers_full_source:
                        destination = Settings().media_root / str(generation.project_id) / "generation-segments" / f"plan-{segment.plan_version}" / f"segment-{segment.position}.mp4"
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        # 始终调用：ensure_segment_clip 负责复用存在且有效的文件，或重建丢失/损坏的裁片。
                        ensure_segment_clip(Path(video_asset.original_path), destination, segment.source_start_sec, segment.source_end_sec, Settings().effective_segment_limit_seconds)
                        segment.clip_path = str(destination)
                        session.commit()
                        _refresh_lease(session, generation)
                        video_url = _publish_segment_if_expired(session, segment)
                    else:
                        _refresh_lease(session, generation)
                        video_url = _publish_if_expired(session, video_asset, settings)
                else:
                    _refresh_lease(session, generation)
                    video_url = _publish_if_expired(session, video_asset, settings)
                image_assets = assets[1:]
                reference_manifest = build_reference_image_manifest(image_assets, replace_person=prompt.replace_person)
                request_text = inject_reference_image_manifest(request_text, reference_manifest)
                image_urls = []
                for asset in image_assets:
                    _refresh_lease(session, generation)
                    image_urls.append(_publish_seedance_image(session, asset, settings))
                payload = build_seedance_request(
                    model,
                    request_text,
                    video_url,
                    generation.ratio,
                    generation.duration,
                    generation.generate_audio,
                    image_urls,
                )
                # 审计快照：外包 batch/segment/prompt 元数据，保持发给供应商的 payload 不变。
                snapshot = {
                    "generation_batch_id": str(generation.generation_batch_id) if generation.generation_batch_id else None,
                    "batch_position": generation.batch_position,
                    "batch_size": generation.batch_size,
                    "generation_segment_id": str(segment.id) if segment else None,
                    "plan_version": segment.plan_version if segment else None,
                    "source_start_sec": segment.source_start_sec if segment else None,
                    "source_end_sec": segment.source_end_sec if segment else None,
                    "full_prompt_revision_id": str(prompt.id),
                    "prompt_version": generation.prompt_version,
                    "provider_request": redact_request_urls(payload),
                }
                generation.request_snapshot = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
                session.commit()
                _refresh_lease(session, generation)
                execute_generation_job(session, generation, gateway, payload)
            except Exception as exc:
                _mark_retryable(generation, exc)
            _release(generation, now)
            session.commit()
            processed += 1
    return processed


def _worker_lock_name(queue: str, slot: int) -> str:
    if slot < 1:
        raise ValueError("Worker slot must be at least 1")
    if queue == "all" and slot != 1:
        raise ValueError("Additional worker slots require an explicit ai or generation queue")
    if queue == "all":
        return ".adflow-worker.lock"
    suffix = "" if slot == 1 else f"-{slot}"
    return f".adflow-{queue}-worker{suffix}.lock"


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Run an AdFlow background worker")
    parser.add_argument("--queue", choices=("all", "ai", "generation"), default="all")
    parser.add_argument("--slot", type=int, default=1, help="Unique local worker slot for ai or generation queue")
    args = parser.parse_args()
    queue, slot = args.queue, args.slot
    try:
        lock_name = _worker_lock_name(queue, slot)
    except ValueError as exc:
        parser.error(str(exc))
    require_database_at_head()
    settings = Settings()
    lock_path = settings.media_root / lock_name
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    try:
        import msvcrt
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print(f"Another AdFlow {queue} worker is already running in slot {slot}; this duplicate worker will exit.")
        return
    while True:
        run_once(queue)
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
