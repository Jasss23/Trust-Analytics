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

from pluang_agent.models import LayerACheck, LayerAReport, SQLAgentAnswer


def run_layer_a(answer: SQLAgentAnswer) -> LayerAReport:
    """Run all Layer A rules and return a structured report."""
    checks: list[LayerACheck] = []
    checks.append(_check_non_empty_result(answer))
    checks.append(_check_no_required_nulls(answer))
    checks.append(_check_no_negative_for_always_positive(answer))
    checks.append(_check_no_zero_for_must_be_positive(answer))
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
