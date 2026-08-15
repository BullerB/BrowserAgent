"""Runtime configuration, loaded from environment / .env.

Nested settings use a double underscore, e.g. ``WEBFLOW_LLM__MODEL``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["openai", "anthropic", "null"]


class LLMSettings(BaseModel):
    provider: LLMProvider = "null"
    model: str = "gpt-4o-mini"
    api_key: SecretStr | None = None
    base_url: str | None = None
    temperature: float = 0.0
    request_timeout_s: float = 120.0
    max_output_tokens: int = 4096


class BrowserSettings(BaseModel):
    headless: bool = True
    slow_mo_ms: int = 0
    locale: str = "da-DK"
    timezone: str = "Europe/Copenhagen"
    viewport_width: int = 1440
    viewport_height: int = 900
    user_agent: str | None = None
    default_timeout_ms: int = 15_000
    navigation_timeout_ms: int = 30_000
    record_trace: bool = False


class AgentSettings(BaseModel):
    max_steps: int = 60
    max_llm_calls: int = 80
    max_consecutive_failures: int = 3
    """How many identical (url, action) repeats count as a stuck loop."""
    loop_window: int = 4
    observation_element_limit: int = 120
    settle_ms: int = 700
    """Domains the agent may navigate to. Empty means "only the provider's own domain"."""
    extra_allowed_domains: tuple[str, ...] = ()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WEBFLOW_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)

    project_root: Path = Path(__file__).resolve().parents[2]
    data_dir: Path = Path("data")
    profile_path: Path = Path("profiles/profile.json")

    log_level: str = "INFO"
    log_json: bool = False

    max_concurrent_providers: int = 3

    def resolve_path(self, p: Path) -> Path:
        """Interpret a configured path as relative to the project root."""
        return p if p.is_absolute() else (self.project_root / p)

    @property
    def data_path(self) -> Path:
        return self.resolve_path(self.data_dir)

    @property
    def artifacts_path(self) -> Path:
        return self.data_path / "artifacts"

    @property
    def db_path(self) -> Path:
        return self.data_path / "runs.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    def ensure_dirs(self) -> None:
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.artifacts_path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
