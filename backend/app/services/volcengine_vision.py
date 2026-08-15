"""Pure request/result adapter for Volcengine VOD Aideo Vision.

Authentication and polling are deliberately kept outside this module so the
worker added later can retry network failures without duplicating payload logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
import json

from app.core.config import Settings


@dataclass(frozen=True)
class VisionTaskResult:
    task_id: str
    status: str
    content: str | None = None
    error_message: str | None = None


class VisionConfigurationError(RuntimeError):
    pass


class VisionGateway(Protocol):
    def upload_video(self, source: Path) -> str: ...
    def submit_vision(self, vid: str) -> str: ...
    def get_result(self, task_id: str) -> VisionTaskResult: ...


class VolcengineVodVisionGateway:
    """Official VOD SDK upload plus SDK-signed Aideo requests."""

    def __init__(self, settings: Settings):
        validate_vision_configuration(settings)
        from volcengine.vod.VodService import VodService
        from volcengine.vod.models.request.request_vod_pb2 import VodUploadMediaRequest

        self._service = VodService()
        self._service.set_ak(settings.volcengine_access_key)
        self._service.set_sk(settings.volcengine_secret_key)
        self._space_name = settings.volcengine_vod_space
        self._upload_request_type = VodUploadMediaRequest

    def upload_video(self, source: Path) -> str:
        request = self._upload_request_type()
        request.SpaceName = self._space_name
        request.FilePath = str(source)
        request.FileName = source.name
        request.FileExtension = source.suffix
        response = self._service.upload_media(request)
        vid = response.Result.Data.Vid
        if not vid:
            raise RuntimeError("VOD upload completed without a Vid")
        return str(vid)

    def _aideo_json(self, action: str, params: dict[str, Any], body: dict[str, Any] | None = None) -> dict[str, Any]:
        # The installed SDK has no generated methods for this newest API, but its
        # signing transport is official and already configured with AK/SK.
        from volcengine.ApiInfo import ApiInfo

        self._service.api_info[action] = ApiInfo("POST" if body is not None else "GET", "/", {
            "Action": action, "Version": "2025-03-03",
        }, {}, {})
        raw = self._service.json(action, params, json.dumps(body)) if body is not None else self._service.get(action, params)
        return json.loads(raw)

    def submit_vision(self, vid: str) -> str:
        response = self._aideo_json("SubmitAideoTaskAsync", {}, build_submit_vision_payload(self._space_name, vid))
        task_id = response.get("Result", {}).get("TaskId")
        if not task_id:
            raise RuntimeError("Vision submission completed without a TaskId")
        return str(task_id)

    def get_result(self, task_id: str) -> VisionTaskResult:
        return parse_vision_result(self._aideo_json("GetAideoTaskResult", {
            "SpaceName": self._space_name, "TaskId": task_id,
        }))


def validate_vision_configuration(settings: Settings) -> None:
    missing = [name for name, value in {
        "VOLCENGINE_ACCESS_KEY": settings.volcengine_access_key,
        "VOLCENGINE_SECRET_KEY": settings.volcengine_secret_key,
        "VOLCENGINE_VOD_SPACE": settings.volcengine_vod_space,
    }.items() if not value]
    if missing:
        raise VisionConfigurationError("Missing " + ", ".join(missing))


def build_submit_vision_payload(space_name: str, vid: str) -> dict[str, Any]:
    payload = {
        "SpaceName": space_name,
        "MultiInputs": [{"Type": "Vid", "Vid": vid}],
        "SkillType": "Vision",
        "Prompt": (
            "分析完整视频和每个镜头。只输出 JSON：summary、observations、inferences、uncertainties、"
            "shots。每个 shots 项必须有 start_sec、end_sec，可选 people、action、product_interaction、"
            "background、camera、lighting、on_screen_text、observations、inferences、uncertainties。"
            "无法确认的内容写入 uncertainties，不要猜测。"
        ),
    }
    return payload


def parse_vision_result(payload: dict[str, Any]) -> VisionTaskResult:
    result = payload.get("Result", payload)
    task_id = str(result.get("TaskId", ""))
    status = str(result.get("Status", ""))
    if status == "Failed":
        error = result.get("Error", {})
        return VisionTaskResult(task_id, status, error_message=error.get("Message") or error.get("Code"))
    if status != "Completed":
        return VisionTaskResult(task_id, status)
    for response in result.get("ApiResponses", []):
        vision = response.get("Vision")
        if vision:
            return VisionTaskResult(task_id, status, content=vision.get("Content"))
    return VisionTaskResult(task_id, status, error_message="Vision content was not returned")
