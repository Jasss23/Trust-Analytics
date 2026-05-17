"""Runtime configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_DB_PATH = Path("var/trust_analytics.sqlite")
DEFAULT_DATA_DIR = Path("demo_data/fintech_analytics/data")
NATIVE_OPENAI_BASE_URL = "https://api.openai.com/v1"


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    data_dir: Path
    db_path: Path
    legacy_openrouter_configured: bool = False
    ignored_openai_base_url: str | None = None


def load_settings() -> Settings:
    """Load project settings from `.env` and environment variables."""
    load_dotenv()
    raw_model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    openai_base_url = os.getenv("OPENAI_BASE_URL") or None
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=_native_openai_model(raw_model),
        data_dir=Path(
            os.getenv("TRUST_ANALYTICS_DATA_DIR")
            or str(DEFAULT_DATA_DIR)
        ),
        db_path=Path(
            os.getenv("TRUST_ANALYTICS_DB_PATH")
            or str(DEFAULT_DB_PATH)
        ),
        legacy_openrouter_configured=bool(
            os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_BASE_URL")
        ),
        ignored_openai_base_url=(
            openai_base_url
            if openai_base_url and openai_base_url.rstrip("/") != NATIVE_OPENAI_BASE_URL
            else None
        ),
    )


def _native_openai_model(value: str) -> str:
    """Accept old OpenRouter-style OpenAI model slugs while using native OpenAI."""
    model = value.strip() or DEFAULT_MODEL
    if model.startswith("openai/"):
        return model.removeprefix("openai/")
    return model
