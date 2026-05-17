from __future__ import annotations

import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep

from fastapi.testclient import TestClient

from trust_analytics import api as api_module
from trust_analytics.api import app
from trust_analytics.portal import AUDIT_ID, HERO_ID, MTU_ID
from trust_analytics.telemetry import record_event

client = TestClient(app)


def test_cached_analysis_projection_has_business_language() -> None:
    response = client.get(f"/api/analysis/{HERO_ID}")
    assert response.status_code == 200
    data = response.json()

    assert data["status"]["label"] == "Use with context"
    assert "Crypto is the clearest October growth priority" in data["headline"]
    assert data["chart"]["type"] == "bar"
    assert data["rows"]
    assert data["decisionPack"]["title"] == "Asset-class growth priority pack"
    assert "Leadership recommendation" in data["useThisFor"]
    assert "pending transactions" in data["sourceCaveat"]


def test_csv_export_contains_displayed_rows() -> None:
    response = client.get(f"/api/analysis/{HERO_ID}/export.csv")
    assert response.status_code == 200
    text = response.text

    assert "asset_class,gtv_idr" in text
    assert "crypto" in text


def test_pptx_export_is_real_deck() -> None:
    response = client.get(f"/api/analysis/{HERO_ID}/deck.pptx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )

    with zipfile.ZipFile(BytesIO(response.content)) as deck:
        names = set(deck.namelist())
    assert "ppt/presentation.xml" in names
    assert "ppt/slides/slide1.xml" in names
    assert "ppt/slides/slide2.xml" in names


def test_email_draft_is_copyable_professional_text() -> None:
    response = client.post(f"/api/analysis/{HERO_ID}/email-draft")
    assert response.status_code == 200
    data = response.json()

    assert "Decision pack ready" in data["subject"]
    assert "Trust Analytics" in data["body"]
    assert "Workflow status: Use with context" in data["body"]
    assert "source" in data["body"].lower()


def test_question_shape_matches_clear_asset_class_question() -> None:
    response = client.post(
        "/api/question/shape",
        json={
            "question_text": "Which asset class should we prioritise for growth in October 2025?",
            "audience": "Leadership",
            "desired_output": "Decision pack",
        },
    )
    assert response.status_code == 200
    data = response.json()

    assert data["recommendedAnalysisId"] == HERO_ID
    assert data["inferredMetric"] == "GTV by asset class"
    assert data["fields"]["dimension"] == "Asset class"
    assert data["fieldStates"]["period"]["status"] in {"confirmed", "inferred"}
    assert data["quality"]["label"] in {"Ready to validate", "Needs confirmation"}


def test_question_shape_marks_audience_and_output_missing_when_not_supplied() -> None:
    response = client.post(
        "/api/question/shape",
        json={
            "question_text": "Which asset class should we prioritise for growth in October 2025?",
        },
    )
    assert response.status_code == 200
    data = response.json()

    assert data["fieldStates"]["audience"]["status"] == "missing"
    assert data["fieldStates"]["desiredOutput"]["status"] == "missing"
    assert data["fields"]["audience"] == ""
    assert data["fields"]["desiredOutput"] == ""
    assert data["quality"]["ready"] is False
    assert data["quality"]["score"] <= 67


def test_question_shape_flags_missing_period() -> None:
    response = client.post(
        "/api/question/shape",
        json={
            "question_text": "Which asset class should we prioritise for growth?",
            "audience": "Leadership",
            "desired_output": "Decision pack",
        },
    )
    assert response.status_code == 200
    data = response.json()

    assert data["recommendedAnalysisId"] == HERO_ID
    assert {"key": "period", "label": "Time period"} in data["missingFields"]
    assert data["quality"]["ready"] is False
    assert data["clarificationNeeds"]


def test_question_shape_flags_missing_dimension_for_mtu() -> None:
    response = client.post(
        "/api/question/shape",
        json={
            "question_text": "How many MTU did we have in October 2025?",
            "audience": "Leadership",
            "desired_output": "Decision pack",
        },
    )
    assert response.status_code == 200
    data = response.json()

    assert data["recommendedAnalysisId"] == MTU_ID
    assert {"key": "dimension", "label": "Comparison dimension"} in data["missingFields"]


def test_question_shape_matches_audit_trend_question() -> None:
    response = client.post(
        "/api/question/shape",
        json={
            "question_text": "Can we rely on the month-on-month GTV trend for quarter-end reporting?",
            "period": "October to December 2025",
            "audience": "Finance",
            "desired_output": "Audit brief",
        },
    )
    assert response.status_code == 200
    data = response.json()

    assert data["recommendedAnalysisId"] == AUDIT_ID
    assert data["verifiedPath"]["status"]["label"] == "Audit required"
    assert "trend reliability" in data["verifiedPath"]["reason"]


def test_library_lists_all_five_seed_questions() -> None:
    response = client.get("/api/library")
    assert response.status_code == 200
    data = response.json()

    seed_ids = {item["id"] for item in data if item["source"] == "seed"}
    assert {
        "q1_gtv_idr_by_asset_oct_2025",
        "q2_gtv_usd_oct_2025",
        "q3_mtu_oct_2025",
        "q4_transaction_count_by_asset_oct_2025",
        "q5_gtv_mom_trend_oct_dec_2025",
    } <= seed_ids


def test_library_includes_successful_ask_runs(tmp_path: Path, monkeypatch) -> None:
    ask_root = tmp_path / "ask"
    ask_case = ask_root / "adhoc_success"
    shutil.copytree(Path("outputs/sample"), ask_case)
    monkeypatch.setattr("trust_analytics.portal.ASK_RUNS_DIR", ask_root)

    response = client.get("/api/library")
    assert response.status_code == 200
    data = response.json()

    assert any(item["source"] == "ask" for item in data)


def test_run_session_blocks_for_inline_clarification() -> None:
    response = client.post(
        "/api/runs",
        json={
            "question_text": "Which asset class should we prioritise for growth?",
            "fields": {},
        },
    )
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "needs_clarification"
    assert {"key": "period", "label": "Time period"} in [
        {"key": item["key"], "label": item["label"]} for item in data["clarificationNeeds"]
    ]


def test_run_session_completes_with_packable_result(monkeypatch) -> None:
    def _fake_run_and_persist_analysis(**_kwargs):
        return {
            "id": "adhoc_validated",
            "status": {"label": "Ready to use", "tone": "ready"},
            "rows": [{"metric": "gtv_idr", "value": 1}],
            "analystEvidence": {"sql": "SELECT 1"},
        }

    monkeypatch.setattr(api_module, "run_and_persist_analysis", _fake_run_and_persist_analysis)
    response = client.post(
        "/api/runs",
        json={
            "question_text": "What was total GTV by asset class in October 2025?",
            "fields": {
                "period": "October 2025",
                "segment": "Completed trading activity",
                "dimension": "Asset class",
                "audience": "Leadership",
                "desiredOutput": "Decision pack",
                "businessObjective": "Prioritise the next growth focus",
            },
        },
    )
    assert response.status_code == 200
    run = response.json()

    deadline = monotonic() + 2
    while monotonic() < deadline:
        status = client.get(f"/api/runs/{run['id']}").json()
        if status["status"] == "completed":
            break
        sleep(0.02)

    assert status["status"] == "completed"
    assert status["resultId"] == "adhoc_validated"
    result = client.get(f"/api/runs/{run['id']}/result")
    assert result.status_code == 200
    assert result.json()["id"] == "adhoc_validated"


def test_admin_costs_empty_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("trust_analytics.telemetry.RUN_LOG_PATH", tmp_path / "missing.jsonl")
    response = client.get("/api/admin/costs")
    assert response.status_code == 200
    data = response.json()

    assert data["totals"]["runs"] == 0
    assert data["runs"] == []
    assert data["pricing"]["estimated"] is True


def test_admin_costs_returns_run_detail(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "runs.jsonl"
    monkeypatch.setattr("trust_analytics.telemetry.RUN_LOG_PATH", log)
    record_event(
        event_type="app_call",
        status="failed",
        action="validate_run",
        run_id="run_failed",
        analysis_id="analysis_failed",
        question_text="Bad question",
        duration_ms=12,
        error={"type": "RuntimeError", "message": "boom"},
        log_path=log,
    )

    response = client.get("/api/admin/costs")
    assert response.status_code == 200
    data = response.json()
    assert data["totals"]["runs"] == 1
    assert data["runs"][0]["status"] == "failed"

    detail = client.get("/api/admin/costs/run_failed")
    assert detail.status_code == 200
    assert detail.json()["summary"]["error"]["message"] == "boom"
