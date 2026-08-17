from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://adflow:change-me@localhost:5432/adflow"
    media_root: Path = Path(r"E:\工具-商用\data\media")
    app_license_path: Path = Path(r"D:\AdFlow\license.json")
    app_license_public_key_path: Path = Path(r"D:\AdFlow\license-public.pem")
    max_reference_video_bytes: int = 500 * 1024 * 1024
    volcengine_api_key: str = ""
    volcengine_seedance_base_url: str = "https://ark.cn-beijing.volces.com"
    volcengine_seedance_task_path: str = "/api/v3/contents/generations/tasks"
    volcengine_seedance_model: str = "doubao-seedance-2-5-260628"
    # 豆包负责读取人工切出的完整镜头；Seedance 只负责后续视频生成。
    volcengine_vision_model: str = "doubao-seed-2-0-lite-260215"
    volcengine_access_key: str = ""
    volcengine_secret_key: str = ""
    volcengine_vod_space: str = ""
    comfly_api_key: str = ""
    comfly_base_url: str = "https://ai.comfly.chat"
    comfly_seedance_task_path: str = "/seedance/v3/contents/generations/tasks"
    comfly_seedance_model: str = "doubao-seedance-2.5"
    comfly_vision_base_url: str = "https://ai.comfly.org"
    comfly_vision_model: str = "gpt-5.6-terra"
    openai_api_key: str = ""
    provider_max_reference_seconds: float = 30.0
    segment_safety_margin_seconds: float = 1.0
    recommended_min_segment_seconds: float = 8.0
    worker_poll_seconds: int = 5
    enforce_license: bool = False
    cors_origin_regex: str = r"http://(localhost|127\.0\.0\.1):\d+"

    @property
    def effective_segment_limit_seconds(self) -> float:
        return self.provider_max_reference_seconds - self.segment_safety_margin_seconds
