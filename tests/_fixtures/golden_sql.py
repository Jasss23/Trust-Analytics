"""Golden SQL answers for the 5 required questions.

Hand-written deterministic SQL whose numerical output we know is correct.
Lives in test fixtures only — used to validate that LLM-generated SQL produces
the same numbers in R4 spot-checks. Not part of the product path.

Relocated from src/trust_analytics/baseline.py at R1 per the locked decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trust_analytics.models import (
    BusinessQuestion,
    InterpretationChoice,
    SourceProvenance,
    SQLAgentAnswer,
)
from trust_analytics.sql_runner import execute_read_only


def golden_answer(db_path: Path, question: BusinessQuestion) -> SQLAgentAnswer:
    builders = {
        "q1_gtv_idr_by_asset_oct_2025": _gtv_idr_by_asset,
        "q2_gtv_usd_oct_2025": _gtv_usd_total,
        "q3_mtu_oct_2025": _mtu_october,
        "q4_transaction_count_by_asset_oct_2025": _transaction_count_by_asset,
        "q5_gtv_mom_trend_oct_dec_2025": _gtv_mom_trend,
    }
    try:
        return builders[question.id](db_path, question)
    except KeyError as exc:
        raise ValueError(f"No golden SQL is defined for {question.id}") from exc


def _gtv_idr_by_asset(db_path: Path, question: BusinessQuestion) -> SQLAgentAnswer:
    sql = """
SELECT
  asset_class,
  ROUND(SUM(CAST(gtv_idr AS REAL)), 2) AS gtv_idr
FROM fct_trading_daily
WHERE transaction_date >= '2025-10-01'
  AND transaction_date < '2025-11-01'
GROUP BY asset_class
ORDER BY asset_class
""".strip()
    rows = execute_read_only(db_path, sql)
    return _answer(
        question,
        metric_value={row["asset_class"]: row["gtv_idr"] for row in rows},
        primary_table="fct_trading_daily",
        why_chosen="Canonical completed-transaction source per dbt metadata.",
        alternatives_available=["agg_monthly_biz_summary", "mart_ops_dashboard"],
        filters=["transaction_date in October 2025", "completed transactions only"],
        assumptions=[
            "Use the dbt daily trading mart as the canonical completed-transaction source.",
        ],
        logic="Sum gtv_idr across October 2025 by asset_class.",
        sql=sql,
        result_rows=rows,
    )


def _gtv_usd_total(db_path: Path, question: BusinessQuestion) -> SQLAgentAnswer:
    sql = """
SELECT
  ROUND(SUM(CAST(gtv_usd AS REAL)), 2) AS total_gtv_usd
FROM fct_trading_daily
WHERE transaction_date >= '2025-10-01'
  AND transaction_date < '2025-11-01'
""".strip()
    rows = execute_read_only(db_path, sql)
    return _answer(
        question,
        metric_value=rows[0]["total_gtv_usd"],
        primary_table="fct_trading_daily",
        why_chosen="Recorded USD on completed transactions, not an IDR conversion.",
        alternatives_available=["agg_monthly_biz_summary", "mart_ops_dashboard"],
        filters=["transaction_date in October 2025", "completed transactions only"],
        assumptions=[
            "Use recorded USD transaction values, not a conversion from IDR.",
            "Use completed transactions only.",
        ],
        logic="Sum gtv_usd across all asset classes in October 2025.",
        sql=sql,
        result_rows=rows,
    )


def _mtu_october(db_path: Path, question: BusinessQuestion) -> SQLAgentAnswer:
    sql = """
SELECT
  (SELECT CAST(mtu AS INTEGER)
   FROM agg_monthly_biz_summary
   WHERE month >= '2025-10-01' AND month < '2025-11-01' AND asset_class = 'Total') AS aum_defined_mtu,
  (SELECT COUNT(DISTINCT user_id)
   FROM raw_transactions
   WHERE transaction_date >= '2025-10-01'
     AND transaction_date < '2025-11-01'
     AND status = 'completed') AS raw_completed_unique_traders,
  (SELECT CAST(mtu_mixpanel AS INTEGER)
   FROM mart_ops_dashboard
   WHERE month >= '2025-10-01' AND month < '2025-11-01' AND asset_class = 'Total') AS mixpanel_mtu
""".strip()
    rows = execute_read_only(db_path, sql)
    row = rows[0]
    return _answer(
        question,
        metric_value={
            "primary_mtu_aum_defined": row["aum_defined_mtu"],
            "raw_completed_unique_traders": row["raw_completed_unique_traders"],
            "mixpanel_mtu": row["mixpanel_mtu"],
        },
        primary_table="agg_monthly_biz_summary",
        why_chosen="Business-summary MTU on Total row is the dashboard-ready definition.",
        alternatives_available=["raw_transactions", "mart_ops_dashboard"],
        filters=["month = October 2025", "Total row for monthly marts"],
        assumptions=[
            "Primary MTU uses the business summary definition populated on the Total row.",
            "Raw distinct completed traders and Mixpanel MTU are shown as alternate definitions.",
        ],
        logic="Read business-summary MTU and compare it to raw completed traders and Ops/Mixpanel MTU.",
        sql=sql,
        result_rows=rows,
        interpretation_choices=[
            InterpretationChoice(
                choice="Primary = business-summary MTU (AUM-derived)",
                alternatives=["raw distinct completed traders", "Mixpanel MTU (client-side)"],
                rationale=(
                    "All three are valid definitions. Business-summary MTU is the canonical "
                    "monthly definition; raw traders count completed actors; Mixpanel measures "
                    "client-side activity and is not delivery-guaranteed."
                ),
            )
        ],
    )


def _transaction_count_by_asset(db_path: Path, question: BusinessQuestion) -> SQLAgentAnswer:
    sql = """
SELECT
  asset_class,
  SUM(CAST(transaction_count AS INTEGER)) AS transaction_count
FROM fct_trading_daily
WHERE transaction_date >= '2025-10-01'
  AND transaction_date < '2025-11-01'
GROUP BY asset_class
ORDER BY transaction_count DESC
""".strip()
    rows = execute_read_only(db_path, sql)
    return _answer(
        question,
        metric_value={row["asset_class"]: row["transaction_count"] for row in rows},
        primary_table="fct_trading_daily",
        why_chosen="Completed-transaction counts on the daily fact table.",
        alternatives_available=["mart_ops_dashboard"],
        filters=["transaction_date in October 2025", "completed transactions only"],
        assumptions=["Use completed transaction counts from the dbt daily trading mart."],
        logic="Sum transaction_count across October 2025 by asset_class and rank descending.",
        sql=sql,
        result_rows=rows,
    )


def _gtv_mom_trend(db_path: Path, question: BusinessQuestion) -> SQLAgentAnswer:
    sql = """
WITH monthly AS (
  SELECT
    substr(transaction_date, 1, 7) AS month,
    SUM(CAST(gtv_idr AS REAL)) AS gtv_idr
  FROM fct_trading_daily
  WHERE transaction_date >= '2025-10-01'
    AND transaction_date < '2026-01-01'
  GROUP BY 1
)
SELECT
  month,
  ROUND(gtv_idr, 2) AS gtv_idr,
  ROUND(gtv_idr - LAG(gtv_idr) OVER (ORDER BY month), 2) AS mom_change_idr,
  ROUND(
    100.0 * (gtv_idr - LAG(gtv_idr) OVER (ORDER BY month))
    / LAG(gtv_idr) OVER (ORDER BY month),
    2
  ) AS mom_change_pct
FROM monthly
ORDER BY month
""".strip()
    rows = execute_read_only(db_path, sql)
    return _answer(
        question,
        metric_value=rows,
        primary_table="fct_trading_daily",
        why_chosen=(
            "fct_trading_daily is the canonical completed-transaction source. The "
            "agg_monthly_biz_summary Total row is known stale for December 2025."
        ),
        alternatives_available=["agg_monthly_biz_summary"],
        filters=[
            "transaction_date from 2025-10-01 through 2025-12-31",
            "completed transactions only",
        ],
        assumptions=["Use IDR GTV as the trend metric unless currency is otherwise specified."],
        logic="Aggregate completed-transaction GTV by month and calculate month-on-month change.",
        sql=sql,
        result_rows=rows,
    )


def _answer(
    question: BusinessQuestion,
    metric_value: Any,
    primary_table: str,
    why_chosen: str,
    alternatives_available: list[str],
    filters: list[str],
    assumptions: list[str],
    logic: str,
    sql: str,
    result_rows: list[dict[str, Any]],
    interpretation_choices: list[InterpretationChoice] | None = None,
) -> SQLAgentAnswer:
    return SQLAgentAnswer(
        question_id=question.id,
        question=question.text,
        metric_name=question.metric,
        metric_value=metric_value,
        period=question.period,
        source=SourceProvenance(
            primary_table=primary_table,
            why_chosen=why_chosen,
            alternatives_available=alternatives_available,
        ),
        sql=sql,
        filters=filters,
        assumptions=assumptions,
        logic=logic,
        result_rows=result_rows,
        interpretation_choices=interpretation_choices or [],
        dq_notes=[],
    )
