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

from dataclasses import dataclass
from typing import Any

from pluang_agent.metrics import MetricEntry, MetricsRegistry
from pluang_agent.models import BusinessQuestion, SQLAgentAnswer


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
                        f"⚠️ warnings on the chosen table."
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
                        f"wrong source. Re-check ⚠️ warnings."
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
