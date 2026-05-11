from __future__ import annotations

from pathlib import Path

import pytest

from pluang_agent.metadata import case_root_from_data_dir, load_dbt_metadata
from pluang_agent.metrics import load_metrics_registry
from pluang_agent.models import BusinessQuestion
from pluang_agent.planner import plan_question, validate_question_plan
from pluang_agent.questions import get_question

CASE_DATA_DIR = Path("/Users/jiashunpang/projects/ai/pluang/docs/pluang_analytics_agent/data")


@pytest.fixture(scope="module")
def metadata():
    if not CASE_DATA_DIR.exists():
        pytest.skip("Case study files are not available locally.")
    return load_dbt_metadata(case_root_from_data_dir(CASE_DATA_DIR))


@pytest.fixture(scope="module")
def registry():
    return load_metrics_registry()


def test_mtu_plan_requires_all_definitions(metadata, registry) -> None:
    result = plan_question(get_question("q3_mtu_oct_2025"), registry, metadata)

    assert result.system_error is None
    assert result.plan is not None
    assert result.plan.answer_shape == "multi_definition"
    assert result.plan.required_definitions == [
        "aum_defined_mtu",
        "raw_completed_unique_traders",
        "mixpanel_mtu",
    ]
    assert validate_question_plan(result.plan, get_question("q3_mtu_oct_2025"), metadata, registry) == []


def test_mom_trend_plan_requires_period_over_period_columns(metadata, registry) -> None:
    result = plan_question(get_question("q5_gtv_mom_trend_oct_dec_2025"), registry, metadata)

    assert result.system_error is None
    assert result.plan is not None
    assert result.plan.answer_shape == "period_over_period"
    assert "mom_change_idr" in result.plan.required_output_columns
    assert "mom_change_pct" in result.plan.required_output_columns


def test_extra_ops_breakdown_comparison_excludes_total(metadata, registry) -> None:
    question = BusinessQuestion(
        id="extra_ops_gtv_idr_by_asset_oct_2025",
        text=(
            "Using the Ops dashboard definition, what was total GTV (IDR) by asset "
            "class in October 2025, and how does it compare to the canonical source?"
        ),
        metric="ops_gtv_idr_by_asset_class",
        period="October 2025",
    )

    result = plan_question(question, registry, metadata)

    assert result.system_error is None
    assert result.plan is not None
    assert result.plan.answer_shape == "breakdown_comparison"
    assert result.plan.primary_source.table == "mart_ops_dashboard"
    assert result.plan.comparison_sources[0].table == "fct_trading_daily"
    assert result.plan.breakdown is not None
    assert result.plan.breakdown.exclude_aggregate_members == ["Total"]
