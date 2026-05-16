"""Layer A — rule-based, high-precision data-quality checks.

Per Decision 4: Layer A's invariant is "rule fires → definitely a problem;
rule doesn't fire ≠ no problem (that's B and C's job)." Prefer false negatives
over false positives — false positives at A poison reviewer trust in the whole
system.

The per-question_id reconciliation handlers that lived here were removed at
R1 in line with the locked decision to replace them with metrics.yml + generic
Layer B at R3.
"""

from __future__ import annotations

from typing import Any

from trust_analytics.metrics import MetricEntry
from trust_analytics.models import LayerACheck, LayerAReport, QuestionPlan, SQLAgentAnswer


def run_layer_a(
    answer: SQLAgentAnswer,
    metric_entry: MetricEntry | None = None,
    question_plan: QuestionPlan | None = None,
) -> LayerAReport:
    """Run all Layer A rules and return a structured report.

    `metric_entry` enables the plausibility-range check (expected_min/max).
    Layer A stays high-precision: a check FAILs only when there's a definitely-
    wrong condition (null required, negative metric, zero where impossible,
    out-of-range value). Anything more interpretive belongs to Layer B/C.
    """
    checks: list[LayerACheck] = []
    checks.append(_check_non_empty_result(answer))
    checks.append(_check_no_required_nulls(answer))
    checks.append(_check_no_negative_for_always_positive(answer))
    checks.append(_check_no_zero_for_must_be_positive(answer))
    if question_plan is not None:
        checks.append(_check_required_columns_present(answer, question_plan))
        checks.append(_check_no_aggregate_member_in_breakdown(answer, question_plan))
        checks.append(_check_metric_value_matches_result_rows(answer))
        checks.append(_check_source_matches_plan(answer, question_plan))
    if metric_entry is not None:
        checks.append(_check_plausible_range(answer, metric_entry))
    return LayerAReport(checks=checks)


def _check_non_empty_result(answer: SQLAgentAnswer) -> LayerACheck:
    if not answer.result_rows:
        return LayerACheck(
            name="non_empty_result",
            result="FAIL",
            detail="The SQL query returned no rows.",
            evidence=[answer.sql],
        )
    return LayerACheck(name="non_empty_result", result="PASS")


def _check_no_required_nulls(answer: SQLAgentAnswer) -> LayerACheck:
    null_fields = _scan(
        answer,
        lambda key, value: value in (None, "")
        and _is_required_value_key(key),
        format_evidence=lambda key, value, idx: f"row {idx}.{key}",
    )
    if null_fields:
        return LayerACheck(
            name="no_required_nulls",
            result="FAIL",
            detail="Required metric field is null/empty.",
            evidence=null_fields[:10],
        )
    return LayerACheck(name="no_required_nulls", result="PASS")


def _check_no_negative_for_always_positive(answer: SQLAgentAnswer) -> LayerACheck:
    negative_fields = _scan(
        answer,
        lambda key, value: _as_float(value) is not None
        and (_as_float(value) or 0) < 0
        and _is_always_positive_key(key),
        format_evidence=lambda key, value, idx: f"row {idx}.{key}={value}",
    )
    if negative_fields:
        return LayerACheck(
            name="no_negative_for_always_positive",
            result="FAIL",
            detail="Metric that must be positive is negative.",
            evidence=negative_fields[:10],
        )
    return LayerACheck(name="no_negative_for_always_positive", result="PASS")


def _check_plausible_range(answer: SQLAgentAnswer, entry: MetricEntry) -> LayerACheck:
    """Flag values outside [expected_min, expected_max] from metrics.yml.

    Skipped (NOT_APPLICABLE) when bounds are not specified. For breakdown queries,
    every numeric value in the breakdown is checked. For the MTU-style 3-source
    answer, every numeric value is checked against the same bounds — generous
    bounds are recommended to avoid false positives across legitimate
    definitional differences.
    """
    if entry.expected_min is None and entry.expected_max is None:
        return LayerACheck(
            name="plausible_range",
            result="NOT_APPLICABLE",
            detail="No expected_min/expected_max in metrics.yml.",
        )
    out_of_range: list[str] = []
    for idx, row in enumerate(answer.result_rows, start=1):
        for key, value in row.items():
            f = _as_float(value)
            if f is None:
                continue
            # Skip non-metric numeric columns (delta, percent, breakdown keys).
            if not _is_always_positive_key(key):
                continue
            if entry.expected_min is not None and f < entry.expected_min:
                out_of_range.append(
                    f"row {idx}.{key}={f} below expected_min={entry.expected_min}"
                )
            if entry.expected_max is not None and f > entry.expected_max:
                out_of_range.append(
                    f"row {idx}.{key}={f} above expected_max={entry.expected_max}"
                )
    if out_of_range:
        return LayerACheck(
            name="plausible_range",
            result="FAIL",
            detail="At least one value falls outside the plausibility range.",
            evidence=out_of_range[:10],
        )
    return LayerACheck(name="plausible_range", result="PASS")


def _check_no_zero_for_must_be_positive(answer: SQLAgentAnswer) -> LayerACheck:
    zero_fields = _scan(
        answer,
        lambda key, value: _as_float(value) == 0 and _is_always_positive_key(key),
        format_evidence=lambda key, value, idx: f"row {idx}.{key}=0",
    )
    if zero_fields:
        return LayerACheck(
            name="no_zero_for_must_be_positive",
            result="FAIL",
            detail="Metric expected to be positive is zero.",
            evidence=zero_fields[:10],
        )
    return LayerACheck(name="no_zero_for_must_be_positive", result="PASS")


def _check_required_columns_present(
    answer: SQLAgentAnswer, plan: QuestionPlan
) -> LayerACheck:
    present: set[str] = set()
    for row in answer.result_rows:
        present.update(row.keys())
    missing = [col for col in plan.required_output_columns if col not in present]
    if missing:
        return LayerACheck(
            name="required_columns_present",
            result="FAIL",
            detail="Executed rows are missing columns required by the validated plan.",
            evidence=missing,
        )
    return LayerACheck(name="required_columns_present", result="PASS")


def _check_no_aggregate_member_in_breakdown(
    answer: SQLAgentAnswer, plan: QuestionPlan
) -> LayerACheck:
    if plan.breakdown is None or not plan.breakdown.exclude_aggregate_members:
        return LayerACheck(name="no_aggregate_member_in_breakdown", result="NOT_APPLICABLE")
    evidence: list[str] = []
    for idx, row in enumerate(answer.result_rows, start=1):
        value = str(row.get(plan.breakdown.dimension, ""))
        if value in plan.breakdown.exclude_aggregate_members:
            evidence.append(f"row {idx}.{plan.breakdown.dimension}={value}")
    if evidence:
        return LayerACheck(
            name="no_aggregate_member_in_breakdown",
            result="FAIL",
            detail="Breakdown includes an aggregate member excluded by the validated plan.",
            evidence=evidence[:10],
        )
    return LayerACheck(name="no_aggregate_member_in_breakdown", result="PASS")


def _check_metric_value_matches_result_rows(answer: SQLAgentAnswer) -> LayerACheck:
    if answer.metric_value != answer.result_rows:
        return LayerACheck(
            name="metric_value_matches_result_rows",
            result="FAIL",
            detail="metric_value must be derived from executed SQL rows.",
            evidence=["metric_value differs from result_rows"],
        )
    return LayerACheck(name="metric_value_matches_result_rows", result="PASS")


def _check_source_matches_plan(answer: SQLAgentAnswer, plan: QuestionPlan) -> LayerACheck:
    if answer.source is None:
        return LayerACheck(
            name="source_matches_validated_plan",
            result="FAIL",
            detail="Answer did not declare source provenance.",
        )
    if answer.source.primary_table != plan.primary_source.table:
        return LayerACheck(
            name="source_matches_validated_plan",
            result="FAIL",
            detail="Answer source does not match the validated question plan.",
            evidence=[f"{answer.source.primary_table} != {plan.primary_source.table}"],
        )
    return LayerACheck(name="source_matches_validated_plan", result="PASS")


def _scan(
    answer: SQLAgentAnswer,
    predicate,
    format_evidence,
) -> list[str]:
    out: list[str] = []
    for idx, row in enumerate(answer.result_rows, start=1):
        for key, value in row.items():
            if predicate(key, value):
                out.append(format_evidence(key, value, idx))
    return out


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_always_positive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in ("gtv", "transaction_count", "mtu", "trader"))


def _is_required_value_key(key: str) -> bool:
    lowered = key.lower()
    if "mom_change" in lowered or "delta" in lowered:
        # Window-function nulls (LAG over the first row) are expected.
        return False
    return _is_always_positive_key(key)
