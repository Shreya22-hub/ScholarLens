"""Configuration management for ScholarLens backend."""

import json
from pathlib import Path
from typing import Any, Dict, List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """ScholarLens application settings loaded from environment or defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_name: str = "ScholarLens"
    environment: str = "development"
    log_level: str = "INFO"

    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    backend_cors_origins: List[str] = [
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ]

    sources_config_path: str = "config/sources.json"
    database_path: str = "data/scholarlens.db"

    # Ingestion & Fetcher Settings
    user_agent: str = "ScholarLens/1.0 (+https://scholar-lens.gov.in; Academic & Verification Research)"
    fetch_timeout_seconds: float = 15.0
    max_fetch_retries: int = 2
    respect_robots_txt: bool = True


settings = Settings()


def load_configured_sources(config_path: str = None) -> List[Dict[str, Any]]:
    """Load official scholarship portal sources from JSON configuration file."""
    path = Path(config_path or settings.sources_config_path)
    if not path.is_file():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("sources", [])
