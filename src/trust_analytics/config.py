"""Runtime configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_DB_PATH = Path("var/trust_analytics.sqlite")
DEFAULT_DATA_DIR = Path("demo_data/fintech_analytics/data")


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    data_dir: Path
    db_path: Path


def load_settings() -> Settings:
    """Load project settings from `.env` and environment variables."""
    load_dotenv()
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        data_dir=Path(
            os.getenv("TRUST_ANALYTICS_DATA_DIR")
            or str(DEFAULT_DATA_DIR)
        ),
        db_path=Path(
            os.getenv("TRUST_ANALYTICS_DB_PATH")
            or str(DEFAULT_DB_PATH)
        ),
    )
