from datetime import UTC, datetime
from pathlib import Path

import requests
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Generation
from app.services.seedance import GenerationGateway, GenerationResult
from app.services.worker_state import MAX_ATTEMPTS, retry_at

MAX_GENERATED_VIDEO_BYTES = 1024 * 1024 * 1024


def _download_result(source_url: str, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    try:
        with requests.get(source_url, stream=True, timeout=(10, 120)) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("video/"):
                raise RuntimeError("Generated result is not a video")
            expected = int(response.headers.get("content-length", "0") or 0)
            if expected > MAX_GENERATED_VIDEO_BYTES:
                raise RuntimeError("Generated result exceeds the download size limit")
            total = 0
            with temporary.open("xb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        total += len(chunk)
                        if total > MAX_GENERATED_VIDEO_BYTES:
                            raise RuntimeError("Generated result exceeds the download size limit")
                        output.write(chunk)
        from app.services.media import probe_video
        probe_video(temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def execute_generation_job(session: Session, generation: Generation, gateway: GenerationGateway, payload: dict) -> Generation:
    try:
        if not generation.external_task_id:
            generation.external_task_id = gateway.submit(payload)
            generation.status = "processing"
            session.commit()
        result: GenerationResult = gateway.get_result(generation.external_task_id)
        generation.status = result.status
        generation.result_url = result.video_url
        generation.error_message = result.error_message
        if result.status == "completed" and result.video_url:
            destination = Settings().media_root / str(generation.project_id) / "generated" / f"v{generation.version}.mp4"
            destination.parent.mkdir(parents=True, exist_ok=True)
            _download_result(result.video_url, destination)
            generation.result_path = str(destination)
        session.commit()
    except Exception as exc:
        generation.attempts += 1
        generation.error_message = str(exc)
        if generation.attempts >= MAX_ATTEMPTS:
            generation.status = "failed"
        else:
            generation.status = "retryable"
            generation.next_attempt_at = retry_at(datetime.now(UTC), generation.attempts)
        session.commit()
    return generation
