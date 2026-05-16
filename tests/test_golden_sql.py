"""Golden SQL fixtures — numeric correctness on the real CSVs.

Replaces tests/test_baseline_real_data.py. The function lives in
tests/_fixtures/golden_sql.py now; the product path no longer falls back
to it. These tests run only when the case CSVs are available locally.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._fixtures.golden_sql import golden_answer
from trust_analytics.data_loader import load_csvs
from trust_analytics.questions import get_question

CASE_DATA_DIR = Path("demo_data/fintech_analytics/data")


@pytest.fixture()
def real_db(tmp_path: Path) -> Path:
    if not CASE_DATA_DIR.exists():
        pytest.skip("Case study CSVs are not available locally.")
    db_path = tmp_path / "trust_analytics.sqlite"
    load_csvs(CASE_DATA_DIR, db_path)
    return db_path


def test_october_gtv_usd(real_db: Path) -> None:
    answer = golden_answer(real_db, get_question("q2_gtv_usd_oct_2025"))

    assert answer.metric_value == pytest.approx(2339863.74)
    assert answer.source is not None
    assert answer.source.primary_table == "fct_trading_daily"


def test_october_mtu_surfaces_three_definitions(real_db: Path) -> None:
    answer = golden_answer(real_db, get_question("q3_mtu_oct_2025"))

    assert answer.metric_value["primary_mtu_aum_defined"] == 12453
    assert answer.metric_value["raw_completed_unique_traders"] == 9239
    assert answer.metric_value["mixpanel_mtu"] == 8882
    # Per the spec's interpretation_choices contract.
    assert answer.interpretation_choices, "MTU should always declare its choice + alternatives"


def test_monthly_trend_uses_fct_completed_totals(real_db: Path) -> None:
    answer = golden_answer(real_db, get_question("q5_gtv_mom_trend_oct_dec_2025"))

    assert [row["month"] for row in answer.result_rows] == ["2025-10", "2025-11", "2025-12"]
    assert answer.result_rows[2]["gtv_idr"] == pytest.approx(38604717992)
    assert answer.source is not None
    assert "stale" in answer.source.why_chosen.lower()
