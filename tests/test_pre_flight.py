"""Pre-flight gate tests (R5).

Catches broken results before QA runs. High-precision-over-recall: a check
FAILs only when there's a definitely-wrong condition.
"""

from __future__ import annotations

from pluang_agent.metrics import MetricEntry, MetricsRegistry, SourceSpec
from pluang_agent.models import BusinessQuestion, SourceProvenance, SQLAgentAnswer
from pluang_agent.pre_flight import pre_flight_check


def _question() -> BusinessQuestion:
    return BusinessQuestion(
        id="test_q",
        text="test",
        metric="test_metric",
        period="October 2025",
    )


def _registry(entry: MetricEntry | None = None) -> MetricsRegistry:
    return MetricsRegistry(entries={entry.id: entry} if entry else {})


def _entry(
    primary_column: str = "gtv_idr",
    expected_min: float | None = None,
    expected_max: float | None = None,
) -> MetricEntry:
    return MetricEntry(
        id="test_q",
        metric_name="test_metric",
        cross_source="disabled",
        period_start="2025-10-01",
        period_end="2025-11-01",
        primary=SourceSpec(
            table="t",
            column=primary_column,
            period_column="d",
            extra_filters=(),
            breakdown=None,
            aggregator="SUM",
        ),
        expected_min=expected_min,
        expected_max=expected_max,
    )


def _answer(rows: list[dict]) -> SQLAgentAnswer:
    return SQLAgentAnswer(
        question_id="test_q",
        question="?",
        metric_name="test_metric",
        metric_value=rows,
        period="October 2025",
        source=SourceProvenance(primary_table="t", why_chosen="x", alternatives_available=[]),
        sql="SELECT 1",
        logic="x",
        result_rows=rows,
    )


def test_passes_on_clean_answer() -> None:
    entry = _entry(expected_min=100.0, expected_max=1_000_000.0)
    answer = _answer([{"gtv_idr": 50_000.0}])
    result = pre_flight_check(answer, _question(), _registry(entry))
    assert result.passed is True
    assert result.issue is None


def test_fails_on_empty_result() -> None:
    answer = _answer([])
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is False
    assert result.issue == "empty_result"
    assert result.hint is not None
    assert "period" in result.hint.lower() or "filter" in result.hint.lower()


def test_fails_on_negative_always_positive_metric() -> None:
    answer = _answer([{"gtv_idr": -100.0}])
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is False
    assert result.issue == "negative_metric"


def test_passes_on_negative_mom_change_column() -> None:
    """mom_change / delta columns are exempt from always-positive check."""
    answer = _answer([{"gtv_idr": 100.0, "mom_change_pct": -50.0}])
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is True


def test_fails_on_out_of_range_above() -> None:
    entry = _entry(expected_min=100.0, expected_max=1000.0)
    answer = _answer([{"gtv_idr": 5000.0}])
    result = pre_flight_check(answer, _question(), _registry(entry))
    assert result.passed is False
    assert result.issue == "out_of_range_above"
    assert result.hint is not None
    assert "double counting" in result.hint.lower() or "above" in result.hint.lower()


def test_fails_on_out_of_range_below() -> None:
    entry = _entry(expected_min=100.0, expected_max=1000.0)
    answer = _answer([{"gtv_idr": 5.0}])
    result = pre_flight_check(answer, _question(), _registry(entry))
    assert result.passed is False
    assert result.issue == "out_of_range_below"


def test_fails_on_all_null_primary_column() -> None:
    """When the registry's primary column is all-NULL across every returned
    row, the SQL likely queried the wrong slice (e.g. asset-class filter on
    a Total-only column)."""
    entry = _entry(primary_column="mtu")
    answer = _answer([{"mtu": None}, {"mtu": None}])
    result = pre_flight_check(answer, _question(), _registry(entry))
    assert result.passed is False
    assert result.issue == "all_null_primary"


def test_passes_when_some_primary_values_present() -> None:
    """If any row has a populated primary value, it's not all-null — pass."""
    entry = _entry(primary_column="mtu")
    answer = _answer([{"mtu": None}, {"mtu": 12453}])
    result = pre_flight_check(answer, _question(), _registry(entry))
    assert result.passed is True


def test_bypasses_when_system_error_set() -> None:
    """System-errored answers should not be re-checked — the workflow
    already routes them."""
    answer = _answer([])
    from pluang_agent.models import SystemError as _SystemError

    answer.system_error = _SystemError(
        error_class="quota",
        message="quota out",
        suggested_action="add credit",
    )
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is True
