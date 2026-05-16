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

import os
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
# WrenAI-style schema context (primary context builder)
# ---------------------------------------------------------------------------

_DEFAULT_INSTRUCTIONS_PATH = Path(__file__).parents[2] / "instructions.yml"


def load_instructions(path: Path | None = None) -> dict[str, list[str]]:
    """Load per-table warning blocks from instructions.yml.

    Returns dict[table_name, list[warning_str]]. Empty dict if file missing.
    Path defaults to project_root/instructions.yml; override via
    TRUST_ANALYTICS_INSTRUCTIONS_PATH env var or explicit argument.
    """
    if path is None:
        env_path = os.getenv("TRUST_ANALYTICS_INSTRUCTIONS_PATH")
        path = Path(env_path) if env_path else _DEFAULT_INSTRUCTIONS_PATH
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, list[str]] = {}
    for table_name, table_block in (raw.get("tables") or {}).items():
        warnings = table_block.get("warnings") or []
        if isinstance(warnings, list):
            out[table_name] = [str(w) for w in warnings]
    return out


def describe_schema_context(
    metadata: DbtMetadata,
    instructions: dict[str, list[str]] | None = None,
) -> str:
    """Return a WrenAI-style full-text schema description for the LLM prompt.

    Format is modelled on WrenAI's describe_schema() from schema_indexer.py:
    each source table and mart model is rendered as a `### Model:` block with
    description, grain, warnings (from instructions.yml), and full column
    descriptions inlined.

    All tables are always included — no retrieval, no truncation.
    """
    if instructions is None:
        instructions = load_instructions()
    lines: list[str] = []

    # Raw source tables from _sources.yml
    for source_group in metadata.sources.get("sources", []):
        group_name = source_group.get("name", "?")
        group_desc = _clean(source_group.get("description", ""))
        lines.append(f"### Source group: {group_name} — {group_desc}")
        lines.append("")

        for table in source_group.get("tables", []):
            _render_table(table, lines, instructions)

    # Mart / reporting models from _models.yml
    if metadata.models:
        lines.append("### dbt mart models (built from source tables above)")
        lines.append("")
        for model in metadata.models.get("models", []):
            _render_model(model, lines, instructions)

    return "\n".join(lines)


def _render_table(
    table: dict[str, Any],
    lines: list[str],
    instructions: dict[str, list[str]],
) -> None:
    name = table.get("name", "?")
    desc = _clean(table.get("description", ""))
    lines.append(f"#### Table: {name}")
    if desc:
        lines.append(f"  {desc}")

    for w in instructions.get(name, []):
        lines.append(f"  NOTE: {w}")

    cols = table.get("columns", [])
    if cols:
        lines.append("  Columns:")
        for col in cols:
            _render_column(col, lines)
    lines.append("")


def _render_model(
    model: dict[str, Any],
    lines: list[str],
    instructions: dict[str, list[str]],
) -> None:
    name = model.get("name", "?")
    desc = _clean(model.get("description", ""))
    meta = model.get("meta", {})
    owner = meta.get("owner", "")
    managed_by = meta.get("managed_by", "")
    source_tables = meta.get("source_tables", [])

    header = f"#### Model: {name}"
    if desc:
        first_sentence = desc.split(".")[0].strip()
        header += f" — {first_sentence}"
    lines.append(header)

    if owner or managed_by:
        lines.append(f"  Owner: {owner}  Managed by: {managed_by}")
    if source_tables:
        lines.append(f"  Source tables: {', '.join(source_tables)}")
    if desc:
        lines.append(f"  Description: {desc}")

    for w in instructions.get(name, []):
        lines.append(f"  NOTE: {w}")

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
