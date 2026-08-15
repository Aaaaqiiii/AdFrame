from __future__ import annotations

from dataclasses import asdict, dataclass

import requests

from app.core.config import Settings


@dataclass(frozen=True)
class ConnectionCheck:
    connected: bool
    message: str

    def as_dict(self) -> dict[str, bool | str]:
        return asdict(self)


def _test_generation(base_url: str, task_path: str, api_key: str) -> ConnectionCheck:
    if not api_key:
        return ConnectionCheck(False, "尚未填写 API Key")
    try:
        # 查询一个不存在的任务不会产生生成费用；401/403 才表示鉴权失败。
        response = requests.get(
            f"{base_url.rstrip('/')}{task_path.rstrip('/')}/adflow-connectivity-check",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=(8, 15),
        )
        if response.status_code in {401, 403}:
            return ConnectionCheck(False, "API Key 无效或没有访问权限")
        if response.status_code >= 500:
            return ConnectionCheck(False, f"服务暂时不可用（HTTP {response.status_code}）")
        return ConnectionCheck(True, f"连接成功，鉴权已通过（HTTP {response.status_code}）")
    except requests.RequestException as exc:
        return ConnectionCheck(False, f"无法连接服务：{exc}")


def test_connection(service: str, settings: Settings | None = None) -> ConnectionCheck:
    settings = settings or Settings()
    if service == "volcengine_generation":
        return _test_generation(settings.volcengine_seedance_base_url, settings.volcengine_seedance_task_path, settings.volcengine_api_key)
    if service == "comfly_generation":
        return _test_generation(settings.comfly_base_url, settings.comfly_seedance_task_path, settings.comfly_api_key)
    if service == "volcengine_vision":
        if not (settings.volcengine_access_key and settings.volcengine_secret_key and settings.volcengine_vod_space):
            return ConnectionCheck(False, "请完整填写 Access Key、Secret Key 和 VOD Space")
        try:
            from volcengine.vod.VodService import VodService
            from volcengine.vod.models.request.request_vod_pb2 import VodGetSpaceDetailRequest

            client = VodService()
            client.set_ak(settings.volcengine_access_key)
            client.set_sk(settings.volcengine_secret_key)
            request = VodGetSpaceDetailRequest()
            request.SpaceName = settings.volcengine_vod_space
            client.get_space_detail(request)
            return ConnectionCheck(True, "连接成功，VOD 空间可以访问")
        except Exception as exc:
            return ConnectionCheck(False, f"VOD 验证失败：{exc}")
    return ConnectionCheck(False, "未知服务")
