from __future__ import annotations

from pathlib import Path

import pytest

from pluang_agent.agents.quality_agent import QualityAgent
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


def test_quality_agent_flags_mtu_ambiguity(real_db: Path) -> None:
    answer = answer_with_baseline(real_db, get_question("q3_mtu_oct_2025"))
    report = QualityAgent(real_db).assess(answer)

    assert any(flag.code == "mtu_definition_ambiguous" for flag in report.flags)
    assert report.hypotheses


def test_quality_agent_flags_december_monthly_summary_disagreement(real_db: Path) -> None:
    answer = answer_with_baseline(real_db, get_question("q5_gtv_mom_trend_oct_dec_2025"))
    report = QualityAgent(real_db).assess(answer)

    assert any(flag.code == "monthly_biz_summary_disagrees_with_fct" for flag in report.flags)
    assert not any(flag.code == "null_required_value" for flag in report.flags)
    assert any(check.status == "fail" for check in report.cross_checks)
