from app.services.worker_state import next_poll_at, retry_at
from app.services.full_prompt import FullPromptValidationError
from requests.exceptions import ReadTimeout
from app.db.models import Job
from app.worker import _mark_retryable, _worker_lock_name
from app.db.models import Generation
from app.worker import run_once
from unittest.mock import patch


def test_unactivated_aideo_service_is_not_retried() -> None:
    job = Job(kind="vision_analysis", status="queued", attempts=0)
    _mark_retryable(job, RuntimeError("OperationDenied: Service has not been activated"))
    assert job.status == "failed"
    assert "尚未开通" in job.error_message


def test_prompt_format_failure_is_not_retried_after_internal_repair() -> None:
    job = Job(kind="final_prompt_generation", status="processing", attempts=0)
    _mark_retryable(job, FullPromptValidationError("时间块缺少栏目 保持："))
    assert job.status == "failed"
    assert job.attempts == 1


def test_long_prompt_read_timeout_stops_after_two_attempts() -> None:
    job = Job(kind="final_prompt_generation", status="processing", attempts=0)
    _mark_retryable(job, ReadTimeout("provider did not respond"))
    assert job.status == "retryable"
    _mark_retryable(job, ReadTimeout("provider did not respond"))
    assert job.status == "failed"


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


def test_worker_queue_split_claims_only_its_own_model() -> None:
    with patch("app.worker._claim", return_value=[]) as claim:
        assert run_once("ai") == 0
        assert [item.args[1] for item in claim.call_args_list] == [Job]
        assert claim.call_args_list[0].kwargs["limit"] == 1
        claim.reset_mock()
        assert run_once("generation") == 0
        assert [item.args[1] for item in claim.call_args_list] == [Generation]
        assert claim.call_args_list[0].kwargs["limit"] == 1


def test_ai_worker_slots_have_independent_locks() -> None:
    assert _worker_lock_name("ai", 1) == ".adflow-ai-worker.lock"
    assert _worker_lock_name("ai", 3) == ".adflow-ai-worker-3.lock"


def test_generation_worker_slots_have_independent_locks() -> None:
    assert _worker_lock_name("generation", 1) == ".adflow-generation-worker.lock"
    assert _worker_lock_name("generation", 2) == ".adflow-generation-worker-2.lock"
