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

from app.core.config import Settings
from app.db.models import Asset, Generation, GenerationSegment, Job, PromptRevision
from app.db.migrations import require_database_at_head
from app.db.session import SessionLocal
from app.services.generation_jobs import execute_generation_job
from app.services.media import ensure_segment_clip
from app.services.seedance import JsonTaskGateway, build_seedance_request
from app.services.tempfile_publisher import TempfilePublisher
from app.services.comfly_frame_vision import ComflyFrameVisionGateway
from app.services.dual_shot_vision import DualShotVisionGateway
from app.services.vision_jobs import execute_vision_job
from app.services.reference_profiles import execute_profile_job
from app.services.final_prompt import execute_final_prompt_job, execute_prompt_refinement_job, execute_selling_point_optimization_job
from app.services.worker_state import MAX_ATTEMPTS, next_poll_at, retry_at


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


def _publish_if_expired(session, asset: Asset, settings: Settings) -> str:
    """Republish an expired or missing temporary URL; otherwise reuse the stored one."""
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
    record.error_message = raw
    if record.attempts >= MAX_ATTEMPTS:
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
):
    predicates = [
        model.status.in_(statuses),
        or_(model.next_attempt_at.is_(None), model.next_attempt_at <= now),
        or_(model.leased_at.is_(None), model.leased_at <= now - timedelta(minutes=10)),
    ]
    if kinds:
        predicates.append(model.kind.in_(kinds))
    records = session.scalars(
        select(model)
        .where(*predicates)
        .order_by(model.id)
        .with_for_update(skip_locked=True)
        .limit(5)
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


def run_once() -> int:
    settings = Settings()
    processed = 0
    now = datetime.now(UTC)
    worker_id = f"{socket.gethostname()}:{__import__('os').getpid()}"
    with SessionLocal() as session:
        vision_jobs = _claim(session, Job, ["vision_analysis", "vision_shot_analysis", "reference_profile_analysis", "final_prompt_generation", "prompt_refinement", "prompt_selling_point_optimization"], worker_id, now, statuses=("queued", "uploaded", "processing", "retryable"))
        for job in vision_jobs:
            try:
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
                _mark_retryable(job, exc)
                if job.kind in {"final_prompt_generation", "prompt_refinement", "prompt_selling_point_optimization"} and job.provider_input_id:
                    revision = session.get(PromptRevision, UUID(job.provider_input_id))
                    if revision:
                        revision.status, revision.error_message = job.status, job.error_message
            _release(job, now)
            session.commit()
            processed += 1
        generations = _claim(session, Generation, None, worker_id, now, statuses=("queued", "processing", "retryable"))
        for generation in generations:
            try:
                gateway, model = _generation_gateway(settings, generation.provider)
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
                if segment is not None:
                    covers_full_source = segment.source_start_sec <= 0.001 and abs(segment.source_end_sec - (video_asset.duration_sec or segment.source_end_sec)) <= 0.001
                    if not covers_full_source:
                        destination = Settings().media_root / str(generation.project_id) / "generation-segments" / f"plan-{segment.plan_version}" / f"segment-{segment.position}.mp4"
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        # 始终调用：ensure_segment_clip 负责复用存在且有效的文件，或重建丢失/损坏的裁片。
                        ensure_segment_clip(Path(video_asset.original_path), destination, segment.source_start_sec, segment.source_end_sec, Settings().effective_segment_limit_seconds)
                        segment.clip_path = str(destination)
                        session.commit()
                        video_url = _publish_segment_if_expired(session, segment)
                    else:
                        video_url = _publish_if_expired(session, video_asset, settings)
                else:
                    video_url = _publish_if_expired(session, video_asset, settings)
                image_urls = [_publish_if_expired(session, asset, settings) for asset in assets[1:]]
                payload = build_seedance_request(
                    model,
                    prompt.text,
                    video_url,
                    generation.ratio,
                    generation.duration,
                    generation.generate_audio,
                    image_urls,
                )
                # 审计快照：外包 segment/prompt 元数据，保持发给供应商的 payload 不变。
                snapshot = {
                    "generation_segment_id": str(segment.id) if segment else None,
                    "plan_version": segment.plan_version if segment else None,
                    "source_start_sec": segment.source_start_sec if segment else None,
                    "source_end_sec": segment.source_end_sec if segment else None,
                    "prompt_version": generation.prompt_version,
                    "provider_request": redact_request_urls(payload),
                }
                generation.request_snapshot = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
                session.commit()
                execute_generation_job(session, generation, gateway, payload)
            except Exception as exc:
                _mark_retryable(generation, exc)
            _release(generation, now)
            session.commit()
            processed += 1
    return processed


def main() -> None:
    require_database_at_head()
    settings = Settings()
    lock_path = settings.media_root / ".adflow-worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    try:
        import msvcrt
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("Another AdFlow worker is already running; this duplicate worker will exit.")
        return
    while True:
        run_once()
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
