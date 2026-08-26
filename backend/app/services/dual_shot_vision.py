from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from uuid import UUID

import requests

from app.core.config import Settings
from app.services.media import concat_videos_lossless, probe_video
from app.services.tempfile_publisher import TempfilePublisher
from app.services.volcengine_vision import VisionConfigurationError, VisionTaskResult


FACT_FIELDS = (
    "people", "action", "product", "product_interaction", "background", "camera",
    "lighting", "visual_style", "visible_text", "keep_unchanged", "uncertainties",
)
QWEN_MIN_VIDEO_SECONDS = 3.0
# Base64约膨胀1/3；保守控制在供应商10 MB Data URI限制以内。
QWEN_DIRECT_VIDEO_MAX_BYTES = 7_000_000


def validate_dual_vision_configuration(settings: Settings) -> None:
    """逐镜Qwen理解通过Comfly调用，缺少Key时不创建半成品任务。"""
    if not settings.comfly_api_key:
        raise VisionConfigurationError("请先配置Comfly API Key")


def _json_object(text: str) -> dict:
    """Accept fenced JSON and harmless text surrounding one JSON object."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start = text.find("{")
    if start < 0:
        raise ValueError("模型没有返回 JSON 对象")
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, dict):
        raise ValueError("模型返回内容不是 JSON 对象")
    return value


def _raise_provider_error(response: requests.Response, provider: str) -> None:
    """保留供应商4xx正文，后台才能显示真正的字段或权限错误。"""
    if response.ok:
        return
    try:
        body = response.json()
        error = body.get("error", {})
        message = error.get("message") if isinstance(error, dict) else str(error)
        message = message or body.get("message") or json.dumps(body, ensure_ascii=False)
    except (ValueError, AttributeError):
        message = response.text[:2000]
    raise RuntimeError(f"{provider} HTTP {response.status_code}: {message}")


def _completion_text(response: requests.Response) -> str:
    _raise_provider_error(response, "Comfly")
    return response.json()["choices"][0]["message"]["content"]


class DualShotVisionGateway:
    """Qwen直接观看完整镜头并返回可编辑的分镜事实。"""

    def __init__(self, settings: Settings, publisher: TempfilePublisher | None = None):
        validate_dual_vision_configuration(settings)
        self.settings = settings
        self.publisher = publisher or TempfilePublisher()
        self._job_id: UUID | None = None
        self._absolute_start_sec = 0.0

    def set_job_context(self, job_id: UUID, absolute_start_sec: float) -> None:
        self._job_id = job_id
        self._absolute_start_sec = absolute_start_sec

    def upload_video(self, source: Path) -> str:
        # 保留本地路径，Qwen直接接收完整镜头。
        return str(source.resolve())

    def _prepare_qwen_video(self, source: Path, work_dir: Path) -> tuple[Path, float, int]:
        """过短镜头用码流复制循环到3秒；不缩放、不重采样、不重新编码。"""
        duration = probe_video(source).duration_sec
        if duration <= 0:
            raise ValueError("镜头时长必须大于0秒")
        if duration >= QWEN_MIN_VIDEO_SECONDS:
            return source, duration, 1
        repeat_count = math.ceil(QWEN_MIN_VIDEO_SECONDS / duration)
        transport = work_dir / "qwen-short-loop.mp4"
        if not transport.is_file():
            concat_videos_lossless([source] * repeat_count, transport)
        return transport, duration, repeat_count

    def _qwen_understand(self, source: Path, work_dir: Path) -> dict:
        transport, original_duration, repeat_count = self._prepare_qwen_video(source, work_dir)
        if transport.stat().st_size <= QWEN_DIRECT_VIDEO_MAX_BYTES:
            encoded = base64.b64encode(transport.read_bytes()).decode("ascii")
            video_url = f"data:video/mp4;base64,{encoded}"
        else:
            # 临时发布的是原分辨率、原码流裁片，不做压缩或转码。
            video_url = self.publisher.publish(transport, "video/mp4").url
        repeat_note = (
            f"原镜头仅{original_duration:.3f}秒；为满足接口最短时长，视频由原镜头无损循环{repeat_count}次。"
            "只分析第一轮动作，后续轮次是同一证据的重复，禁止理解为连续重复动作。"
            if repeat_count > 1 else
            f"这是一个时长{original_duration:.3f}秒的原始单独镜头。"
        )
        schema = {field: "" for field in FACT_FIELDS}
        response = requests.post(
            f"{self.settings.comfly_vision_base_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.comfly_api_key}"},
            json={
                "model": self.settings.comfly_qwen_vision_model,
                "messages": [{"role": "user", "content": [
                    {"type": "video_url", "video_url": {"url": video_url}, "fps": 4 if original_duration < QWEN_MIN_VIDEO_SECONDS else 2},
                    {"type": "text", "text": (
                        f"完整观看这个单独镜头。它在原视频中的绝对起点是{self._absolute_start_sec:.3f}秒。{repeat_note}"
                        "按时间顺序描述人物、完整动作、产品、产品交互、背景、镜头、光线、视觉风格、可见文字、应保持内容和影响改编的不确定项。"
                        f"只返回以下字段的JSON对象，不要Markdown：{json.dumps(schema, ensure_ascii=False)}"
                    )},
                ]}],
                "temperature": 0.1,
                "max_tokens": 2200,
            },
            timeout=(15, 300),
        )
        return _json_object(_completion_text(response))

    def submit_vision(self, source_path: str) -> str:
        source = Path(source_path)
        if self._job_id is None:
            raise RuntimeError("镜头理解任务缺少任务上下文")
        work_dir = source.parent.parent / "dual-shot-vision" / str(self._job_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        qwen_path = work_dir / "qwen.json"
        if qwen_path.exists():
            qwen = json.loads(qwen_path.read_text(encoding="utf-8"))
        else:
            qwen = self._qwen_understand(source, work_dir)
            qwen_path.write_text(json.dumps(qwen, ensure_ascii=False), encoding="utf-8")
        final_path = work_dir / "final.json"
        if final_path.exists():
            final = json.loads(final_path.read_text(encoding="utf-8"))
        else:
            final = {field: qwen.get(field, "") for field in FACT_FIELDS}
            final_path.write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")
        result = work_dir / "result.json"
        # 原始Qwen回答用于故障追踪；execute_vision_job只把final写入用户事实字段。
        result.write_text(json.dumps({"qwen": qwen, "final": final}, ensure_ascii=False), encoding="utf-8")
        return str(result)

    def get_result(self, task_id: str) -> VisionTaskResult:
        path = Path(task_id)
        if not path.is_file():
            return VisionTaskResult(task_id, "Failed", error_message="逐镜双模型理解结果不存在")
        return VisionTaskResult(task_id, "Completed", content=path.read_text(encoding="utf-8"))
