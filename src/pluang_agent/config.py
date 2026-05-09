"""Runtime configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-4.1-mini"
DEFAULT_DB_PATH = Path("var/pluang.sqlite")
DEFAULT_DATA_DIR = Path("/Users/jiashunpang/Downloads/pluang_analytics_agent/data")


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str | None
    openrouter_model: str
    openrouter_base_url: str
    data_dir: Path
    db_path: Path


def load_settings() -> Settings:
    """Load project settings from `.env` and environment variables."""
    load_dotenv()
    return Settings(
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        openrouter_model=os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
        data_dir=Path(os.getenv("PLUANG_DATA_DIR", str(DEFAULT_DATA_DIR))),
        db_path=Path(os.getenv("PLUANG_DB_PATH", str(DEFAULT_DB_PATH))),
    )
