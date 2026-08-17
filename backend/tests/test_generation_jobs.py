from unittest.mock import Mock

import pytest

from app.db.session import SessionLocal
from app.services.generation_jobs import execute_generation_job
from app.services.seedance import SubmissionUncertainError


def test_uncertain_submission_is_never_automatically_retried(queued_generation) -> None:
    gateway = Mock()
    gateway.submit.side_effect = SubmissionUncertainError("供应商提交结果不明确")
    with SessionLocal() as session:
        generation = session.get(type(queued_generation), queued_generation.id)
        execute_generation_job(session, generation, gateway, {"model": "seedance"})
        assert generation.status == "submission_uncertain"
        assert generation.next_attempt_at is None
        assert generation.attempts == 1


def test_task_id_is_committed_before_first_poll(queued_generation) -> None:
    observed: list[str] = []

    class Gateway:
        def __init__(self):
            self._called_poll = False

        def submit(self, payload):
            return "provider-task-99"

        def get_result(self, task_id):
            # Simulate a concurrent reader by opening a second session.
            with SessionLocal() as other:
                row = other.get(type(queued_generation), queued_generation.id)
                observed.append((row.status, row.external_task_id))
            return Mock(task_id=task_id, status="processing", video_url=None, error_message=None)

    with SessionLocal() as session:
        generation = session.get(type(queued_generation), queued_generation.id)
        execute_generation_job(session, generation, Gateway(), {"model": "seedance"})
    assert observed and observed[0] == ("processing", "provider-task-99")
