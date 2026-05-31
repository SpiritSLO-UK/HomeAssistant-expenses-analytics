"""Application configuration.

Settings are loaded from environment variables (prefix ``HAFI_``). Inside the
Home Assistant add-on, ``run.sh`` translates the add-on options into these
environment variables. Defaults are chosen so the app runs privately and
locally with no external calls (spec §7.1, §28.2).
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PrivacyMode(str, Enum):
    """Privacy modes (spec §7). Strict local is the default."""

    strict_local = "strict_local"
    local_llm = "local_llm"
    cloud_manual = "cloud_manual"
    cloud_auto = "cloud_auto"
    no_ai = "no_ai"


class SetupMode(str, Enum):
    """Household setup modes (spec §6, §12.3)."""

    personal = "personal"
    household = "household"
    shared_private = "shared_private"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HAFI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core ---
    app_name: str = "HA Finance Intelligence"
    database_path: str = "./data/finance.db"
    currency: str = "GBP"
    privacy_mode: PrivacyMode = PrivacyMode.strict_local
    setup_mode: SetupMode = SetupMode.household

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8099
    log_level: str = "INFO"

    # --- Home Assistant ingress ---
    # When served behind HA ingress the app receives an ``X-Ingress-Path``
    # header; FastAPI's root_path handling + forwarded headers cover this.
    behind_ingress: bool = True

    # --- CORS (local frontend dev only; empty in production) ---
    cors_origins: list[str] = []

    # --- Integrations (all OFF by default — strict local) ---
    mqtt_enabled: bool = False
    ai_enabled: bool = False
    ocr_enabled: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    @property
    def database_file(self) -> Path:
        return Path(self.database_path)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
