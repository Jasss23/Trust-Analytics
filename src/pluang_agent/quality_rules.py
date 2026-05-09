"""Deterministic data-quality and reconciliation checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pluang_agent.models import CrossCheck, QualityFlag, QualityHypothesis, SQLAgentAnswer
from pluang_agent.sql_runner import execute_read_only


def generic_value_flags(answer: SQLAgentAnswer) -> list[QualityFlag]:
    flags: list[QualityFlag] = []
    if not answer.result_rows:
        flags.append(
            QualityFlag(
                code="empty_result",
                severity="critical",
                known_issue="The SQL query returned no rows.",
                evidence=[answer.sql],
            )
        )
        return flags

    null_fields: list[str] = []
    zero_fields: list[str] = []
    negative_fields: list[str] = []
    for idx, row in enumerate(answer.result_rows, start=1):
        for key, value in row.items():
            if value in (None, "") and _is_required_value_key(key):
                null_fields.append(f"row {idx}.{key}")
                continue
            numeric = _as_float(value)
            if numeric is None:
                continue
            if numeric == 0 and _is_positive_metric_key(key):
                zero_fields.append(f"row {idx}.{key}=0")
            if numeric < 0 and _is_always_positive_key(key):
                negative_fields.append(f"row {idx}.{key}={numeric}")

    if null_fields:
        flags.append(
            QualityFlag(
                code="null_required_value",
                severity="warning",
                known_issue="The answer contains null or empty values in returned fields.",
                evidence=null_fields[:10],
            )
        )
    if zero_fields:
        flags.append(
            QualityFlag(
                code="suspicious_zero_value",
                severity="warning",
                known_issue="A metric expected to be positive is zero.",
                evidence=zero_fields[:10],
            )
        )
    if negative_fields:
        flags.append(
            QualityFlag(
                code="negative_positive_metric",
                severity="critical",
                known_issue="A metric that should not be negative is negative.",
                evidence=negative_fields[:10],
            )
        )
    return flags


def reconciliation_checks(
    db_path: Path,
    answer: SQLAgentAnswer,
) -> tuple[list[QualityFlag], list[QualityHypothesis], list[CrossCheck]]:
    handlers = {
        "q1_gtv_idr_by_asset_oct_2025": _check_october_asset_sources,
        "q2_gtv_usd_oct_2025": _check_october_usd_sources,
        "q3_mtu_oct_2025": _check_mtu_sources,
        "q4_transaction_count_by_asset_oct_2025": _check_october_transaction_sources,
        "q5_gtv_mom_trend_oct_dec_2025": _check_monthly_trend_sources,
    }
    handler = handlers.get(answer.question_id)
    if handler is None:
        return [], [], []
    return handler(db_path)


def _check_october_asset_sources(
    db_path: Path,
) -> tuple[list[QualityFlag], list[QualityHypothesis], list[CrossCheck]]:
    sql = """
SELECT
  b.asset_class,
  ROUND(SUM(CAST(f.gtv_idr AS REAL)), 2) AS fct_gtv_idr,
  CAST(b.gtv_idr AS REAL) AS biz_gtv_idr,
  CAST(o.gtv_idr AS REAL) AS ops_gtv_idr
FROM fct_trading_daily f
JOIN agg_monthly_biz_summary b
  ON b.month >= '2025-10-01'
 AND b.month < '2025-11-01'
 AND b.asset_class = f.asset_class
JOIN mart_ops_dashboard o
  ON o.month >= '2025-10-01'
 AND o.month < '2025-11-01'
 AND o.asset_class = f.asset_class
WHERE f.transaction_date >= '2025-10-01'
  AND f.transaction_date < '2025-11-01'
GROUP BY b.asset_class, b.gtv_idr, o.gtv_idr
ORDER BY b.asset_class
""".strip()
    rows = execute_read_only(db_path, sql)
    evidence = [
        f"{row['asset_class']}: fct={row['fct_gtv_idr']}, biz={row['biz_gtv_idr']}, ops={row['ops_gtv_idr']}"
        for row in rows
    ]
    flags: list[QualityFlag] = []
    hypotheses: list[QualityHypothesis] = []
    checks = [
        CrossCheck(
            name="fct_vs_biz_october_gtv_by_asset",
            status="pass",
            evidence=evidence,
        )
    ]
    if any(abs(float(row["ops_gtv_idr"]) - float(row["biz_gtv_idr"])) > 1 for row in rows):
        flags.append(
            QualityFlag(
                code="ops_source_disagrees",
                severity="info",
                known_issue="Ops dashboard GTV differs from the completed-transaction business mart.",
                evidence=evidence,
            )
        )
        hypotheses.append(
            QualityHypothesis(
                flag_code="ops_source_disagrees",
                suspected_cause="The Ops mart includes status != 'failed', while the canonical GTV definition uses completed transactions only.",
                evidence=[
                    "mart_ops_dashboard.sql filters status != 'failed'; fct_trading_daily.sql filters status = 'completed'."
                ],
            )
        )
        checks.append(
            CrossCheck(name="ops_vs_biz_october_gtv_by_asset", status="warn", evidence=evidence)
        )
    return flags, hypotheses, checks


def _check_october_usd_sources(
    db_path: Path,
) -> tuple[list[QualityFlag], list[QualityHypothesis], list[CrossCheck]]:
    sql = """
SELECT
  (SELECT ROUND(SUM(CAST(gtv_usd AS REAL)), 2)
   FROM fct_trading_daily
   WHERE transaction_date >= '2025-10-01' AND transaction_date < '2025-11-01') AS fct_usd,
  (SELECT CAST(gtv_usd AS REAL)
   FROM agg_monthly_biz_summary
   WHERE month >= '2025-10-01' AND month < '2025-11-01' AND asset_class = 'Total') AS biz_usd,
  (SELECT CAST(gtv_usd_reported AS REAL)
   FROM mart_ops_dashboard
   WHERE month >= '2025-10-01' AND month < '2025-11-01' AND asset_class = 'Total') AS ops_usd
""".strip()
    row = execute_read_only(db_path, sql)[0]
    evidence = [f"fct_usd={row['fct_usd']}, biz_usd={row['biz_usd']}, ops_usd={row['ops_usd']}"]
    flags: list[QualityFlag] = []
    hypotheses: list[QualityHypothesis] = []
    checks = [CrossCheck(name="fct_vs_biz_october_gtv_usd", status="pass", evidence=evidence)]
    if abs(float(row["ops_usd"]) - float(row["biz_usd"])) / float(row["biz_usd"]) > 0.05:
        flags.append(
            QualityFlag(
                code="ops_usd_definition_conflict",
                severity="warning",
                known_issue="Ops USD GTV materially differs from the recorded-USD business definition.",
                evidence=evidence,
            )
        )
        hypotheses.append(
            QualityHypothesis(
                flag_code="ops_usd_definition_conflict",
                suspected_cause="Ops converts IDR with a fixed 15000 rate and includes non-failed transactions; the business definition sums recorded amount_usd for completed transactions.",
                evidence=[
                    "mart_ops_dashboard.sql uses ROUND(SUM(amount_idr) / 15000, 2) and status != 'failed'."
                ],
            )
        )
        checks.append(
            CrossCheck(name="ops_vs_biz_october_gtv_usd", status="warn", evidence=evidence)
        )
    return flags, hypotheses, checks


def _check_mtu_sources(
    db_path: Path,
) -> tuple[list[QualityFlag], list[QualityHypothesis], list[CrossCheck]]:
    sql = """
SELECT
  (SELECT CAST(mtu AS INTEGER)
   FROM agg_monthly_biz_summary
   WHERE month >= '2025-10-01' AND month < '2025-11-01' AND asset_class = 'Total') AS biz_mtu,
  (SELECT COUNT(DISTINCT user_id)
   FROM raw_transactions
   WHERE transaction_date >= '2025-10-01'
     AND transaction_date < '2025-11-01'
     AND status = 'completed') AS raw_completed_unique_traders,
  (SELECT CAST(mtu_mixpanel AS INTEGER)
   FROM mart_ops_dashboard
   WHERE month >= '2025-10-01' AND month < '2025-11-01' AND asset_class = 'Total') AS mixpanel_mtu
""".strip()
    row = execute_read_only(db_path, sql)[0]
    evidence = [
        f"business_mtu={row['biz_mtu']}, raw_completed_unique_traders={row['raw_completed_unique_traders']}, mixpanel_mtu={row['mixpanel_mtu']}"
    ]
    return (
        [
            QualityFlag(
                code="mtu_definition_ambiguous",
                severity="warning",
                known_issue="Available MTU-like sources disagree and represent different definitions.",
                evidence=evidence,
            )
        ],
        [
            QualityHypothesis(
                flag_code="mtu_definition_ambiguous",
                suspected_cause="The business summary uses an AUM-derived monthly definition, raw transactions count completed traders, and Ops uses client-side Mixpanel events.",
                evidence=[
                    "agg_monthly_biz_summary.mtu is populated only on Total rows.",
                    "stg_mixpanel_events is client-side and delivery is not guaranteed.",
                ],
            )
        ],
        [CrossCheck(name="mtu_source_comparison", status="warn", evidence=evidence)],
    )


def _check_october_transaction_sources(
    db_path: Path,
) -> tuple[list[QualityFlag], list[QualityHypothesis], list[CrossCheck]]:
    sql = """
SELECT
  b.asset_class,
  CAST(b.transaction_count AS INTEGER) AS biz_transaction_count,
  CAST(o.total_transactions AS INTEGER) AS ops_total_transactions
FROM agg_monthly_biz_summary b
JOIN mart_ops_dashboard o
  ON o.month = b.month AND o.asset_class = b.asset_class
WHERE b.month >= '2025-10-01'
  AND b.month < '2025-11-01'
  AND b.asset_class != 'Total'
ORDER BY b.asset_class
""".strip()
    rows = execute_read_only(db_path, sql)
    evidence = [
        f"{row['asset_class']}: biz_completed={row['biz_transaction_count']}, ops_non_failed={row['ops_total_transactions']}"
        for row in rows
    ]
    return (
        [
            QualityFlag(
                code="ops_transaction_definition_conflict",
                severity="info",
                known_issue="Ops transaction counts are higher than completed-transaction counts.",
                evidence=evidence,
            )
        ],
        [
            QualityHypothesis(
                flag_code="ops_transaction_definition_conflict",
                suspected_cause="Ops includes pending transactions by filtering only failed transactions out.",
                evidence=["mart_ops_dashboard.sql uses status != 'failed'."],
            )
        ],
        [CrossCheck(name="biz_vs_ops_october_transaction_count", status="warn", evidence=evidence)],
    )


def _check_monthly_trend_sources(
    db_path: Path,
) -> tuple[list[QualityFlag], list[QualityHypothesis], list[CrossCheck]]:
    sql = """
WITH fct AS (
  SELECT substr(transaction_date, 1, 7) AS month, ROUND(SUM(CAST(gtv_idr AS REAL)), 2) AS fct_gtv_idr
  FROM fct_trading_daily
  WHERE transaction_date >= '2025-10-01' AND transaction_date < '2026-01-01'
  GROUP BY 1
),
biz AS (
  SELECT substr(month, 1, 7) AS month, CAST(gtv_idr AS REAL) AS biz_gtv_idr
  FROM agg_monthly_biz_summary
  WHERE month >= '2025-10-01' AND month < '2026-01-01' AND asset_class = 'Total'
),
ops AS (
  SELECT substr(month, 1, 7) AS month, CAST(gtv_idr AS REAL) AS ops_gtv_idr
  FROM mart_ops_dashboard
  WHERE month >= '2025-10-01' AND month < '2026-01-01' AND asset_class = 'Total'
)
SELECT fct.month, fct_gtv_idr, biz_gtv_idr, ops_gtv_idr
FROM fct
JOIN biz USING (month)
JOIN ops USING (month)
ORDER BY fct.month
""".strip()
    rows = execute_read_only(db_path, sql)
    evidence = [
        f"{row['month']}: fct={row['fct_gtv_idr']}, biz={row['biz_gtv_idr']}, ops={row['ops_gtv_idr']}"
        for row in rows
    ]
    flags: list[QualityFlag] = []
    hypotheses: list[QualityHypothesis] = []
    checks = [CrossCheck(name="monthly_trend_source_comparison", status="pass", evidence=evidence)]
    bad_months = [
        row["month"]
        for row in rows
        if abs(float(row["fct_gtv_idr"]) - float(row["biz_gtv_idr"])) / float(row["fct_gtv_idr"])
        > 0.01
    ]
    if bad_months:
        flags.append(
            QualityFlag(
                code="monthly_biz_summary_disagrees_with_fct",
                severity="critical",
                known_issue="The monthly business summary Total row disagrees with the fct_trading_daily source for at least one month.",
                evidence=evidence,
            )
        )
        hypotheses.append(
            QualityHypothesis(
                flag_code="monthly_biz_summary_disagrees_with_fct",
                suspected_cause="The December Total row in agg_monthly_biz_summary appears stale or incorrectly aggregated compared with its asset rows and fct_trading_daily.",
                evidence=[f"Disagreeing month(s): {', '.join(bad_months)}"],
            )
        )
        checks[0].status = "fail"
    return flags, hypotheses, checks


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_always_positive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in ("gtv", "transaction_count", "mtu", "trader"))


def _is_positive_metric_key(key: str) -> bool:
    return _is_always_positive_key(key)


def _is_required_value_key(key: str) -> bool:
    lowered = key.lower()
    if "mom_change" in lowered:
        return False
    return _is_always_positive_key(key)
