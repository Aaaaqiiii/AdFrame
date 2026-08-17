from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.projects import router as projects_router
from app.api.routes.timeline import router as timeline_router
from app.api.routes.analysis import router as analysis_router
from app.api.routes.generations import router as generations_router
from app.api.routes.generation_segments import router as generation_segments_router
from app.db.migrations import require_database_at_head
from app.core.config import Settings
from app.core.license import LicenseError, verify_license
from app.core.logging import configure_logging
from app.services.connectivity import test_connection
from pydantic import BaseModel, Field


class LocalSettingsUpdate(BaseModel):
    volcengine_api_key: str | None = Field(default=None, min_length=1, max_length=1000)
    comfly_api_key: str | None = Field(default=None, min_length=1, max_length=1000)
    volcengine_access_key: str | None = Field(default=None, min_length=1, max_length=1000)
    volcengine_secret_key: str | None = Field(default=None, min_length=1, max_length=1000)
    volcengine_vod_space: str | None = Field(default=None, min_length=1, max_length=500)


SETTING_GROUPS = {
    "volcengine_generation": {"VOLCENGINE_API_KEY"},
    # 保存 Comfly Key 时默认验证 GPT Chat Completions（提示词/关键帧理解实际端点）。
    "comfly_prompt": {"COMFLY_API_KEY"},
    "comfly_generation": set(),
    "volcengine_vision": {"VOLCENGINE_ACCESS_KEY", "VOLCENGINE_SECRET_KEY", "VOLCENGINE_VOD_SPACE"},
}


def create_app() -> FastAPI:
    settings = Settings()
    configure_logging()
    if settings.enforce_license:
        try:
            verify_license(settings.app_license_path, settings.app_license_public_key_path)
        except (OSError, LicenseError) as exc:
            raise RuntimeError("AdFlow licence verification failed") from exc
    require_database_at_head()
    app = FastAPI(title="AdFlow API")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=settings.cors_origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(projects_router)
    app.include_router(timeline_router)
    app.include_router(analysis_router)
    app.include_router(generations_router)
    app.include_router(generation_segments_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/preflight")
    def preflight() -> dict[str, dict[str, str | bool]]:
        current = Settings()
        return {
            "volcengine_generation": {
                "ready": bool(current.volcengine_api_key),
                "model": current.volcengine_seedance_model,
                "endpoint": current.volcengine_seedance_base_url + current.volcengine_seedance_task_path,
            },
            "comfly_generation": {
                "ready": bool(current.comfly_api_key),
                "model": current.comfly_seedance_model,
                "endpoint": current.comfly_base_url + current.comfly_seedance_task_path,
            },
            "comfly_prompt": {
                "ready": bool(current.comfly_api_key),
                "model": current.comfly_vision_model,
                "endpoint": current.comfly_vision_base_url + "/v1/chat/completions",
            },
            "storyboard_vision": {
                # 逐镜链路必须同时具备豆包完整片段理解和 GPT 关键帧理解。
                "ready": bool(current.volcengine_api_key and current.comfly_api_key),
                "model": f"{current.volcengine_vision_model} + {current.comfly_vision_model}",
                "endpoint": "火山方舟 + Comfly GPT",
            },
            "temporary_publisher": {"ready": True, "model": "tempfile.org 24h", "endpoint": "https://tempfile.org/api/upload/local"},
        }

    @app.put("/api/local-settings")
    def update_local_settings(payload: LocalSettingsUpdate, request: Request) -> dict[str, object]:
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(status_code=403, detail="Local settings can only be changed from this computer")
        env_path = Path(Settings.model_config["env_file"])
        lines = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.exists() else []
        updates = {
            key: value.strip() for key, value in {
                "VOLCENGINE_API_KEY": payload.volcengine_api_key,
                "COMFLY_API_KEY": payload.comfly_api_key,
                "VOLCENGINE_ACCESS_KEY": payload.volcengine_access_key,
                "VOLCENGINE_SECRET_KEY": payload.volcengine_secret_key,
                "VOLCENGINE_VOD_SPACE": payload.volcengine_vod_space,
            }.items() if value
        }
        if not updates:
            raise HTTPException(status_code=422, detail="At least one local setting is required")
        output = []
        remaining = dict(updates)
        for line in lines:
            name = line.split("=", 1)[0]
            output.append(f"{name}={remaining.pop(name)}" if name in remaining else line)
        output.extend(f"{name}={value}" for name, value in remaining.items())
        try:
            env_path.write_text("\n".join(output) + "\n", encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"配置文件保存失败：{exc}") from exc
        service = next(name for name, keys in SETTING_GROUPS.items() if keys & updates.keys())
        check = test_connection(service, Settings())
        return {"configured": True, "service": service, "connection": check.as_dict()}

    @app.post("/api/local-settings/test/{service}")
    def check_local_setting(service: str, request: Request) -> dict[str, bool | str]:
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(status_code=403, detail="Local settings can only be tested from this computer")
        if service not in SETTING_GROUPS:
            raise HTTPException(status_code=404, detail="Unknown service")
        return test_connection(service).as_dict()

    return app


app = create_app()
