from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./data/app.db"
    workspace_root: Path = PROJECT_ROOT / "workspaces"
    skill_root: Path = PROJECT_ROOT / "DAskill" / "data-analysis"
    frontend_origin: str = "http://localhost:3000"
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = Field(default=60, ge=1, le=300)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_tokens: int = Field(default=8192, ge=256, le=32768)
    llm_thinking_enabled: bool | None = None
    max_agent_steps: int = Field(default=30, ge=1, le=100)
    max_executions_per_run: int = Field(default=20, ge=1, le=50)
    max_code_retry: int = Field(default=3, ge=0, le=10)
    max_code_repair_stall: int = Field(default=2, ge=1, le=5)
    code_repair_oscillation_window: int = Field(default=4, ge=4, le=8)
    max_report_preparation_attempts: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Local Report Repair stall/oscillation limit, not a total round limit",
    )
    python_timeout_seconds: int = Field(default=120, ge=1, le=1800)
    python_memory_limit: str = "2g"
    python_cpu_limit: float = Field(default=2.0, gt=0, le=8)
    sandbox_image: str = "ai-analysis-sandbox:latest"
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)

    @field_validator("workspace_root", mode="before")
    @classmethod
    def normalize_workspace_root(cls, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()

    @field_validator("skill_root", mode="before")
    @classmethod
    def normalize_skill_root(cls, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()

    @property
    def frontend_origins(self) -> list[str]:
        origins = {
            item.strip().rstrip("/") for item in self.frontend_origin.split(",") if item.strip()
        }
        for origin in list(origins):
            if "://localhost" in origin:
                origins.add(origin.replace("://localhost", "://127.0.0.1", 1))
            elif "://127.0.0.1" in origin:
                origins.add(origin.replace("://127.0.0.1", "://localhost", 1))
        return sorted(origins)


@lru_cache
def get_settings() -> Settings:
    return Settings()
