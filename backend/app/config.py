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

    # --- MQTT publishing (spec §27; backlog "fully use mqtt") ---
    # Only used when mqtt_enabled. Defaults target Home Assistant's Mosquitto
    # add-on (host ``core-mosquitto``). The discovery prefix and base topic
    # follow HA's MQTT discovery convention (spec §27.2-27.3).
    mqtt_host: str = "core-mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_discovery_prefix: str = "homeassistant"
    mqtt_base_topic: str = "homeassistant/finance"

    # --- AI gateway (spec §22). Off by default; opt-in via privacy_mode. ---
    # The API key for a cloud (or auth'd local) LLM is a secret, so it comes from
    # the environment (HAFI_AI_API_KEY), never stored in the DB. The endpoint and
    # model are non-secret and live in DB settings (settings_service).
    ai_api_key: str | None = None
    ai_timeout_seconds: float = 30.0

    # --- Investment price feed (spec §27). Off by default (manual). A keyed
    # provider (e.g. Alpha Vantage) reads its API key here; keyless sources
    # (Stooq) and manual mode need nothing. Only ticker symbols are ever sent —
    # never balances or holdings sizes — so a price fetch is privacy-safe.
    investment_api_key: str | None = None

    # --- At-rest DB encryption (backlog #15b) ---
    # Optional. When the DB is encrypted in "stored" unlock mode, the passphrase
    # is supplied here (env HAFI_DB_KEY) so the add-on can start unattended. In
    # "prompt" mode this stays empty and the user unlocks via the UI each start.
    db_key: str | None = None

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
