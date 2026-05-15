"""Pre-flight gate — catches broken results before QA Layer A runs (R5).

Pre-flight is a separate concern from QA. It exists to ensure that QA only
ever assesses results that are *executable and non-trivially shaped*. When
pre-flight fails, the workflow retries the SQL Agent with a correction
context; only on exhaustion does the question route to AUDIT_REQUIRED.

High-precision-over-recall discipline (same as Layer A): a check FAILs only
when there's a definitely-wrong condition. Pre-flight should never block a
legitimate answer.

Layer A's overlapping checks (empty result, negative-always-positive, etc.)
stay as defence-in-depth — by the time QA sees an answer, pre-flight has
already pruned the obvious failures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pluang_agent.metrics import MetricEntry, MetricsRegistry
from pluang_agent.models import (
    BusinessQuestion,
    DerivationTrace,
    QuestionPlan,
    SQLAgentAnswer,
)


@dataclass(frozen=True)
class PreFlightResult:
    """Outcome of pre_flight_check. `hint` carries a one-line correction
    suggestion that goes into the next attempt's correction context."""

    passed: bool
    issue: str | None = None
    hint: str | None = None


def pre_flight_check(
    answer: SQLAgentAnswer,
    question: BusinessQuestion,
    registry: MetricsRegistry,
    question_plan: QuestionPlan | None = None,
) -> PreFlightResult:
    """Return PreFlightResult(passed=True) when the answer is suitable for QA.

    The checks run in order; the first failure wins, since a downstream
    issue is meaningless if rows are empty.
    """
    # System-error answers bypass pre-flight (workflow already routes them).
    if answer.system_error is not None:
        return PreFlightResult(passed=True)

    entry = registry.get(question.id)

    if not answer.result_rows:
        return PreFlightResult(
            passed=False,
            issue="empty_result",
            hint=(
                "The previous SQL returned zero rows. Re-check the period filter "
                "format (period_column stores YYYY-MM-DD, not YYYY-MM); re-check "
                "every extra_filter; and confirm the table actually has rows in "
                "the requested period."
            ),
        )

    shape_check = _check_answer_shape(answer, question_plan)
    if shape_check is not None:
        return shape_check

    # R6: trace-driven checks. All three are data-driven (read what the trace
    # says, compare to what the answer/SQL did). They fire on any question
    # whose trace expresses the condition — never on hardcoded question_ids.
    if answer.derivation_trace is not None:
        trace_check = _check_trace_complete(answer.derivation_trace)
        if trace_check is not None:
            return trace_check
        filter_check = _check_filters_match_trace(answer, answer.derivation_trace)
        if filter_check is not None:
            return filter_check
        ambiguity_check = _check_ambiguity_surfaced(answer, answer.derivation_trace)
        if ambiguity_check is not None:
            return ambiguity_check

    null_check = _check_all_null_primary(answer, entry)
    if null_check is not None:
        return null_check

    negative_check = _check_no_negative_for_always_positive(answer)
    if negative_check is not None:
        return negative_check

    if entry is not None:
        range_check = _check_plausible_range(answer, entry)
        if range_check is not None:
            return range_check

    return PreFlightResult(passed=True)


# ---------------------------------------------------------------------------
# R6: trace-driven checks (data-driven, not question_id-driven)
# ---------------------------------------------------------------------------


def _check_trace_complete(trace: DerivationTrace) -> PreFlightResult | None:
    """Structural completeness check on the trace itself.

    Catches a trace that passed the planner's validator but somehow lost a
    field downstream (defence-in-depth — pre-flight runs the same shape
    invariants the planner already enforced).
    """
    if not trace.candidate_sources:
        return PreFlightResult(
            passed=False,
            issue="trace_no_candidates",
            hint="Derivation trace has no candidate_sources — planner must enumerate at least one.",
        )
    selected = [c for c in trace.candidate_sources if c.selected]
    if len(selected) != 1:
        return PreFlightResult(
            passed=False,
            issue="trace_selection_invariant",
            hint=f"Trace must have exactly one selected=True candidate; found {len(selected)}.",
        )
    if selected[0].table != trace.chosen_source:
        return PreFlightResult(
            passed=False,
            issue="trace_chosen_source_mismatch",
            hint=(
                f"trace.chosen_source ({trace.chosen_source!r}) does not match the "
                f"selected candidate ({selected[0].table!r})."
            ),
        )
    for c in trace.candidate_sources:
        if not c.selected and not (c.rejection_reason or "").strip():
            return PreFlightResult(
                passed=False,
                issue="trace_rejection_reason_missing",
                hint=(
                    f"Non-selected candidate {c.table!r} has no rejection_reason. "
                    "Reviewer can't audit the choice without it."
                ),
            )
    if not trace.aggregator_rationale.strip():
        return PreFlightResult(
            passed=False,
            issue="trace_aggregator_rationale_missing",
            hint="trace.aggregator_rationale is empty.",
        )
    return None


def _check_filters_match_trace(
    answer: SQLAgentAnswer, trace: DerivationTrace
) -> PreFlightResult | None:
    """Every trace.chosen_filters entry must appear (syntactically) in the SQL.

    Data-driven: the test fires only if the trace declared specific filters.
    A scalar question with no filters skips this check. A trace that mandates
    `asset_class != 'Total'` will fail here if the SQL drops it.
    """
    sql_norm = _normalise_sql(answer.sql)
    missing: list[str] = []
    for f in trace.chosen_filters:
        if not _filter_present(sql_norm, f):
            missing.append(f)
    if missing:
        return PreFlightResult(
            passed=False,
            issue="filters_missing_from_sql",
            hint=(
                f"Trace requires filters that aren't in the SQL: {missing}. "
                "Add them to the WHERE clause exactly as declared in chosen_filters."
            ),
        )
    return None


def _check_ambiguity_surfaced(
    answer: SQLAgentAnswer, trace: DerivationTrace
) -> PreFlightResult | None:
    """When the trace shows >= 2 candidates with grain_match=='exact' AND
    every required scope predicate is feasible on each of them, that's
    *real* ambiguity — multiple sources could answer the question equally
    well. The answer MUST populate interpretation_choices.

    Data-driven, not shape-driven: this fires for scalar / breakdown_comparison /
    multi_definition alike, as long as the trace shows multiple equally-feasible
    candidates. When only one source is feasible, no interpretation_choices is
    required (no real ambiguity to surface).
    """
    fully_feasible: list[str] = []
    predicates = trace.scope_predicates or []
    for c in trace.candidate_sources:
        if c.grain_match != "exact":
            continue
        all_feasible = True
        for p in predicates:
            value = c.scope_feasibility.get(p, "")
            if not value.strip().startswith("feasible_via"):
                all_feasible = False
                break
        if all_feasible:
            fully_feasible.append(c.table)
    if len(fully_feasible) >= 2 and len(answer.interpretation_choices) == 0:
        return PreFlightResult(
            passed=False,
            issue="ambiguity_not_surfaced",
            hint=(
                f"Trace shows {len(fully_feasible)} equally-feasible sources "
                f"({fully_feasible}) for this question, but interpretation_choices "
                f"is empty. Populate interpretation_choices with one entry "
                f"describing the source choice + alternatives + rationale."
            ),
        )
    return None


def _normalise_sql(sql: str) -> str:
    """Lowercase + squash runs of whitespace for filter substring matching."""
    return re.sub(r"\s+", " ", sql.lower()).strip()


def _filter_present(sql_norm: str, declared_filter: str) -> bool:
    """Approximate match: declared filter present in normalised SQL.

    We accept either an exact substring match or a relaxed token-match (all
    identifier+operator+literal tokens in the same order). This avoids
    false-positives from cosmetic SQL formatting differences.
    """
    f_norm = _normalise_sql(declared_filter)
    if f_norm in sql_norm:
        return True
    # Token-level fallback: pull alphanumeric + comparison tokens in order
    tokens = [t for t in re.findall(r"[a-z0-9_]+|<>|<=|>=|=|<|>|!=", f_norm) if t]
    cursor = 0
    for tok in tokens:
        found = sql_norm.find(tok, cursor)
        if found < 0:
            return False
        cursor = found + len(tok)
    return True


def _check_answer_shape(
    answer: SQLAgentAnswer,
    plan: QuestionPlan | None,
) -> PreFlightResult | None:
    if plan is None:
        return None

    if answer.source is None:
        return PreFlightResult(
            passed=False,
            issue="source_missing",
            hint="Return source.primary_table so the answer can be checked against the validated plan.",
        )
    if answer.source.primary_table != plan.primary_source.table:
        return PreFlightResult(
            passed=False,
            issue="source_mismatch",
            hint=(
                f"The validated plan requires primary source {plan.primary_source.table}, "
                f"but the answer declared {answer.source.primary_table}. Rewrite using the "
                "validated plan source or route to audit if the source policy is impossible."
            ),
        )
    if plan.primary_source.table not in answer.sql:
        return PreFlightResult(
            passed=False,
            issue="sql_source_mismatch",
            hint=f"SQL must query the validated primary source {plan.primary_source.table}.",
        )

    missing = _missing_required_columns(answer, plan.required_output_columns)
    if missing:
        comparison_hint = ""
        if plan.answer_shape == "breakdown_comparison":
            comparison_hint = (
                " For breakdown_comparison, aggregate the primary source and "
                "comparison source in separate CTEs, join by the breakdown key, "
                "and output primary value, comparison value, absolute_delta_idr, "
                "and delta_pct."
            )
        return PreFlightResult(
            passed=False,
            issue="required_columns_missing",
            hint=(
                f"Executed rows are missing required output column(s): {missing}. "
                "Use the exact aliases from required_output_columns in the validated plan."
                f"{comparison_hint}"
            ),
        )

    if plan.breakdown is not None and plan.breakdown.exclude_aggregate_members:
        bad_rows: list[str] = []
        for idx, row in enumerate(answer.result_rows, start=1):
            value = str(row.get(plan.breakdown.dimension, ""))
            if value in plan.breakdown.exclude_aggregate_members:
                bad_rows.append(f"row {idx}.{plan.breakdown.dimension}={value}")
        if bad_rows:
            return PreFlightResult(
                passed=False,
                issue="aggregate_member_in_breakdown",
                hint=(
                    f"The validated plan excludes aggregate breakdown members "
                    f"{plan.breakdown.exclude_aggregate_members}, but found {bad_rows[:3]}. "
                    "Add the appropriate filter, such as asset_class != 'Total'."
                ),
            )

    if plan.answer_shape == "multi_definition":
        missing_defs = _missing_required_columns(answer, plan.required_definitions)
        if missing_defs:
            return PreFlightResult(
                passed=False,
                issue="missing_required_definitions",
                hint=(
                    f"Multi-definition answer must return one value per required definition: "
                    f"{plan.required_definitions}. Missing {missing_defs}."
                ),
            )
        if len(answer.interpretation_choices) == 0:
            return PreFlightResult(
                passed=False,
                issue="missing_interpretation_choices",
                hint="Multi-definition answer must populate interpretation_choices.",
            )

    if plan.answer_shape == "period_over_period":
        pop_check = _check_period_over_period_pct(answer, plan)
        if pop_check is not None:
            return pop_check

    if plan.answer_shape == "breakdown_comparison" and not plan.comparison_sources:
        return PreFlightResult(
            passed=False,
            issue="comparison_source_missing",
            hint="Breakdown comparison plans must include and query a comparison source.",
        )

    return None


def _check_period_over_period_pct(
    answer: SQLAgentAnswer,
    plan: QuestionPlan,
) -> PreFlightResult | None:
    value_col = plan.primary_source.column
    if value_col not in plan.required_output_columns:
        candidates = [
            col for col in plan.required_output_columns
            if col not in {"month", "mom_change_idr", "mom_change_pct"}
        ]
        value_col = candidates[0] if candidates else value_col
    for idx, row in enumerate(answer.result_rows[1:], start=2):
        change = _as_float(row.get("mom_change_idr"))
        pct = _as_float(row.get("mom_change_pct"))
        value = _as_float(row.get(value_col))
        prev_value = _as_float(answer.result_rows[idx - 2].get(value_col))
        if change is None or pct is None:
            continue
        if abs(change) > 1e-9 and abs(pct) < 1e-9:
            return PreFlightResult(
                passed=False,
                issue="period_over_period_pct_zero",
                hint=(
                    "mom_change_pct is zero while mom_change_idr is non-zero. "
                    "SQLite likely performed integer division. Cast numerator or "
                    "denominator to REAL, or multiply by 100.0 before division."
                ),
            )
        if value is not None and prev_value not in (None, 0):
            expected = (value - prev_value) / prev_value * 100.0
            if abs(expected) > 0.01 and abs((pct or 0) - expected) > max(0.05, abs(expected) * 0.1):
                return PreFlightResult(
                    passed=False,
                    issue="period_over_period_pct_incorrect",
                    hint=(
                        f"mom_change_pct={pct} does not match computed percent change "
                        f"~{expected:.4f}. Recompute using REAL arithmetic."
                    ),
                )
    return None


def _missing_required_columns(answer: SQLAgentAnswer, required: list[str]) -> list[str]:
    if not required:
        return []
    present: set[str] = set()
    for row in answer.result_rows:
        present.update(row.keys())
    return [col for col in required if col not in present]


def _check_all_null_primary(
    answer: SQLAgentAnswer, entry: MetricEntry | None
) -> PreFlightResult | None:
    """Fail when every row's primary metric column is null.

    When the registry has no entry, fall back to a generic "all values are
    null across all rows" heuristic.
    """
    if entry is not None:
        col = entry.primary.column
        # Only check if that column actually appears in the result.
        column_present = any(col in row for row in answer.result_rows)
        if column_present:
            if all(_is_null_or_empty(row.get(col)) for row in answer.result_rows):
                return PreFlightResult(
                    passed=False,
                    issue="all_null_primary",
                    hint=(
                        f"Every row returned NULL for {col!r} (the registry's primary "
                        f"column). Likely causes: a filter excluded all populated rows "
                        f"(e.g. asset_class filter on a column populated only on the "
                        f"Total row), or you queried the wrong table. Re-check the "
                        f"NOTE warnings on the chosen table."
                    ),
                )
        return None
    # Generic fallback when no registry entry: only fail if EVERY value in
    # EVERY row is null (very rare with a real SELECT — high-precision).
    all_null = True
    for row in answer.result_rows:
        for value in row.values():
            if not _is_null_or_empty(value):
                all_null = False
                break
        if not all_null:
            break
    if all_null and answer.result_rows:
        return PreFlightResult(
            passed=False,
            issue="all_null_metric",
            hint="Every value in every returned row is NULL. The query likely "
            "selected only null-populated columns; re-check the SELECT list.",
        )
    return None


def _check_no_negative_for_always_positive(
    answer: SQLAgentAnswer,
) -> PreFlightResult | None:
    """Fail when an always-positive metric (gtv/transaction_count/mtu/trader)
    contains a negative value. mom_change / delta columns are exempt."""
    for idx, row in enumerate(answer.result_rows, start=1):
        for key, value in row.items():
            if not _is_always_positive_key(key):
                continue
            f = _as_float(value)
            if f is None:
                continue
            if f < 0:
                return PreFlightResult(
                    passed=False,
                    issue="negative_metric",
                    hint=(
                        f"Row {idx}.{key} = {f}, but {key!r} should always be "
                        f"positive. Likely cause: SQL subtracted instead of summing, "
                        f"or applied an aggregator to the wrong column. Re-check the "
                        f"registry's aggregator and column choice."
                    ),
                )
    return None


def _check_plausible_range(
    answer: SQLAgentAnswer, entry: MetricEntry
) -> PreFlightResult | None:
    """Fail when any always-positive value lies outside [expected_min,
    expected_max] from metrics.yml. Skipped when bounds are unset."""
    if entry.expected_min is None and entry.expected_max is None:
        return None
    for idx, row in enumerate(answer.result_rows, start=1):
        for key, value in row.items():
            if not _is_always_positive_key(key):
                continue
            f = _as_float(value)
            if f is None:
                continue
            if entry.expected_min is not None and f < entry.expected_min:
                return PreFlightResult(
                    passed=False,
                    issue="out_of_range_below",
                    hint=(
                        f"Row {idx}.{key} = {f}, below expected_min "
                        f"({entry.expected_min}). Either the metric is mis-aggregated "
                        f"(e.g. COUNT on a pre-aggregated mart) or the registry "
                        f"bound is too tight; pick the right one."
                    ),
                )
            if entry.expected_max is not None and f > entry.expected_max:
                return PreFlightResult(
                    passed=False,
                    issue="out_of_range_above",
                    hint=(
                        f"Row {idx}.{key} = {f}, above expected_max "
                        f"({entry.expected_max}). Likely cause: double counting "
                        f"(e.g. including a Total row alongside per-asset rows) or "
                        f"wrong source. Re-check NOTE warnings on the chosen table."
                    ),
                )
    return None


def _is_null_or_empty(value: Any) -> bool:
    return value is None or value == ""


def _is_always_positive_key(key: str) -> bool:
    lowered = key.lower()
    if "mom_change" in lowered or "delta" in lowered:
        return False
    return any(token in lowered for token in ("gtv", "transaction_count", "mtu", "trader"))


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
