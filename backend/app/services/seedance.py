from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import requests


class SubmissionUncertainError(RuntimeError):
    """Provider submission outcome is unknown; never automatically resubmit."""


def sanitize_provider_summary(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    # 完整吞掉授权头的值（含 "Bearer ..."），其余 key 值只保留首段脱敏。
    text = re.sub(r"(?i)(authorization)(\"?\s*[:=]\s*\"?)[^\"]*\"?", r"\1\2***", text)
    text = re.sub(r"(?i)(api[_-]?key|token)(\"?\s*[:=]\s*\"?)[^\",\s]+", r"\1\2***", text)
    return text[:4000]


@dataclass(frozen=True)
class GenerationResult:
    task_id: str
    status: str
    video_url: str | None = None
    error_message: str | None = None


class GenerationGateway(Protocol):
    def submit(self, payload: dict[str, Any]) -> str: ...
    def get_result(self, task_id: str) -> GenerationResult: ...


def build_seedance_request(model: str, prompt: str, reference_video_url: str, ratio: str, duration: int, generate_audio: bool, reference_image_urls: list[str] | None = None) -> dict[str, Any]:
    content = [
        {"type": "text", "text": prompt},
        {"type": "video_url", "role": "reference_video", "video_url": {"url": reference_video_url}},
    ]
    content.extend({"type": "image_url", "role": "reference_image", "image_url": {"url": url}} for url in (reference_image_urls or []))
    return {
        "model": model,
        "content": content,
        "ratio": ratio,
        "duration": duration,
        "generate_audio": generate_audio,
    }


def _first_string(*values: Any) -> str | None:
    return next((value for value in values if isinstance(value, str) and value), None)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_generation_result(payload: dict[str, Any]) -> GenerationResult:
    data = _dict(payload.get("data"))
    result = _dict(data.get("result") or payload.get("result") or data or payload)
    nested_result = _dict(result.get("result"))
    content = _dict(result.get("content"))
    nested_content = _dict(content.get("content"))
    nested_data = _dict(data.get("data"))
    task_id = str(_first_string(result.get("id"), result.get("task_id"), data.get("id"), data.get("task_id"), payload.get("id"), payload.get("task_id")) or "")
    raw_status = str(result.get("status") or data.get("status") or payload.get("status") or "queued").lower()
    status = {"succeeded": "completed", "success": "completed", "completed": "completed", "running": "processing", "pending": "queued"}.get(raw_status, raw_status)
    video_url = _first_string(
        content.get("video_url"), nested_content.get("video_url"), result.get("video_url"),
        data.get("video_url"), nested_result.get("video_url"), nested_result.get("url"), result.get("url"), nested_data.get("video_url"),
        *([item.get("video_url") or item.get("url") for item in result.get("content", []) if isinstance(item, dict)] if isinstance(result.get("content"), list) else []),
    )
    error = _dict(result.get("error"))
    return GenerationResult(task_id, status, video_url, error.get("message") or result.get("error_message"))


class JsonTaskGateway:
    def __init__(self, base_url: str, api_key: str, task_path: str = "/contents/generations/tasks", http: requests.Session | None = None):
        if not base_url or not api_key:
            raise ValueError("Generation provider endpoint and API key are required")
        if not task_path.startswith("/"):
            raise ValueError("Generation task path must begin with '/'")
        self._base_url, self._task_path, self._api_key, self._http = base_url.rstrip("/"), task_path.rstrip("/"), api_key, http or requests.Session()

    def submit(self, payload: dict[str, Any]) -> str:
        try:
            response = self._http.post(f"{self._base_url}{self._task_path}", headers={"Authorization": f"Bearer {self._api_key}"}, json=payload, timeout=(10, 60))
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise SubmissionUncertainError(f"供应商提交结果不明确：{exc}") from exc
        response.raise_for_status()
        body = response.json()
        task_id = _first_string(body.get("id"), body.get("task_id"), _dict(body.get("data")).get("id"), _dict(body.get("data")).get("task_id"))
        if not task_id:
            raise RuntimeError("Generation submission did not return a task ID")
        return str(task_id)

    def get_result(self, task_id: str) -> GenerationResult:
        response = self._http.get(f"{self._base_url}{self._task_path}/{task_id}", headers={"Authorization": f"Bearer {self._api_key}"}, timeout=(10, 30))
        response.raise_for_status()
        return parse_generation_result(response.json())
