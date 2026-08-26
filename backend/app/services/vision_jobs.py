from pathlib import Path
import shutil
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Asset, Job, Shot, ShotAISummary, TimelineRevision, VideoAnalysis
from app.services.analysis_persistence import persist_vision_result
from app.services.media import clip_video
from app.services.vision import parse_vision_facts
from app.services.volcengine_vision import VisionGateway
from app.services.worker_state import MAX_ATTEMPTS, retry_at


def _write_shot_facts(shot: Shot, content: str) -> None:
    bundle = __import__("json").loads(content)
    final = bundle.get("final", bundle)
    # 用户只看到规范化结果；Qwen原始输出另存后台审计表。
    facts = parse_vision_facts(__import__("json").dumps({"summary": "", "shots": [{"start_sec": 0, "end_sec": 1, **final}], "observations": ""}, ensure_ascii=False))
    fact = facts.shots[0] if facts.shots else None
    shot.people = fact.people if fact else None
    shot.action = fact.action if fact else None
    shot.product = final.get("product")
    shot.product_interaction = fact.product_interaction if fact else None
    shot.background = fact.background if fact else None
    shot.camera = fact.camera if fact else None
    shot.lighting = fact.lighting if fact else None
    shot.visual_style = final.get("visual_style")
    shot.on_screen_text = final.get("visible_text")
    shot.keep_unchanged = final.get("keep_unchanged")
    shot.observations = fact.observations if fact else facts.observations
    shot.inferences = fact.inferences if fact else facts.inferences
    shot.uncertainties = fact.uncertainties if fact else facts.uncertainties


def execute_vision_job(session: Session, job: Job, gateway: VisionGateway) -> Job:
    """Advance one persisted Vision job once; safe for an external worker to retry."""
    asset = session.scalar(
        select(Asset).where(Asset.project_id == job.project_id, Asset.kind == "reference_video").order_by(Asset.id.desc())
    )
    if asset is None:
        job.status, job.error_message = "failed", "Reference video is missing"
        session.commit()
        return job
    try:
        shot = session.get(Shot, job.shot_id) if job.kind == "vision_shot_analysis" and job.shot_id else None
        if shot is not None:
            latest_revision = session.scalar(
                select(TimelineRevision)
                .where(TimelineRevision.project_id == job.project_id)
                .order_by(TimelineRevision.version.desc())
            )
            if latest_revision is None or shot.timeline_revision_id != latest_revision.id:
                job.status, job.error_message = "superseded", "时间轴已更新，旧镜头任务已作废"
                session.commit()
                return job
            # 同一次任务的分阶段结果可复用；新任务不会误用旧的模型结果。
            set_context = getattr(gateway, "set_job_context", None)
            if set_context:
                set_context(job.id, shot.start_sec)
        if not job.provider_input_id:
            source = Path(asset.original_path)
            if job.kind == "vision_shot_analysis":
                if shot is None or shot.timeline_revision.project_id != job.project_id:
                    job.status, job.error_message = "failed", "The selected shot is missing"
                    session.commit()
                    return job
                source = clip_video(source, source.parent.parent / "analysis-clips" / f"{shot.id}.mp4", shot.start_sec, shot.end_sec)
            job.provider_input_id = gateway.upload_video(source)
            job.status = "uploaded"
            session.commit()
        if not job.external_task_id:
            job.external_task_id = gateway.submit_vision(job.provider_input_id)
            job.status = "processing"
            session.commit()
        result = gateway.get_result(job.external_task_id)
        if result.status == "Completed" and result.content is not None:
            if job.kind == "vision_shot_analysis":
                session.refresh(job)
                if job.status == "superseded":
                    session.commit()
                    return job
                shot = session.get(Shot, job.shot_id) if job.shot_id else None
                if shot is None:
                    raise ValueError("The selected shot is missing")
                bundle = __import__("json").loads(result.content)
                # 原始模型判断只在后台保留，既不返回前端，也不参与最终提示词。
                if "qwen" in bundle:
                    session.add(VideoAnalysis(project_id=job.project_id, job_id=job.id, shot_id=shot.id, provider="qwen_full_shot", raw_content=__import__("json").dumps(bundle["qwen"], ensure_ascii=False)))
                latest_version = session.scalar(select(func.max(ShotAISummary.version)).where(ShotAISummary.shot_id == shot.id)) or 0
                session.add(ShotAISummary(shot_id=shot.id, version=latest_version + 1, content=__import__("json").dumps(bundle.get("final", bundle), ensure_ascii=False)))
                _write_shot_facts(shot, result.content)
                shot.analysis_status, shot.analysis_error = "succeeded", None
            else:
                persist_vision_result(session, job.project_id, job.id, result.content, parse_vision_facts(result.content))
            job.status, job.error_message = "completed", None
        elif result.status == "Failed":
            job.status, job.error_message = "failed", result.error_message
            if job.kind == "vision_shot_analysis" and job.shot_id:
                shot = session.get(Shot, job.shot_id)
                if shot:
                    shot.analysis_status, shot.analysis_error = "failed", result.error_message
        else:
            job.status = "processing"
        session.commit()
        if job.status == "completed" and job.kind == "vision_shot_analysis":
            # 原始双模型回答已入库，临时裁片和抽帧缓存无需无限占用磁盘。
            clip = Path(job.provider_input_id) if job.provider_input_id else None
            result_path = Path(job.external_task_id) if job.external_task_id else None
            if clip and clip.parent.name == "analysis-clips":
                clip.unlink(missing_ok=True)
            if result_path and result_path.parent.parent.name == "dual-shot-vision":
                shutil.rmtree(result_path.parent, ignore_errors=True)
    except Exception as exc:
        job.attempts += 1
        job.error_message = str(exc)
        if job.attempts >= MAX_ATTEMPTS:
            job.status = "failed"
        else:
            job.status = "retryable"
            job.next_attempt_at = retry_at(datetime.now(UTC), job.attempts)
        session.commit()
    return job
