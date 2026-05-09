from __future__ import annotations

from pathlib import Path

import pytest

from pluang_agent.baseline import answer_with_baseline
from pluang_agent.data_loader import load_csvs
from pluang_agent.questions import get_question

CASE_DATA_DIR = Path("/Users/jiashunpang/Downloads/pluang_analytics_agent/data")


@pytest.fixture()
def real_db(tmp_path: Path) -> Path:
    if not CASE_DATA_DIR.exists():
        pytest.skip("Case study CSVs are not available locally.")
    db_path = tmp_path / "pluang.sqlite"
    load_csvs(CASE_DATA_DIR, db_path)
    return db_path


def test_october_gtv_usd_baseline(real_db: Path) -> None:
    answer = answer_with_baseline(real_db, get_question("q2_gtv_usd_oct_2025"))

    assert answer.metric_value == pytest.approx(2339863.74)


def test_october_mtu_baseline_surfaces_ambiguity(real_db: Path) -> None:
    answer = answer_with_baseline(real_db, get_question("q3_mtu_oct_2025"))

    assert answer.metric_value["primary_mtu_aum_defined"] == 12453
    assert answer.metric_value["raw_completed_unique_traders"] == 9239
    assert answer.metric_value["mixpanel_mtu"] == 8882
    assert answer.ambiguity_notes


def test_monthly_trend_uses_fct_completed_totals(real_db: Path) -> None:
    answer = answer_with_baseline(real_db, get_question("q5_gtv_mom_trend_oct_dec_2025"))

    assert [row["month"] for row in answer.result_rows] == ["2025-10", "2025-11", "2025-12"]
    assert answer.result_rows[2]["gtv_idr"] == pytest.approx(38604717992)
