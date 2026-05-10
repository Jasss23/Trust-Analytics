"""Generic Layer B — metrics.yml-driven cross-source reconciliation.

Per Decision 4: "rules collect evidence, LLM judges". Rules:
- Look up the metric in metrics.yml.
- Execute the primary source SQL and each alternative source SQL.
- Compare values per breakdown key (or scalar). Compute relative deltas.
- Emit `CrossSourceFinding` for each source.
- Verdict = AGREE / DISAGREEMENT / NOT_APPLICABLE.

LLM (when client available and verdict is DISAGREEMENT):
- Receives only the structured findings + per-source notes — never raw rows.
- Proposes a `Hypothesis` or returns null with `hypothesis_absence_note`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pluang_agent.llm import LLMClient, LLMOutputError
from pluang_agent.metrics import MetricEntry, MetricsRegistry, SourceSpec
from pluang_agent.models import (
    CrossSourceFinding,
    Hypothesis,
    LayerBReport,
    SQLAgentAnswer,
)
from pluang_agent.sql_runner import execute_read_only

_PROMPTS_DIR = Path(__file__).parents[2] / "prompts"


def run_layer_b(
    db_path: Path,
    answer: SQLAgentAnswer,
    registry: MetricsRegistry,
    llm_client: LLMClient | None = None,
) -> LayerBReport:
    """Generic cross-source reconciliation.

    Returns a LayerBReport with verdict, findings, and hypothesis (or absence note).
    Never raises — failures degrade gracefully to NOT_APPLICABLE with a note.
    """
    entry = registry.get(answer.question_id)
    if entry is None:
        return LayerBReport(
            verdict="NOT_APPLICABLE",
            hypothesis_absence_note=f"No metrics.yml entry for {answer.question_id}.",
        )
    if entry.cross_source == "disabled":
        note = entry.notes_for_layer_b or "Cross-source reconciliation disabled in metrics.yml."
        return LayerBReport(
            verdict="NOT_APPLICABLE",
            hypothesis_absence_note=note,
        )

    # --- Execute primary source ---
    try:
        primary_values = _execute_source(db_path, entry, entry.primary)
    except Exception as exc:
        return LayerBReport(
            verdict="NOT_APPLICABLE",
            hypothesis_absence_note=f"Primary source execution failed: {type(exc).__name__}: {exc}",
        )

    findings: list[CrossSourceFinding] = []
    findings.append(
        CrossSourceFinding(
            source=f"{entry.primary.table}.{entry.primary.column} (canonical primary)",
            value=_serialise_values(primary_values),
            delta_vs_primary=0.0,
            notes="Reference value for cross-source comparison.",
        )
    )

    # --- Execute alternatives and compute deltas ---
    max_abs_delta_pct: float = 0.0
    for alt in entry.alternatives:
        try:
            alt_values = _execute_source(db_path, entry, alt)
        except Exception as exc:
            findings.append(
                CrossSourceFinding(
                    source=f"{alt.table}.{alt.column}",
                    value=None,
                    delta_vs_primary=None,
                    notes=f"Execution failed: {type(exc).__name__}: {exc}",
                )
            )
            continue

        delta_pct = _max_abs_delta_pct(primary_values, alt_values)
        max_abs_delta_pct = max(max_abs_delta_pct, delta_pct)

        findings.append(
            CrossSourceFinding(
                source=f"{alt.table}.{alt.column}",
                value=_serialise_values(alt_values),
                delta_vs_primary=round(delta_pct, 4),
                notes=alt.notes,
            )
        )

    # --- Verdict ---
    if not entry.alternatives:
        verdict = "NOT_APPLICABLE"
        hypothesis = None
        absence = "No alternative sources defined for this metric."
        return LayerBReport(
            cross_source_findings=findings,
            verdict=verdict,
            hypothesis=hypothesis,
            hypothesis_absence_note=absence,
        )

    if max_abs_delta_pct > entry.disagreement_threshold_pct:
        verdict = "DISAGREEMENT"
    else:
        verdict = "AGREE"

    # --- Hypothesis (LLM-driven, only on DISAGREEMENT) ---
    hypothesis: Hypothesis | None = None
    absence: str | None = None
    if verdict == "DISAGREEMENT":
        if llm_client is not None and llm_client.available:
            try:
                hypothesis = _propose_hypothesis(
                    entry=entry,
                    findings=findings,
                    llm_client=llm_client,
                    question_id=answer.question_id,
                )
            except (LLMOutputError, json.JSONDecodeError, ValueError) as exc:
                absence = (
                    f"LLM hypothesis attempt failed ({type(exc).__name__}); "
                    "rule-based findings retained."
                )
            if hypothesis is None and absence is None:
                absence = "LLM declined to propose a grounded hypothesis."
        else:
            absence = "LLM client unavailable; rule-based findings only."
    else:
        absence = "All sources agree within disagreement threshold."

    return LayerBReport(
        cross_source_findings=findings,
        verdict=verdict,
        hypothesis=hypothesis,
        hypothesis_absence_note=absence,
    )


# ---------------------------------------------------------------------------
# SQL building and execution
# ---------------------------------------------------------------------------


def _execute_source(db_path: Path, entry: MetricEntry, src: SourceSpec) -> dict[str, float]:
    """Run a SourceSpec against the DB. Returns dict[breakdown_key, float].

    Single-value (no breakdown) is returned as {"_total_": value}.
    """
    sql, breakdown_alias = _build_sql(entry, src)
    rows = execute_read_only(db_path, sql)
    if breakdown_alias is None:
        if not rows:
            return {"_total_": 0.0}
        first = rows[0]
        # Find the value column (the one not equal to the breakdown alias)
        for v in first.values():
            f = _to_float(v)
            if f is not None:
                return {"_total_": f}
        return {"_total_": 0.0}
    out: dict[str, float] = {}
    for row in rows:
        key = str(row.get(breakdown_alias, "_unknown_"))
        # Pick the non-key numeric column
        for k, v in row.items():
            if k == breakdown_alias:
                continue
            f = _to_float(v)
            if f is not None:
                out[key] = f
                break
    return out


def _build_sql(entry: MetricEntry, src: SourceSpec) -> tuple[str, str | None]:
    """Return (sql, breakdown_alias_or_None)."""
    where_parts = [
        f"{src.period_column} >= '{entry.period_start}'",
        f"{src.period_column} < '{entry.period_end}'",
    ]
    where_parts.extend(src.extra_filters)
    where_clause = " AND ".join(where_parts)

    if src.aggregator == "SUM":
        agg_expr = f"ROUND(SUM(CAST({src.column} AS REAL)), 4)"
    elif src.aggregator == "COUNT_DISTINCT":
        agg_expr = f"COUNT(DISTINCT {src.column})"
    elif src.aggregator == "RAW":
        agg_expr = src.column
    else:
        raise ValueError(f"Unknown aggregator: {src.aggregator}")

    if src.breakdown == "month_bucket":
        sql = (
            f"SELECT substr({src.period_column}, 1, 7) AS bd_key, "
            f"{agg_expr} AS bd_value "
            f"FROM {src.table} "
            f"WHERE {where_clause} "
            f"GROUP BY bd_key ORDER BY bd_key"
        )
        return sql, "bd_key"
    if src.breakdown:
        sql = (
            f"SELECT {src.breakdown} AS bd_key, {agg_expr} AS bd_value "
            f"FROM {src.table} "
            f"WHERE {where_clause} "
            f"GROUP BY {src.breakdown} ORDER BY {src.breakdown}"
        )
        return sql, "bd_key"
    sql = (
        f"SELECT {agg_expr} AS bd_value "
        f"FROM {src.table} "
        f"WHERE {where_clause}"
    )
    return sql, None


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _max_abs_delta_pct(primary: dict[str, float], alt: dict[str, float]) -> float:
    """Maximum absolute relative delta (%) across all keys.

    Keys present in primary but missing from alt are skipped (different sources
    may have legitimately different breakdown coverage). Zero primary values
    use absolute delta normalised by 1.0 to avoid divide-by-zero.
    """
    max_pct = 0.0
    for key, p in primary.items():
        a = alt.get(key)
        if a is None:
            continue
        denom = abs(p) if abs(p) > 1e-9 else 1.0
        pct = abs(a - p) / denom * 100.0
        if pct > max_pct:
            max_pct = pct
    return max_pct


def _serialise_values(values: dict[str, float]) -> Any:
    """Convert values dict to a JSON-serialisable form for the finding."""
    if list(values.keys()) == ["_total_"]:
        return values["_total_"]
    return dict(values)


# ---------------------------------------------------------------------------
# LLM hypothesis
# ---------------------------------------------------------------------------


def _load_hypothesis_prompt() -> str:
    return (_PROMPTS_DIR / "qa_layer_b_hypothesis.md").read_text(encoding="utf-8")


def _propose_hypothesis(
    entry: MetricEntry,
    findings: list[CrossSourceFinding],
    llm_client: LLMClient,
    question_id: str,
) -> Hypothesis | None:
    """Call the LLM with structured findings and parse the hypothesis JSON."""
    system = _load_hypothesis_prompt()
    user = _build_hypothesis_user_prompt(entry, findings)
    response = llm_client.chat_json(
        system, user, stage_tag=f"qa_layer_b:{question_id}"
    )
    payload = _parse_json(response.content)
    if payload.get("hypothesis") is None:
        return None
    return Hypothesis.model_validate(payload["hypothesis"])


def _build_hypothesis_user_prompt(
    entry: MetricEntry,
    findings: list[CrossSourceFinding],
) -> str:
    findings_block = "\n".join(
        f"- source: {f.source}\n"
        f"  value: {f.value}\n"
        f"  delta_vs_primary_pct: {f.delta_vs_primary}\n"
        f"  notes: {f.notes or '(none)'}"
        for f in findings
    )
    metric_note = entry.notes_for_layer_b or "(none)"
    return (
        f"## Metric\n"
        f"id: {entry.id}\n"
        f"name: {entry.metric_name}\n"
        f"period: {entry.period_start} to {entry.period_end} (exclusive end)\n"
        f"disagreement_threshold_pct: {entry.disagreement_threshold_pct}\n\n"
        f"## Metric-level note\n"
        f"{metric_note}\n\n"
        f"## Findings\n"
        f"{findings_block}\n\n"
        f"## Your task\n"
        f"Following the rules in the system prompt, return ONLY the JSON output. "
        f"Do not include markdown fences."
    )


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        import re

        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"Layer B hypothesis JSON invalid: {exc}") from exc
