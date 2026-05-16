"""Layer A plausibility check — fires only when expected_min/max are configured."""

from __future__ import annotations

from trust_analytics.metrics import MetricEntry, SourceSpec
from trust_analytics.models import SourceProvenance, SQLAgentAnswer
from trust_analytics.quality_rules import run_layer_a


def _entry(expected_min: float | None, expected_max: float | None) -> MetricEntry:
    return MetricEntry(
        id="test_q",
        metric_name="test_metric",
        cross_source="disabled",
        period_start="2025-10-01",
        period_end="2025-11-01",
        primary=SourceSpec(
            table="t",
            column="gtv_idr",
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
        period="oct 2025",
        source=SourceProvenance(primary_table="t", why_chosen="x", alternatives_available=[]),
        sql="SELECT 1",
        logic="x",
        result_rows=rows,
    )


def test_plausibility_skipped_when_no_bounds() -> None:
    entry = _entry(None, None)
    answer = _answer([{"gtv_idr": 1.0}])
    report = run_layer_a(answer, metric_entry=entry)
    plaus = next(c for c in report.checks if c.name == "plausible_range")
    assert plaus.result == "NOT_APPLICABLE"


def test_plausibility_passes_in_range() -> None:
    entry = _entry(100.0, 1_000_000.0)
    answer = _answer([{"gtv_idr": 50_000.0}])
    report = run_layer_a(answer, metric_entry=entry)
    plaus = next(c for c in report.checks if c.name == "plausible_range")
    assert plaus.result == "PASS"


def test_plausibility_fails_above_max() -> None:
    """Catches the Q5 double-count case: ~76B reported, ceiling 1T per month would
    not catch it but a tighter ceiling would. Use a synthetic ceiling here."""
    entry = _entry(100.0, 1000.0)
    answer = _answer([{"gtv_idr": 5000.0}])
    report = run_layer_a(answer, metric_entry=entry)
    plaus = next(c for c in report.checks if c.name == "plausible_range")
    assert plaus.result == "FAIL"
    assert any("above expected_max" in e for e in plaus.evidence)


def test_plausibility_fails_below_min() -> None:
    entry = _entry(100.0, 1000.0)
    answer = _answer([{"gtv_idr": 5.0}])
    report = run_layer_a(answer, metric_entry=entry)
    plaus = next(c for c in report.checks if c.name == "plausible_range")
    assert plaus.result == "FAIL"
    assert any("below expected_min" in e for e in plaus.evidence)


def test_plausibility_ignores_non_metric_keys() -> None:
    """Breakdown column 'asset_class' or delta-percent columns aren't checked."""
    entry = _entry(100.0, 1000.0)
    answer = _answer([{"asset_class": "crypto", "gtv_idr": 500.0, "mom_change_pct": -50.0}])
    report = run_layer_a(answer, metric_entry=entry)
    plaus = next(c for c in report.checks if c.name == "plausible_range")
    assert plaus.result == "PASS"
