"""dbt metadata loading and compact context construction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DbtMetadata:
    sources: dict[str, Any]
    models: dict[str, Any]


def case_root_from_data_dir(data_dir: Path) -> Path:
    return data_dir.expanduser().resolve().parent


def load_dbt_metadata(case_root: Path) -> DbtMetadata:
    sources_path = case_root / "dbt" / "models" / "staging" / "_sources.yml"
    models_path = case_root / "dbt" / "models" / "marts" / "_models.yml"
    sources = _read_yaml(sources_path)
    models = _read_yaml(models_path)
    return DbtMetadata(sources=sources, models=models)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def compact_metadata_context(metadata: DbtMetadata) -> str:
    """Return concise context for prompts and README/debug output."""
    lines: list[str] = []
    for source in metadata.sources.get("sources", []):
        lines.append(
            f"Source group: {source.get('name')} - {source.get('description', '').strip()}"
        )
        for table in source.get("tables", []):
            cols = ", ".join(col["name"] for col in table.get("columns", []))
            lines.append(
                f"- {table.get('name')}: {table.get('description', '').strip()} Columns: {cols}"
            )

    for model in metadata.models.get("models", []):
        cols = ", ".join(col["name"] for col in model.get("columns", []))
        source_tables = ", ".join(model.get("meta", {}).get("source_tables", []))
        lines.append(
            f"Model: {model.get('name')} owner={model.get('meta', {}).get('owner')} "
            f"sources=[{source_tables}] Columns: {cols}. {model.get('description', '').strip()}"
        )
    return "\n".join(lines)


def metric_hints() -> str:
    return "\n".join(
        [
            "GTV IDR/USD should use completed transactions unless the user explicitly asks for Ops reporting.",
            "amount_usd is recorded directly at transaction time; do not convert IDR using implied_fx_rate.",
            "agg_monthly_biz_summary contains Total rows; MTU is populated only on Total rows.",
            "mart_ops_dashboard includes non-failed transactions and Mixpanel MTU, so it can disagree.",
            "Mixpanel events are client-side and should not be sole source of truth for transaction volume.",
        ]
    )
