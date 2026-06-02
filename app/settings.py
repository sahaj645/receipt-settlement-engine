"""Application settings loaded from environment variables.

We use pydantic-settings so the same code path works for local `.env`
development and Render's injected env vars in production.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. All values can be overridden via env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Gemini ---------------------------------------------------------
    gemini_api_key: str = Field(default="", description="Google AI Studio API key.")
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model id. Current free-tier multimodal model (Nov 2025+).",
    )
    gemini_timeout_s: int = Field(default=45, description="Per-request timeout.")

    # --- App ------------------------------------------------------------
    app_name: str = "Fair Split"
    app_version: str = "0.1.0"
    cors_allow_origins: str = Field(
        default="*",
        description="Comma-separated origin list. '*' for public demo.",
    )

    @property
    def cors_origins(self) -> list[str]:
        if self.cors_allow_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — avoids re-reading env on every request."""
    return Settings()
