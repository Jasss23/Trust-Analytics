from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pluang_agent.agents.sql_agent import SQLAgent
from pluang_agent.llm import LLMResponse
from pluang_agent.metadata import DbtMetadata
from pluang_agent.metrics import MetricsRegistry
from pluang_agent.models import (
    BusinessQuestion,
    PlanBreakdown,
    PlanPeriod,
    PlanSource,
    QuestionPlan,
    SourceProvenance,
    SQLAgentAnswer,
    UsageRecord,
)
from pluang_agent.pre_flight import pre_flight_check
from pluang_agent.quality_rules import run_layer_a
from pluang_agent.sql_attempt import run_one_attempt


def _question() -> BusinessQuestion:
    return BusinessQuestion(
        id="extra_q",
        text="Using Ops dashboard, what was GTV by asset class in October 2025?",
        metric="gtv_idr",
        period="October 2025",
    )


def _plan() -> QuestionPlan:
    return QuestionPlan(
        question_id="extra_q",
        metric_intent="gtv_idr",
        period=PlanPeriod(start="2025-10-01", end="2025-11-01"),
        answer_shape="breakdown",
        primary_source=PlanSource(
            table="mart_ops_dashboard",
            column="gtv_idr",
            period_column="month",
            reason="User requested Ops dashboard.",
        ),
        breakdown=PlanBreakdown(
            dimension="asset_class",
            exclude_aggregate_members=["Total"],
        ),
        required_output_columns=["asset_class", "gtv_idr"],
    )


def _answer(rows: list[dict]) -> SQLAgentAnswer:
    return SQLAgentAnswer(
        question_id="extra_q",
        question=_question().text,
        metric_name="gtv_idr",
        metric_value=rows,
        period="October 2025",
        source=SourceProvenance(
            primary_table="mart_ops_dashboard",
            why_chosen="ops",
            alternatives_available=[],
        ),
        sql="SELECT asset_class, gtv_idr FROM mart_ops_dashboard",
        filters=[],
        assumptions=[],
        logic="test",
        result_rows=rows,
    )


def test_preflight_rejects_aggregate_member_in_breakdown() -> None:
    answer = _answer([
        {"asset_class": "Total", "gtv_idr": 100},
        {"asset_class": "crypto", "gtv_idr": 60},
    ])

    result = pre_flight_check(
        answer,
        _question(),
        MetricsRegistry(),
        question_plan=_plan(),
    )

    assert result.passed is False
    assert result.issue == "aggregate_member_in_breakdown"


def test_preflight_rejects_missing_required_columns_for_shape() -> None:
    answer = _answer([{"asset_class": "crypto", "total_gtv_idr": 60}])

    result = pre_flight_check(
        answer,
        _question(),
        MetricsRegistry(),
        question_plan=_plan(),
    )

    assert result.passed is False
    assert result.issue == "required_columns_missing"


def test_preflight_rejects_missing_multi_definition_values() -> None:
    plan = QuestionPlan(
        question_id="extra_q",
        metric_intent="monthly_transacting_users",
        period=PlanPeriod(start="2025-10-01", end="2025-11-01"),
        answer_shape="multi_definition",
        primary_source=PlanSource(
            table="agg_monthly_biz_summary",
            column="mtu",
            period_column="month",
            extra_filters=["asset_class = 'Total'"],
            aggregator="RAW",
            reason="canonical MTU definition",
        ),
        required_output_columns=[
            "aum_defined_mtu",
            "raw_completed_unique_traders",
            "mixpanel_mtu",
        ],
        required_definitions=[
            "aum_defined_mtu",
            "raw_completed_unique_traders",
            "mixpanel_mtu",
        ],
    )
    answer = SQLAgentAnswer(
        question_id="extra_q",
        question="How many MTU were there in October 2025?",
        metric_name="monthly_transacting_users",
        metric_value=[{"aum_defined_mtu": 12453}],
        period="October 2025",
        source=SourceProvenance(
            primary_table="agg_monthly_biz_summary",
            why_chosen="canonical",
            alternatives_available=[],
        ),
        sql="SELECT mtu AS aum_defined_mtu FROM agg_monthly_biz_summary",
        filters=[],
        assumptions=[],
        logic="test",
        result_rows=[{"aum_defined_mtu": 12453}],
    )

    result = pre_flight_check(
        answer,
        _question(),
        MetricsRegistry(),
        question_plan=plan,
    )

    assert result.passed is False
    assert result.issue == "required_columns_missing"


def test_preflight_rejects_integer_division_mom_pct() -> None:
    plan = QuestionPlan(
        question_id="extra_q",
        metric_intent="gtv_idr_month_on_month_trend",
        period=PlanPeriod(start="2025-10-01", end="2026-01-01"),
        answer_shape="period_over_period",
        primary_source=PlanSource(
            table="fct_trading_daily",
            column="gtv_idr",
            period_column="transaction_date",
            reason="canonical",
        ),
        required_output_columns=["month", "gtv_idr", "mom_change_idr", "mom_change_pct"],
    )
    answer = SQLAgentAnswer(
        question_id="extra_q",
        question="What was the month-on-month GTV trend?",
        metric_name="gtv_idr_month_on_month_trend",
        metric_value=[
            {"month": "2025-10", "gtv_idr": 100, "mom_change_idr": None, "mom_change_pct": None},
            {"month": "2025-11", "gtv_idr": 101, "mom_change_idr": 1, "mom_change_pct": 0},
        ],
        period="October to November 2025",
        source=SourceProvenance(
            primary_table="fct_trading_daily",
            why_chosen="canonical",
            alternatives_available=[],
        ),
        sql="SELECT month, gtv_idr, mom_change_idr, mom_change_pct FROM fct_trading_daily",
        filters=[],
        assumptions=[],
        logic="test",
        result_rows=[
            {"month": "2025-10", "gtv_idr": 100, "mom_change_idr": None, "mom_change_pct": None},
            {"month": "2025-11", "gtv_idr": 101, "mom_change_idr": 1, "mom_change_pct": 0},
        ],
    )

    result = pre_flight_check(
        answer,
        _question(),
        MetricsRegistry(),
        question_plan=plan,
    )

    assert result.passed is False
    assert result.issue == "period_over_period_pct_zero"


def test_layer_a_flags_metric_value_not_derived_from_result_rows() -> None:
    answer = _answer([{"asset_class": "crypto", "gtv_idr": 60}])
    answer.metric_value = [{"asset_class": "crypto", "gtv_idr": 999}]

    report = run_layer_a(answer, question_plan=_plan())

    check = next(c for c in report.checks if c.name == "metric_value_matches_result_rows")
    assert check.result == "FAIL"


class _StubReturns:
    available = True

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "question_id": "extra_q",
                    "question": _question().text,
                    "metric_name": "gtv_idr",
                    "metric_value": [{"stale": 999}],
                    "period": "October 2025",
                    "source": {
                        "primary_table": "mart_ops_dashboard",
                        "why_chosen": "ops",
                        "alternatives_available": [],
                    },
                    "sql": (
                        "SELECT asset_class, CAST(gtv_idr AS INTEGER) AS gtv_idr "
                        "FROM mart_ops_dashboard WHERE month >= '2025-10-01' "
                        "AND month < '2025-11-01' AND asset_class != 'Total'"
                    ),
                    "filters": [],
                    "assumptions": [],
                    "logic": "test",
                    "result_rows": [],
                    "interpretation_choices": [],
                    "dq_notes": [],
                    "warnings": [],
                }
            ),
            usage=UsageRecord(),
        )


def test_run_one_attempt_overwrites_llm_metric_value_with_executed_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE mart_ops_dashboard (month TEXT, asset_class TEXT, gtv_idr TEXT)")
    conn.execute("INSERT INTO mart_ops_dashboard VALUES ('2025-10-01', 'crypto', '60')")
    conn.execute("INSERT INTO mart_ops_dashboard VALUES ('2025-10-01', 'Total', '60')")
    conn.commit()
    conn.close()

    agent = SQLAgent(
        db_path=db_path,
        metadata=DbtMetadata(sources={}, models={}),
        llm_client=_StubReturns(),  # type: ignore[arg-type]
    )
    outcome = run_one_attempt(
        agent,
        _question(),
        db_path,
        MetricsRegistry(),
        question_plan=_plan(),
    )

    assert outcome.status == "success"
    assert outcome.answer.metric_value == outcome.answer.result_rows
    assert outcome.answer.metric_value == [{"asset_class": "crypto", "gtv_idr": 60}]
