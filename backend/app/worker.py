"""Run with: python -m app.worker"""
from __future__ import annotations

import time
import json
import socket
from pathlib import Path
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from app.core.config import Settings
from app.db.models import Generation, Job
from app.db.migrations import require_database_at_head
from app.db.session import SessionLocal
from app.services.generation_jobs import execute_generation_job
from app.services.seedance import JsonTaskGateway, build_seedance_request
from app.services.comfly_frame_vision import ComflyFrameVisionGateway
from app.services.dual_shot_vision import DualShotVisionGateway
from app.services.vision_jobs import execute_vision_job
from app.services.reference_profiles import execute_profile_job
from app.services.final_prompt import execute_final_prompt_job, execute_prompt_refinement_job
from app.services.worker_state import MAX_ATTEMPTS, next_poll_at, retry_at


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


def _claim(session, model, kinds: list[str] | None, worker_id: str, now: datetime):
    predicates = [model.status.in_(["queued", "uploaded", "processing", "retryable"]), or_(model.next_attempt_at.is_(None), model.next_attempt_at <= now), or_(model.leased_at.is_(None), model.leased_at <= now - timedelta(minutes=10))]
    if kinds:
        predicates.append(model.kind.in_(kinds))
    records = session.scalars(select(model).where(*predicates).order_by(model.id).with_for_update(skip_locked=True).limit(5)).all()
    for record in records:
        record.leased_at, record.leased_by = now, worker_id
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
        vision_jobs = _claim(session, Job, ["vision_analysis", "vision_shot_analysis", "reference_profile_analysis", "final_prompt_generation", "prompt_refinement"], worker_id, now)
        for job in vision_jobs:
            try:
                if job.kind == "reference_profile_analysis":
                    execute_profile_job(session, job, settings)
                elif job.kind == "final_prompt_generation":
                    execute_final_prompt_job(session, job, settings)
                elif job.kind == "prompt_refinement":
                    execute_prompt_refinement_job(session, job, settings)
                else:
                    # 完整视频旧任务保持兼容；人工确认后的逐镜任务使用双模型链路。
                    gateway = DualShotVisionGateway(settings) if job.kind == "vision_shot_analysis" else ComflyFrameVisionGateway(settings)
                    execute_vision_job(session, job, gateway)
            except Exception as exc:
                _mark_retryable(job, exc)
                if job.kind in {"final_prompt_generation", "prompt_refinement"} and job.provider_input_id:
                    from uuid import UUID
                    from app.db.models import PromptRevision
                    revision = session.get(PromptRevision, UUID(job.provider_input_id))
                    if revision:
                        revision.status, revision.error_message = job.status, job.error_message
            _release(job, now)
            session.commit()
            processed += 1
        generations = _claim(session, Generation, None, worker_id, now)
        for generation in generations:
            try:
                gateway, model = _generation_gateway(settings, generation.provider)
                from app.db.models import Asset, PromptRevision
                asset = session.scalar(select(Asset).where(Asset.project_id == generation.project_id, Asset.kind == "reference_video").order_by(Asset.id.desc()))
                prompt = session.scalar(select(PromptRevision).where(PromptRevision.project_id == generation.project_id, PromptRevision.version == generation.prompt_version))
                if asset is None or prompt is None or not asset.public_url:
                    generation.status, generation.error_message = "failed", "Generation input is no longer available"
                    session.commit()
                else:
                    execute_generation_job(
                        session,
                        generation,
                        gateway,
                        build_seedance_request(
                            model,
                            prompt.text,
                            asset.public_url,
                            generation.ratio,
                            generation.duration,
                            generation.generate_audio,
                            json.loads(generation.reference_image_urls or "[]"),
                        ),
                    )
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
