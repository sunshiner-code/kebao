from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


def _json_env(name: str) -> dict:
    raw = os.environ.get(name)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"环境变量 {name} 不是合法的 JSON：{exc}") from exc
    return value if isinstance(value, dict) else {}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    root_dir: Path = field(default_factory=lambda: Path(_env("TECHINS_ROOT", str(Path(__file__).resolve().parents[2]))))
    data_dir: Path = field(default_factory=lambda: Path(_env("TECHINS_DATA_DIR", str(Path(__file__).resolve().parents[2] / "data"))))
    database_url: str | None = _env("TECHINS_DATABASE_URL")
    jwt_secret: str = _env("TECHINS_JWT_SECRET", "techins-dev-secret-change-me") or ""
    connector_key: str = _env("TECHINS_CONNECTOR_KEY", "") or ""
    jwt_ttl_hours: int = int(_env("TECHINS_JWT_TTL_HOURS", "12") or 12)

    cloud_llm_base_url: str | None = _env("CLOUD_LLM_BASE_URL")
    cloud_llm_api_key: str | None = _env("CLOUD_LLM_API_KEY")
    cloud_llm_model: str | None = _env("CLOUD_LLM_MODEL")
    cloud_llm_display_model: str | None = _env("CLOUD_LLM_DISPLAY_MODEL")
    private_llm_base_url: str | None = _env("PRIVATE_LLM_BASE_URL")
    private_llm_api_key: str | None = _env("PRIVATE_LLM_API_KEY", "local")
    private_llm_model: str | None = _env("PRIVATE_LLM_MODEL")
    private_llm_display_model: str | None = _env("PRIVATE_LLM_DISPLAY_MODEL")
    cloud_llm_extra: dict = field(default_factory=lambda: _json_env("CLOUD_LLM_EXTRA"))
    private_llm_extra: dict = field(default_factory=lambda: _json_env("PRIVATE_LLM_EXTRA"))
    planner_params: dict = field(default_factory=lambda: _json_env("PLANNER_LLM_EXTRA") or {"reasoning_effort": "low"})
    llm_timeout_s: float = float(_env("LLM_TIMEOUT_SECONDS", "600") or 600)
    planner_timeout_s: float = float(_env("PLANNER_TIMEOUT_SECONDS", "90") or 90)
    llm_disabled: bool = (_env("TECHINS_DISABLE_LLM", "0") or "0") == "1"
    # 风险预警单独开关：与 TECHINS_DISABLE_LLM 同时为 0 才调用大模型，任一为 1 则改走内置推理引擎
    rw_llm_disabled: bool = (_env("TECHINS_DISABLE_RW_LLM", "0") or "0") == "1"

    executor_workers: int = int(_env("TECHINS_EXECUTOR_WORKERS", "4") or 4)
    monitor_tick_seconds: float = float(_env("TECHINS_MONITOR_TICK_SECONDS", "60") or 60)
    monitor_enabled: bool = (_env("TECHINS_MONITOR_ENABLED", "1") or "1") == "1"
    # 登录页是否列出演示账号。默认关闭，只保留账号密码登录
    show_demo_accounts: bool = (_env("TECHINS_SHOW_DEMO_ACCOUNTS", "0") or "0") == "1"
    simulated_minutes_per_tick: int = int(_env("TECHINS_SIM_MINUTES_PER_TICK", "60") or 60)

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        # 显式把 TECHINS_DATABASE_URL 设成空字符串时，仍使用 SQLite 文件。测试和冒烟脚本用这个开关避开 .env 里的 MySQL。
        if "TECHINS_DATABASE_URL" in os.environ:
            return f"sqlite:///{self.data_dir / 'techins.db'}"
        password = _env("MYSQL_PASSWORD")
        if password:
            user = quote_plus(_env("MYSQL_USER", "techins") or "techins")
            host = _env("MYSQL_HOST", "127.0.0.1")
            port = _env("MYSQL_PORT", "3306")
            name = _env("MYSQL_DATABASE", "techins")
            return f"mysql+pymysql://{user}:{quote_plus(password)}@{host}:{port}/{name}?charset=utf8mb4"
        return f"sqlite:///{self.data_dir / 'techins.db'}"

    @property
    def materials_dir(self) -> Path:
        return self.data_dir / "materials"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def datasets_dir(self) -> Path:
        return self.data_dir / "datasets"

    @property
    def skills_dir(self) -> Path:
        return Path(__file__).resolve().parent / "skills"


settings = Settings()


def ensure_dirs() -> None:
    for path in (settings.data_dir, settings.materials_dir, settings.workspaces_dir, settings.datasets_dir):
        path.mkdir(parents=True, exist_ok=True)
