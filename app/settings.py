from __future__ import annotations

import logging
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Astro API"
    debug: bool = False
    allowed_origins: List[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000", "*"]
    )

    # Optional: where your personal links (bookmarks to services) are sourced
    links_file: str = "app/sample_links.yaml"

    # Weather API settings
    weatherapi_key: str = ""
    weatherapi_url: str = "http://api.weatherapi.com/v1/"
    cache_ttl_seconds: int = 3600

    # Safety knobs
    max_abs_lat: float = 90.0
    max_abs_lon: float = 180.0


settings = Settings()
