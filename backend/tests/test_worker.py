from app.services.worker_state import next_poll_at, retry_at
from app.db.models import Job
from app.worker import _mark_retryable


def test_unactivated_aideo_service_is_not_retried() -> None:
    job = Job(kind="vision_analysis", status="queued", attempts=0)
    _mark_retryable(job, RuntimeError("OperationDenied: Service has not been activated"))
    assert job.status == "failed"
    assert "尚未开通" in job.error_message


def test_retry_delay_grows_and_is_capped() -> None:
    from datetime import UTC, datetime
    now = datetime(2026, 8, 12, tzinfo=UTC)

    assert retry_at(now, 1).second == 5
    assert (retry_at(now, 10) - now).total_seconds() == 300


def test_poll_delay_slows_down_for_long_running_tasks() -> None:
    from datetime import UTC, datetime
    now = datetime(2026, 8, 12, tzinfo=UTC)

    assert (next_poll_at(now, now, "processing") - now).total_seconds() == 5
    assert (next_poll_at(now, now.replace(minute=2), "processing") - now.replace(minute=2)).total_seconds() == 15
    assert (next_poll_at(now, now.replace(minute=11), "processing") - now.replace(minute=11)).total_seconds() == 30
