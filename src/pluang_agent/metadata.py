"""dbt metadata loading and WrenAI-inspired schema context construction.

WrenAI's `describe_schema()` (schema_indexer.py) is the reference pattern:
format each model as `### Model: name — description` with full column
descriptions inlined. This surfaces business meaning that raw column names
cannot convey — exactly the disambiguation the LLM needs to pick the right
source table and construct correct date filters.

No embedding, no retrieval — all 7 tables fit comfortably in one prompt.
Retrieval is deferred to a future scaling step (see README Scaling section).
"""

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


# ---------------------------------------------------------------------------
# WrenAI-style schema context (primary context builder — R2+)
# ---------------------------------------------------------------------------

# Per-table/model warning annotations that the dbt YAML cannot express.
# These map directly to the disambiguation rules in prompts/sql_agent_system.md.
_TABLE_WARNINGS: dict[str, list[str]] = {
    "agg_monthly_biz_summary": [
        "month column stores YYYY-MM-01 (first day of month). "
        "Always use: month >= 'YYYY-MM-01' AND month < 'YYYY-MM+1-01'. Never 'YYYY-MM'.",
        "Contains a Total row (asset_class = 'Total') that aggregates all asset classes. "
        "When grouping by asset_class, add WHERE asset_class != 'Total'.",
        "mtu is populated ONLY on the Total row. NULL for individual asset_class rows.",
    ],
    "mart_ops_dashboard": [
        "Managed by the Ops team — NOT a dbt model.",
        "Includes status != 'failed' (completed + pending). "
        "Produces HIGHER GTV and transaction counts than fct_trading_daily.",
        "gtv_usd_reported uses a fixed 15,000 IDR/USD exchange rate. "
        "Do NOT use for USD GTV — use fct_trading_daily.gtv_usd instead.",
        "month column stores YYYY-MM-01. "
        "Always use: month >= 'YYYY-MM-01' AND month < 'YYYY-MM+1-01'.",
    ],
    "stg_mixpanel_events": [
        "Client-side events — delivery is NOT guaranteed. "
        "Do not use as sole source of truth for transaction volumes or user counts.",
    ],
    "fct_trading_daily": [
        "Canonical source for completed-transaction GTV and counts. "
        "status = completed is already applied — do NOT add a status filter.",
    ],
}


def describe_schema_context(metadata: DbtMetadata) -> str:
    """Return a WrenAI-style full-text schema description for the LLM prompt.

    Format is modelled on WrenAI's describe_schema() from schema_indexer.py:
    each source table and mart model is rendered as a `### Model:` block with
    description, grain, warnings, and full column descriptions inlined.

    All 7 tables are always included — no retrieval, no truncation.
    """
    lines: list[str] = []

    # Raw source tables from _sources.yml
    for source_group in metadata.sources.get("sources", []):
        group_name = source_group.get("name", "?")
        group_desc = _clean(source_group.get("description", ""))
        lines.append(f"### Source group: {group_name} — {group_desc}")
        lines.append("")

        for table in source_group.get("tables", []):
            _render_table(table, lines)

    # Mart / reporting models from _models.yml
    if metadata.models:
        lines.append("### dbt mart models (built from source tables above)")
        lines.append("")
        for model in metadata.models.get("models", []):
            _render_model(model, lines)

    return "\n".join(lines)


def _render_table(table: dict[str, Any], lines: list[str]) -> None:
    name = table.get("name", "?")
    desc = _clean(table.get("description", ""))
    lines.append(f"#### Table: {name}")
    if desc:
        lines.append(f"  {desc}")

    warnings = _TABLE_WARNINGS.get(name, [])
    for w in warnings:
        lines.append(f"  ⚠️  {w}")

    cols = table.get("columns", [])
    if cols:
        lines.append("  Columns:")
        for col in cols:
            _render_column(col, lines)
    lines.append("")


def _render_model(model: dict[str, Any], lines: list[str]) -> None:
    name = model.get("name", "?")
    desc = _clean(model.get("description", ""))
    meta = model.get("meta", {})
    owner = meta.get("owner", "")
    managed_by = meta.get("managed_by", "")
    source_tables = meta.get("source_tables", [])

    header = f"#### Model: {name}"
    if desc:
        # Take first sentence of description for the header line
        first_sentence = desc.split(".")[0].strip()
        header += f" — {first_sentence}"
    lines.append(header)

    if owner or managed_by:
        lines.append(f"  Owner: {owner}  Managed by: {managed_by}")
    if source_tables:
        lines.append(f"  Source tables: {', '.join(source_tables)}")

    # Full description on its own line if it differs from the header fragment
    if desc:
        lines.append(f"  Description: {desc}")

    warnings = _TABLE_WARNINGS.get(name, [])
    for w in warnings:
        lines.append(f"  ⚠️  {w}")

    cols = model.get("columns", [])
    if cols:
        lines.append("  Columns:")
        for col in cols:
            _render_column(col, lines)
    lines.append("")


def _render_column(col: dict[str, Any], lines: list[str]) -> None:
    name = col.get("name", "?")
    desc = _clean(col.get("description", ""))
    if desc:
        lines.append(f"    - {name}: {desc}")
    else:
        lines.append(f"    - {name}")


def _clean(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Legacy compact context (kept for backward compat; deprecated in R2)
# ---------------------------------------------------------------------------

def compact_metadata_context(metadata: DbtMetadata) -> str:
    """Minimal column-name-only context. Superseded by describe_schema_context().

    Kept so any code that hasn't been updated yet still compiles. New code
    should call describe_schema_context() instead.
    """
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
    """Legacy metric hints. Content now lives in prompts/sql_agent_system.md."""
    return "\n".join(
        [
            "GTV IDR/USD should use completed transactions unless the user explicitly asks for Ops reporting.",
            "amount_usd is recorded directly at transaction time; do not convert IDR using implied_fx_rate.",
            "agg_monthly_biz_summary contains Total rows; MTU is populated only on Total rows.",
            "mart_ops_dashboard includes non-failed transactions and Mixpanel MTU, so it can disagree.",
            "Mixpanel events are client-side and should not be sole source of truth for transaction volume.",
        ]
    )
