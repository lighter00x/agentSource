"""进程级配置：从环境变量和 .env 读取。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./data/drugpaper.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    env: str = Field(default="dev", validation_alias=AliasChoices("DRUGPAPER_ENV", "env"))
    # 生产：postgresql+asyncpg://user:pass@host:5432/db；本地无数据库时默认用 SQLite
    database_url: str = DEFAULT_DATABASE_URL
    redis_url: str = ""
    runs_dir: Path = Path("./runs")
    models_config_path: Path = Path("config/models.yaml")
    skills_root: Path = Path("./skills")
    skills_watch_enabled: bool = True
    skills_admin_token: str = ""
    # P2：存储、解析服务、渲染
    blob_root: Path = Path("./data/blobs")
    mineru_url: str = ""
    mineru_max_inflight: int = Field(default=4, ge=1)
    mineru_timeout_s: float = Field(default=1800, gt=0)
    mineru_poll_interval_s: float = Field(default=5, gt=0)
    mineru_submit_attempts: int = Field(default=6, ge=1)
    mineru_backoff_s: float = Field(default=2, ge=0)
    mineru_submit_timeout_s: float = Field(default=60, gt=0)
    mineru_status_timeout_s: float = Field(default=30, gt=0)
    mineru_result_timeout_s: float = Field(default=120, gt=0)
    # MolParser：附件仅提供默认示例地址，实际地址必须显式配置。
    molparser_base_url: str = ""
    molparser_timeout_s: float = Field(default=60, gt=0)
    molparser_rec_only: bool = True
    molparser_model_version: str = "default"  # 更新外部权重时同步更改，识别缓存纳入该版本。
    # Optional platform-provided OPSIN service, including its /ws or /opsin path.
    opsin_base_url: str = ""
    opsin_timeout_s: float = Field(default=20, gt=0)
    opsin_model_version: str = "default"
    # 多 API Worker 推荐 remote，共享一个有界检测 Worker。
    yolo_backend: Literal["local", "remote"] = "local"
    yolo_model_path: Path | None = None
    yolo_device: str = "cpu"
    yolo_base_url: str = ""
    yolo_service_token: str = ""
    yolo_conf: float = Field(default=0.15, ge=0, le=1)
    yolo_high_recall_conf: float = Field(default=0.08, ge=0, le=1)
    yolo_iou: float = Field(default=0.15, gt=0, le=1)
    yolo_imgsz: int = Field(default=640, ge=64, le=2048)
    yolo_padding_ratio: float = Field(default=0.05, ge=0, le=0.5)
    yolo_max_detections: int = Field(default=256, ge=1, le=1024)
    yolo_queue_size: int = Field(default=16, ge=1, le=256)
    yolo_max_jobs_per_user: int = Field(default=2, ge=1, le=16)
    yolo_queue_timeout_s: float = Field(default=30, gt=0)
    yolo_timeout_s: float = Field(default=120, gt=0)
    yolo_cache_size: int = Field(default=64, ge=0, le=1024)
    yolo_max_pixels: int = Field(default=16_000_000, ge=1)
    yolo_max_image_bytes: int = Field(default=12_000_000, ge=1, le=24_000_000)
    routing_config_path: Path = Path("config/routing.yaml")
    figure_exploration_enabled: bool = True
    figure_exploration_max_turns: int = Field(default=64, ge=1, le=128)
    figure_exploration_max_tool_calls: int = Field(default=80, ge=1, le=160)
    figure_exploration_timeout_s: float = Field(default=1800, ge=10, le=3600)
    figure_exploration_max_visual_reads: int = Field(default=8, ge=1, le=24)
    figure_exploration_preview_batch_size: int = Field(default=4, ge=1, le=6)
    figure_exploration_max_preview_batches: int = Field(default=2, ge=0, le=8)
    figure_exploration_max_context_images: int = Field(default=12, ge=2, le=48)
    figure_exploration_max_request_bytes: int = Field(default=24_000_000, ge=100_000, le=96_000_000)
    figure_exploration_max_extractions: int = Field(default=3, ge=1, le=6)
    figure_exploration_max_regenerations: int = Field(default=1, ge=0, le=2)
    figure_exploration_max_stagnant_calls: int = Field(default=6, ge=3, le=20)
    figure_exploration_closing_turns: int = Field(default=3, ge=1, le=6)
    figure_concurrency: int = Field(default=6, ge=1, le=32)
    model_endpoint_concurrency: int = Field(default=8, ge=1, le=128)
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    render_dpi: int = 144
    crop_dpi: int = 300
    trace_jsonl: bool = True  # DbTracer 之外是否同时写 runs/<run_id>.jsonl

    @field_validator("database_url", mode="before")
    @classmethod
    def _db_default(cls, v: object) -> object:
        # `.env` 里留空的 `DATABASE_URL=` 视为未配置
        return DEFAULT_DATABASE_URL if v is None or (isinstance(v, str) and not v.strip()) else v

    @field_validator("blob_root", "runs_dir", mode="before")
    @classmethod
    def _path_default(cls, v: object, info: ValidationInfo) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return {"blob_root": Path("./data/blobs"), "runs_dir": Path("./runs")}[
                info.field_name or "blob_root"
            ]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
