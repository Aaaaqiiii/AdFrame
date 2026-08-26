import re

import pytest
import requests

from app.services.seedance import JsonTaskGateway, SubmissionUncertainError, build_seedance_request, parse_generation_result, sanitize_provider_summary


def test_submit_timeout_is_classified_as_uncertain() -> None:
    class Http:
        def post(self, *_args, **_kwargs):
            raise requests.Timeout("read timed out")
    gateway = JsonTaskGateway("https://provider", "secret", http=Http())
    with pytest.raises(SubmissionUncertainError):
        gateway.submit({"model": "seedance"})


def test_submit_preserves_provider_error_body() -> None:
    response = requests.Response()
    response.status_code = 400
    response.reason = "Bad Request"
    response._content = b'{"error":{"code":"InvalidParameter","message":"duration is invalid"}}'

    class Http:
        def post(self, *_args, **_kwargs):
            return response

    gateway = JsonTaskGateway("https://provider", "secret", http=Http())
    with pytest.raises(RuntimeError, match="InvalidParameter.*duration is invalid"):
        gateway.submit({"model": "seedance"})


def test_sanitize_provider_summary_redacts_secrets() -> None:
    summary = sanitize_provider_summary({"model": "seedance", "Authorization": "Bearer secret-key", "api_key": "sk-123456"})
    assert "secret-key" not in summary
    assert "sk-123456" not in summary
    assert "***" in summary
    assert len(summary) <= 4000


def test_official_request_uses_public_reference_video_and_audio_switch() -> None:
    request = build_seedance_request(
        model="doubao-seedance-1-0-pro-250528", prompt="keep product", reference_video_url="https://tempfile.org/a/download",
        ratio="9:16", duration=5, generate_audio=False,
    )

    assert request["content"][0] == {"type": "text", "text": "keep product"}
    assert request["content"][1] == {"type": "video_url", "role": "reference_video", "video_url": {"url": "https://tempfile.org/a/download"}}
    assert request["generate_audio"] is False


def test_request_includes_only_enabled_reference_images() -> None:
    request = build_seedance_request(
        "doubao-seedance-2.5", "提示词", "https://tempfile.org/video/download", "adaptive", -1, False,
        reference_image_urls=["https://tempfile.org/person/download"],
    )

    assert request["content"][2] == {"type": "image_url", "role": "reference_image", "image_url": {"url": "https://tempfile.org/person/download"}}


def test_generation_result_parser_handles_success_and_failure() -> None:
    succeeded = parse_generation_result({"id": "task-1", "status": "succeeded", "content": {"video_url": "https://cdn/video.mp4"}})
    failed = parse_generation_result({"id": "task-2", "status": "failed", "error": {"message": "bad prompt"}})

    assert succeeded.status == "completed"
    assert succeeded.video_url == "https://cdn/video.mp4"
    assert failed.status == "failed"
    assert failed.error_message == "bad prompt"


def test_generation_result_parser_accepts_provider_task_id_and_nested_video_urls() -> None:
    result = parse_generation_result({
        "data": {"task_id": "comfly-task", "status": "succeeded", "result": {"url": "https://cdn/result.mp4"}}
    })

    assert result.task_id == "comfly-task"
    assert result.status == "completed"
    assert result.video_url == "https://cdn/result.mp4"


def test_gateway_uses_configured_full_task_path_and_task_id_response() -> None:
    class Response:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict: return {"data": {"task_id": "provider-task"}}

    class Http:
        def __init__(self) -> None: self.url = ""
        def post(self, url: str, **_: object) -> Response: self.url = url; return Response()

    http = Http()
    gateway = JsonTaskGateway("https://ai.comfly.chat", "key", task_path="/seedance/v3/contents/generations/tasks", http=http)

    assert gateway.submit({"model": "doubao-seedance-2.5"}) == "provider-task"
    assert http.url == "https://ai.comfly.chat/seedance/v3/contents/generations/tasks"
