from __future__ import annotations

import zipfile
from io import BytesIO

from fastapi.testclient import TestClient

from trust_analytics.api import app
from trust_analytics.portal import AUDIT_ID, HERO_ID, MTU_ID

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
    assert data["quality"]["label"] in {"Ready to build", "Needs shaping"}


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
