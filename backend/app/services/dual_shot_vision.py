from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from uuid import UUID

import requests

from app.core.config import Settings
from app.services.media import extract_keyframes, probe_video
from app.services.tempfile_publisher import TempfilePublisher
from app.services.volcengine_vision import VisionConfigurationError, VisionTaskResult


FACT_FIELDS = (
    "people", "action", "product", "product_interaction", "background", "camera",
    "lighting", "visual_style", "visible_text", "keep_unchanged", "uncertainties",
)


def validate_dual_vision_configuration(settings: Settings) -> None:
    """逐镜理解同时依赖方舟和 Comfly，两者缺一时不创建半成品任务。"""
    missing = []
    if not settings.volcengine_api_key:
        missing.append("火山方舟 API Key")
    if not settings.comfly_api_key:
        missing.append("Comfly API Key")
    if missing:
        raise VisionConfigurationError(f"请先配置{'、'.join(missing)}")


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


def _responses_text(response: requests.Response) -> str:
    """Extract final text from the official Volcengine Responses payload."""
    _raise_provider_error(response, "火山方舟")
    for item in response.json().get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return content["text"]
    raise ValueError("火山方舟没有返回文本结果")


class DualShotVisionGateway:
    """豆包看完整镜头、GPT看连续帧，最后仅返回GPT综合后的事实。"""

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
        # 保留本地路径；上传仅发生在豆包完整镜头调用时，GPT只接收抽取帧。
        return str(source.resolve())

    def _doubao_understand(self, source: Path) -> dict:
        base = self.settings.volcengine_seedance_base_url.rstrip('/')
        headers = {"Authorization": f"Bearer {self.settings.volcengine_api_key}"}
        # 本地镜头使用方舟 Files API；第三方临时链接会被方舟判定为 Invalid video_url。
        with source.open("rb") as stream:
            uploaded = requests.post(
                f"{base}/api/v3/files", headers=headers,
                files={"file": (source.name, stream, "video/mp4")},
                data={"purpose": "user_data", "preprocess_configs[video][fps]": "2"},
                timeout=(15, 300),
            )
        _raise_provider_error(uploaded, "火山方舟文件上传")
        file_id = uploaded.json().get("id")
        if not file_id:
            raise RuntimeError("火山方舟文件上传成功但没有返回 file_id")
        for _ in range(150):
            file_response = requests.get(f"{base}/api/v3/files/{file_id}", headers=headers, timeout=(15, 30))
            _raise_provider_error(file_response, "火山方舟文件预处理")
            file_status = file_response.json().get("status", "")
            if file_status in {"active", "processed", "completed", "ready", "succeeded"}:
                break
            if file_status in {"failed", "error", "cancelled"}:
                raise RuntimeError(f"火山方舟视频预处理失败：{file_response.text[:1000]}")
            time.sleep(2)
        else:
            raise TimeoutError("火山方舟视频预处理超过5分钟")
        response = requests.post(
            f"{base}/api/v3/responses", headers={**headers, "Content-Type": "application/json"},
            json={"model": self.settings.volcengine_vision_model, "input": [{"role": "user", "content": [
                {"type": "input_video", "file_id": file_id},
                {"type": "input_text", "text": "完整观看这个单独镜头，描述人物、完整动作、产品、产品交互、背景、镜头、光线、视觉风格、可见文字和影响改编的不确定项。只返回JSON。"},
            ]}]}, timeout=(15, 300),
        )
        return _json_object(_responses_text(response))

    def _gpt_understand(self, source: Path, work_dir: Path) -> dict:
        duration = probe_video(source).duration_sec
        # 短镜头3帧、普通镜头5帧、长镜头按时长增加，最多12帧。
        count = 3 if duration < 2 else 5 if duration < 6 else min(12, 7 + int(duration // 8))
        margin = min(0.08, duration / 10)
        timestamps = [margin + (duration - 2 * margin) * index / (count - 1) for index in range(count)]
        frames = extract_keyframes(source, work_dir / "frames", timestamps)
        content: list[dict] = [{"type": "text", "text": "这些图片是同一镜头按时间排列的连续证据帧。独立分析，不参考其他模型。只返回JSON，字段与人物、动作、产品、产品交互、背景、镜头、光线、视觉风格、可见文字、不确定项对应。"}]
        for frame in frames:
            encoded = base64.b64encode(frame.path.read_bytes()).decode("ascii")
            content.extend([
                {"type": "text", "text": f"原视频绝对时间 {self._absolute_start_sec + frame.timestamp_sec:.3f} 秒；镜头内时间 {frame.timestamp_sec:.3f} 秒"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
            ])
        response = requests.post(
            f"{self.settings.comfly_vision_base_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.comfly_api_key}"},
            json={"model": self.settings.comfly_vision_model, "messages": [{"role": "user", "content": content}], "temperature": 0.1, "max_tokens": 2200},
            timeout=(15, 300),
        )
        return _json_object(_completion_text(response))

    def _synthesize(self, doubao: dict, gpt: dict) -> dict:
        # 综合调用只输出用户可编辑的最终事实，不暴露模型分歧或内部置信度。
        schema = {field: "" for field in FACT_FIELDS}
        response = requests.post(
            f"{self.settings.comfly_vision_base_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.comfly_api_key}"},
            json={
                "model": self.settings.comfly_vision_model,
                "messages": [{"role": "system", "content": "你负责把两份独立镜头理解综合成一份可人工修改的最终事实。只保留后续广告改编需要的可见事实；无法可靠确认且影响改编的内容写入uncertainties。只返回规定JSON。"}, {"role": "user", "content": f"输出格式：{json.dumps(schema, ensure_ascii=False)}\n豆包完整镜头理解：{json.dumps(doubao, ensure_ascii=False)}\nGPT连续帧理解：{json.dumps(gpt, ensure_ascii=False)}"}],
                "temperature": 0.1,
                "max_tokens": 2600,
            },
            timeout=(15, 300),
        )
        final = _json_object(_completion_text(response))
        return {field: final.get(field, "") for field in FACT_FIELDS}

    def submit_vision(self, source_path: str) -> str:
        source = Path(source_path)
        if self._job_id is None:
            raise RuntimeError("镜头理解任务缺少任务上下文")
        work_dir = source.parent.parent / "dual-shot-vision" / str(self._job_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        doubao_path, gpt_path = work_dir / "doubao.json", work_dir / "gpt.json"
        if doubao_path.exists():
            doubao = json.loads(doubao_path.read_text(encoding="utf-8"))
        else:
            doubao = self._doubao_understand(source)
            doubao_path.write_text(json.dumps(doubao, ensure_ascii=False), encoding="utf-8")
        if gpt_path.exists():
            gpt = json.loads(gpt_path.read_text(encoding="utf-8"))
        else:
            gpt = self._gpt_understand(source, work_dir)
            gpt_path.write_text(json.dumps(gpt, ensure_ascii=False), encoding="utf-8")
        final_path = work_dir / "final.json"
        if final_path.exists():
            final = json.loads(final_path.read_text(encoding="utf-8"))
        else:
            final = self._synthesize(doubao, gpt)
            final_path.write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")
        result = work_dir / "result.json"
        # 中间结果用于重试和故障追踪；execute_vision_job只把final写入用户事实字段。
        result.write_text(json.dumps({"doubao": doubao, "gpt": gpt, "final": final}, ensure_ascii=False), encoding="utf-8")
        return str(result)

    def get_result(self, task_id: str) -> VisionTaskResult:
        path = Path(task_id)
        if not path.is_file():
            return VisionTaskResult(task_id, "Failed", error_message="逐镜双模型理解结果不存在")
        return VisionTaskResult(task_id, "Completed", content=path.read_text(encoding="utf-8"))
