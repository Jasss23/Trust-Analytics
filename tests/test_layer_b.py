"""Generic Layer B reproduces every known cross-source finding from R0.

This is the R3.0 validation gate — before deleting any handcoded reconciliation
logic, we must prove the new metrics.yml-driven Layer B catches the same
findings on real data:

- Q1 (GTV by asset): Ops disagrees with fct_trading_daily by ~13% due to
  status != 'failed' filter difference.
- Q2 (Total USD GTV): Ops disagrees by ~21% due to fixed 15K IDR/USD rate.
- Q3 (MTU): cross_source disabled (definitional difference, not data quality).
- Q4 (Tx count by asset): Ops disagrees by ~10-15%.
- Q5 (GTV trend Oct-Dec): biz_summary December Total row stale by ~3.5%.

All tests skip when case CSVs are not local.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pluang_agent.data_loader import load_csvs
from pluang_agent.layer_b import run_layer_b
from pluang_agent.metrics import load_metrics_registry
from pluang_agent.models import SourceProvenance, SQLAgentAnswer
from pluang_agent.questions import get_question

CASE_DATA_DIR = Path(
    "/Users/jiashunpang/projects/ai/pluang/docs/pluang_analytics_agent/data"
)


@pytest.fixture(scope="module")
def real_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not CASE_DATA_DIR.exists():
        pytest.skip("Case study CSVs are not available locally.")
    db_path = tmp_path_factory.mktemp("real") / "pluang.sqlite"
    load_csvs(CASE_DATA_DIR, db_path)
    return db_path


@pytest.fixture(scope="module")
def registry():
    return load_metrics_registry()


def _stub_answer(question_id: str) -> SQLAgentAnswer:
    """Minimal answer envelope — Layer B only reads question_id and (optionally) source."""
    q = get_question(question_id)
    return SQLAgentAnswer(
        question_id=q.id,
        question=q.text,
        metric_name=q.metric,
        metric_value=None,
        period=q.period,
        source=SourceProvenance(
            primary_table="fct_trading_daily",
            why_chosen="stub",
            alternatives_available=[],
        ),
        sql="",
        logic="stub",
        result_rows=[],
    )


def test_q1_layer_b_flags_ops_disagreement(real_db: Path, registry) -> None:
    answer = _stub_answer("q1_gtv_idr_by_asset_oct_2025")
    report = run_layer_b(real_db, answer, registry, llm_client=None)

    assert report.verdict == "DISAGREEMENT"
    assert len(report.cross_source_findings) == 3  # primary + 2 alternatives
    # mart_ops_dashboard should be the disagreeing source
    ops_finding = next(
        f for f in report.cross_source_findings if "mart_ops_dashboard" in f.source
    )
    assert ops_finding.delta_vs_primary is not None
    assert ops_finding.delta_vs_primary > 5.0, "Ops should disagree by >5% per status filter"
    # biz_summary should agree exactly (sourced from fct)
    biz_finding = next(
        f for f in report.cross_source_findings if "agg_monthly_biz_summary" in f.source
    )
    assert biz_finding.delta_vs_primary is not None
    assert biz_finding.delta_vs_primary < 0.1, "biz should match fct since it sources from fct"


def test_q2_layer_b_flags_ops_usd_disagreement(real_db: Path, registry) -> None:
    answer = _stub_answer("q2_gtv_usd_oct_2025")
    report = run_layer_b(real_db, answer, registry, llm_client=None)

    assert report.verdict == "DISAGREEMENT"
    ops_finding = next(
        f for f in report.cross_source_findings if "mart_ops_dashboard" in f.source
    )
    assert ops_finding.delta_vs_primary is not None
    assert ops_finding.delta_vs_primary > 10.0, (
        "Ops USD should disagree materially per fixed 15K IDR/USD conversion"
    )


def test_q3_layer_b_disabled_for_mtu_definitional_difference(
    real_db: Path, registry
) -> None:
    answer = _stub_answer("q3_mtu_oct_2025")
    report = run_layer_b(real_db, answer, registry, llm_client=None)

    assert report.verdict == "NOT_APPLICABLE"
    assert report.hypothesis_absence_note is not None
    assert "definition" in report.hypothesis_absence_note.lower()


def test_q4_layer_b_flags_ops_transaction_count_disagreement(
    real_db: Path, registry
) -> None:
    answer = _stub_answer("q4_transaction_count_by_asset_oct_2025")
    report = run_layer_b(real_db, answer, registry, llm_client=None)

    assert report.verdict == "DISAGREEMENT"
    ops_finding = next(
        f for f in report.cross_source_findings if "mart_ops_dashboard" in f.source
    )
    assert ops_finding.delta_vs_primary is not None
    assert ops_finding.delta_vs_primary > 5.0


def test_q5_layer_b_catches_december_stale_total_row(real_db: Path, registry) -> None:
    answer = _stub_answer("q5_gtv_mom_trend_oct_dec_2025")
    report = run_layer_b(real_db, answer, registry, llm_client=None)

    assert report.verdict == "DISAGREEMENT"
    biz_finding = next(
        f for f in report.cross_source_findings if "agg_monthly_biz_summary" in f.source
    )
    assert biz_finding.delta_vs_primary is not None
    # December stale Total row creates a ~3.5% delta on at least one month
    assert biz_finding.delta_vs_primary > 1.0


def test_layer_b_no_llm_returns_findings_with_absence_note(
    real_db: Path, registry
) -> None:
    """When llm_client is None, hypothesis is absent but findings still populated."""
    answer = _stub_answer("q1_gtv_idr_by_asset_oct_2025")
    report = run_layer_b(real_db, answer, registry, llm_client=None)

    assert report.hypothesis is None
    assert report.hypothesis_absence_note is not None
    assert "LLM" in report.hypothesis_absence_note or "rule-based" in report.hypothesis_absence_note
    # But findings are still there — rules ran successfully
    assert report.cross_source_findings
