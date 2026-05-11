"""Question planner and planner QA gate.

The planner turns natural language into a typed answer contract before SQL is
generated. It is deliberately rule-based in this prototype: deterministic
plans are easier to validate, and an LLM planner can be added later as a
candidate generator that must still pass this same QA gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from pluang_agent.metadata import DbtMetadata
from pluang_agent.metrics import MetricsRegistry, SourceSpec
from pluang_agent.models import (
    BusinessQuestion,
    PlanBreakdown,
    PlanPeriod,
    PlanSource,
    QuestionPlan,
    SystemError,
)


@dataclass(frozen=True)
class PlannerResult:
    plan: QuestionPlan | None
    system_error: SystemError | None = None


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def plan_question(
    question: BusinessQuestion,
    registry: MetricsRegistry,
    metadata: DbtMetadata,
    reviewer_note: str | None = None,
) -> PlannerResult:
    entry = registry.get(question.id)
    if entry is not None:
        plan = _plan_from_registry(question, entry, reviewer_note)
    else:
        plan = _plan_from_question(question)

    issues = validate_question_plan(plan, question, metadata, registry)
    if issues:
        return PlannerResult(
            plan=None,
            system_error=SystemError(
                error_class="planner_validation_failed",
                message="; ".join(issues[:5]),
                suggested_action=(
                    "Review the generated question plan. The SQL Agent was not "
                    "called because the plan failed deterministic validation."
                ),
                raw=plan.model_dump_json(),
            ),
        )
    return PlannerResult(plan=plan)


def validate_question_plan(
    plan: QuestionPlan,
    question: BusinessQuestion,
    metadata: DbtMetadata,
    registry: MetricsRegistry,
) -> list[str]:
    issues: list[str] = []
    if plan.question_id != question.id:
        issues.append("plan question_id does not match input question")
    if not _valid_date(plan.period.start) or not _valid_date(plan.period.end):
        issues.append("plan period must use concrete YYYY-MM-DD bounds")
    if plan.period.start >= plan.period.end:
        issues.append("plan period start must be before end")

    table_columns = _table_columns(metadata)
    for label, src in [("primary_source", plan.primary_source), *[
        (f"comparison_sources[{i}]", src) for i, src in enumerate(plan.comparison_sources)
    ]]:
        cols = table_columns.get(src.table)
        if cols is None:
            issues.append(f"{label}.table not found in metadata: {src.table}")
            continue
        if src.column not in cols:
            issues.append(f"{label}.column not found in {src.table}: {src.column}")
        if src.period_column not in cols:
            issues.append(f"{label}.period_column not found in {src.table}: {src.period_column}")
        if plan.breakdown is not None and src.table == plan.primary_source.table:
            if plan.breakdown.dimension not in cols:
                issues.append(
                    f"breakdown.dimension not found in {src.table}: {plan.breakdown.dimension}"
                )

    text = question.text.lower()
    if _asks_breakdown(text) and plan.answer_shape not in {"breakdown", "breakdown_comparison"}:
        issues.append("question asks for a breakdown but plan answer_shape is not breakdown-like")
    if _asks_comparison(text) and not plan.comparison_sources:
        issues.append("question asks for comparison but plan has no comparison source")
    if _asks_period_over_period(text) and plan.answer_shape != "period_over_period":
        issues.append("question asks for trend/MoM but plan is not period_over_period")

    required = set(plan.required_output_columns)
    if plan.answer_shape == "multi_definition":
        if len(plan.required_definitions) < 2:
            issues.append("multi_definition plan must require at least two definitions")
        if not set(plan.required_definitions).issubset(required):
            issues.append("multi_definition required definitions must be output columns")
    if plan.answer_shape == "period_over_period":
        for col in ("mom_change_idr", "mom_change_pct"):
            if col not in required:
                issues.append(f"period_over_period missing required output column {col}")
    if plan.answer_shape == "breakdown_comparison":
        for col in ("absolute_delta_idr", "delta_pct"):
            if col not in required:
                issues.append(f"breakdown_comparison missing required output column {col}")
        if not plan.comparison_sources:
            issues.append("breakdown_comparison requires a comparison source")
    if plan.breakdown is not None and "Total" in _aggregate_members_for_table(plan.primary_source.table):
        if "Total" not in plan.breakdown.exclude_aggregate_members:
            issues.append("breakdown over table with Total row must exclude Total")

    entry = registry.get(question.id)
    if entry is not None and plan.source_policy == "canonical":
        if plan.primary_source.table != entry.primary.table:
            issues.append("canonical plan primary source must match registry primary source")

    return issues


def _plan_from_registry(
    question: BusinessQuestion,
    entry,
    reviewer_note: str | None,
) -> QuestionPlan:
    text = " ".join([question.text.lower(), reviewer_note.lower() if reviewer_note else ""])
    primary = _source_from_spec(entry.primary, "Registry canonical primary source.")
    comparisons = [_source_from_spec(src, src.notes or "Registry alternative source.") for src in entry.alternatives]
    breakdown = _breakdown_from_source(entry.primary)

    if _is_multi_definition_entry(entry):
        required_definitions = [
            "aum_defined_mtu",
            "raw_completed_unique_traders",
            "mixpanel_mtu",
        ]
        return QuestionPlan(
            question_id=question.id,
            metric_intent=entry.metric_name,
            period=PlanPeriod(start=entry.period_start, end=entry.period_end),
            answer_shape="multi_definition",
            primary_source=primary,
            comparison_sources=comparisons,
            breakdown=None,
            required_output_columns=required_definitions,
            required_definitions=required_definitions,
            ambiguity_policy="return_all_definitions",
            source_policy="canonical_with_definitional_alternatives",
            validation_rules=[
                "return one value for each required definition",
                "metric_value must be derived from executed SQL rows",
            ],
        )

    if _asks_period_over_period(text):
        value_col = entry.primary.column
        return QuestionPlan(
            question_id=question.id,
            metric_intent=entry.metric_name,
            period=PlanPeriod(start=entry.period_start, end=entry.period_end),
            answer_shape="period_over_period",
            primary_source=primary,
            comparison_sources=comparisons,
            breakdown=None,
            required_output_columns=["month", value_col, "mom_change_idr", "mom_change_pct"],
            source_policy="canonical",
            validation_rules=[
                "return one row per period bucket",
                "include absolute and percent period-over-period change",
                "metric_value must be derived from executed SQL rows",
            ],
        )

    if breakdown is not None:
        return QuestionPlan(
            question_id=question.id,
            metric_intent=entry.metric_name,
            period=PlanPeriod(start=entry.period_start, end=entry.period_end),
            answer_shape="breakdown",
            primary_source=primary,
            comparison_sources=comparisons,
            breakdown=breakdown,
            required_output_columns=[breakdown.dimension, entry.primary.column],
            source_policy="canonical",
            validation_rules=[
                "do not include aggregate members in breakdown output",
                "metric_value must be derived from executed SQL rows",
            ],
        )

    return QuestionPlan(
        question_id=question.id,
        metric_intent=entry.metric_name,
        period=PlanPeriod(start=entry.period_start, end=entry.period_end),
        answer_shape="scalar",
        primary_source=primary,
        comparison_sources=comparisons,
        required_output_columns=[entry.primary.column],
        source_policy="canonical",
        validation_rules=["metric_value must be derived from executed SQL rows"],
    )


def _plan_from_question(question: BusinessQuestion) -> QuestionPlan:
    text = question.text.lower()
    period = _parse_period(text) or PlanPeriod(start="2025-10-01", end="2025-11-01")
    metric = _metric_intent(text, question.metric)
    requested_ops = "ops" in text or "dashboard" in text
    breakdown = PlanBreakdown(
        dimension="asset_class",
        exclude_aggregate_members=["Total"] if requested_ops else [],
    ) if _asks_breakdown(text) else None

    primary = _heuristic_source(metric, requested_ops)
    comparisons: list[PlanSource] = []
    if _asks_comparison(text):
        comparisons.append(_heuristic_source(metric, requested_ops=False))

    shape = "scalar"
    if breakdown and comparisons:
        shape = "breakdown_comparison"
    elif breakdown:
        shape = "breakdown"
    elif _asks_period_over_period(text):
        shape = "period_over_period"

    required = _required_columns(shape, metric, primary, breakdown)
    return QuestionPlan(
        question_id=question.id,
        metric_intent=metric,
        period=period,
        answer_shape=shape,  # type: ignore[arg-type]
        primary_source=primary,
        comparison_sources=comparisons,
        breakdown=breakdown,
        required_output_columns=required,
        source_policy="user_requested_noncanonical" if requested_ops else "schema_grounded",
        validation_rules=[
            "do not include aggregate members in breakdown output",
            "metric_value must be derived from executed SQL rows",
        ],
    )


def _source_from_spec(src: SourceSpec, reason: str) -> PlanSource:
    return PlanSource(
        table=src.table,
        column=src.column,
        period_column=src.period_column,
        aggregator=src.aggregator,
        extra_filters=list(src.extra_filters),
        reason=reason,
    )


def _breakdown_from_source(src: SourceSpec) -> PlanBreakdown | None:
    if src.breakdown is None or src.breakdown == "month_bucket":
        return None
    return PlanBreakdown(
        dimension=src.breakdown,
        exclude_aggregate_members=_aggregate_members_for_table(src.table),
    )


def _aggregate_members_for_table(table: str) -> list[str]:
    if table in {"agg_monthly_biz_summary", "mart_ops_dashboard"}:
        return ["Total"]
    return []


def _is_multi_definition_entry(entry) -> bool:
    note = (entry.notes_for_layer_b or "").lower()
    return "three definitions" in note or "multiple valid definitions" in note


def _asks_breakdown(text: str) -> bool:
    return " by " in text or "across " in text or "breakdown" in text


def _asks_comparison(text: str) -> bool:
    return any(token in text for token in ("compare", "compared", "versus", " vs ", "difference"))


def _asks_period_over_period(text: str) -> bool:
    return any(token in text for token in ("month-on-month", "mom", "trend", "period-over-period"))


def _metric_intent(text: str, fallback: str) -> str:
    if "gtv" in text and "usd" in text:
        return "gtv_usd"
    if "gtv" in text:
        return "gtv_idr"
    if "transaction" in text and "count" in text:
        return "transaction_count"
    if "mtu" in text or "monthly transacting" in text:
        return "monthly_transacting_users"
    return fallback


def _heuristic_source(metric: str, requested_ops: bool) -> PlanSource:
    if requested_ops:
        column = {
            "gtv_idr": "gtv_idr",
            "gtv_usd": "gtv_usd_reported",
            "transaction_count": "total_transactions",
            "monthly_transacting_users": "mtu_mixpanel",
        }.get(metric, "gtv_idr")
        return PlanSource(
            table="mart_ops_dashboard",
            column=column,
            period_column="month",
            aggregator="SUM",
            extra_filters=[],
            reason="User explicitly requested Ops dashboard definition.",
        )
    column = {
        "gtv_idr": "gtv_idr",
        "gtv_usd": "gtv_usd",
        "transaction_count": "transaction_count",
    }.get(metric, "gtv_idr")
    return PlanSource(
        table="fct_trading_daily",
        column=column,
        period_column="transaction_date",
        aggregator="SUM",
        extra_filters=[],
        reason="Canonical completed-transaction source.",
    )


def _required_columns(
    shape: str,
    metric: str,
    primary: PlanSource,
    breakdown: PlanBreakdown | None,
) -> list[str]:
    value_col = primary.column if metric != "transaction_count" else "transaction_count"
    if shape == "breakdown_comparison":
        prefix = "ops" if primary.table == "mart_ops_dashboard" else "primary"
        return [
            breakdown.dimension if breakdown else "dimension",
            f"{prefix}_{value_col}",
            f"canonical_{value_col}",
            "absolute_delta_idr",
            "delta_pct",
        ]
    if shape == "breakdown":
        return [breakdown.dimension if breakdown else "dimension", value_col]
    if shape == "period_over_period":
        return ["month", value_col, "mom_change_idr", "mom_change_pct"]
    return [value_col]


def _parse_period(text: str) -> PlanPeriod | None:
    months = [(m, y) for m, y in re.findall(
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})",
        text,
    )]
    if not months:
        return None
    start_month, start_year = months[0]
    end_month, end_year = months[-1]
    start = date(int(start_year), MONTHS[start_month], 1)
    end = _next_month(date(int(end_year), MONTHS[end_month], 1))
    return PlanPeriod(start=start.isoformat(), end=end.isoformat())


def _next_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _table_columns(metadata: DbtMetadata) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for source_group in metadata.sources.get("sources", []):
        for table in source_group.get("tables", []):
            out[table["name"]] = {col["name"] for col in table.get("columns", [])}
    for model in metadata.models.get("models", []):
        out[model["name"]] = {col["name"] for col in model.get("columns", [])}
    return out
