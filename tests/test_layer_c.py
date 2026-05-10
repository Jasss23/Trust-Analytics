"""Layer C — LLM-driven composition with rule-based fallback."""

from __future__ import annotations

import json

from pluang_agent.layer_c import run_layer_c
from pluang_agent.llm import LLMResponse
from pluang_agent.models import (
    Hypothesis,
    LayerACheck,
    LayerAReport,
    LayerBReport,
    SourceProvenance,
    SQLAgentAnswer,
    UsageRecord,
)


def _answer(interpretation_choices_count: int = 0) -> SQLAgentAnswer:
    from pluang_agent.models import InterpretationChoice

    ics = [
        InterpretationChoice(
            choice=f"choice_{i}", alternatives=[], rationale="r"
        )
        for i in range(interpretation_choices_count)
    ]
    return SQLAgentAnswer(
        question_id="q1",
        question="?",
        metric_name="m",
        metric_value=[{"x": 1.0}],
        period="oct 2025",
        source=SourceProvenance(primary_table="t", why_chosen="x", alternatives_available=[]),
        sql="SELECT 1",
        logic="x",
        result_rows=[{"x": 1.0}],
        interpretation_choices=ics,
    )


def _layer_a(any_fail: bool) -> LayerAReport:
    return LayerAReport(
        checks=[
            LayerACheck(name="non_empty_result", result="PASS"),
            LayerACheck(
                name="plausible_range",
                result="FAIL" if any_fail else "PASS",
                detail="value too low" if any_fail else None,
            ),
        ]
    )


def _layer_b(verdict: str, hypothesis: Hypothesis | None = None) -> LayerBReport:
    return LayerBReport(verdict=verdict, hypothesis=hypothesis)


# ---------------------------------------------------------------------------
# Rule-based fallback (no LLM)
# ---------------------------------------------------------------------------


def test_fallback_no_llm_clean_pipeline_is_green() -> None:
    report = run_layer_c(_answer(), _layer_a(False), _layer_b("AGREE"), llm_client=None)
    assert report.trust_profile.overall == "GREEN"
    assert report.trust_profile.dimensions.correctness == "GREEN"
    assert report.trust_profile.dimensions.source_reliability == "GREEN"
    assert report.trust_profile.dimensions.ambiguity == "GREEN"


def test_fallback_disagreement_is_red() -> None:
    report = run_layer_c(
        _answer(),
        _layer_a(False),
        _layer_b("DISAGREEMENT"),
        llm_client=None,
    )
    assert report.trust_profile.overall == "RED"
    assert report.trust_profile.dimensions.source_reliability == "RED"


def test_fallback_layer_a_fail_is_red_with_unresolved() -> None:
    report = run_layer_c(_answer(), _layer_a(True), _layer_b("AGREE"), llm_client=None)
    assert report.trust_profile.overall == "RED"
    assert report.trust_profile.dimensions.correctness == "RED"
    assert any("plausible_range" in q for q in report.unresolved_questions)


def test_fallback_interpretation_choices_make_yellow_ambiguity() -> None:
    report = run_layer_c(_answer(2), _layer_a(False), _layer_b("AGREE"), llm_client=None)
    assert report.trust_profile.dimensions.ambiguity == "YELLOW"


def test_fallback_n_a_layer_b_makes_yellow_source() -> None:
    report = run_layer_c(_answer(), _layer_a(False), _layer_b("NOT_APPLICABLE"), llm_client=None)
    assert report.trust_profile.dimensions.source_reliability == "YELLOW"


# ---------------------------------------------------------------------------
# LLM-driven path
# ---------------------------------------------------------------------------


class _StubLLM:
    def __init__(self, content: str):
        self._content = content
        self.available = True

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        return LLMResponse(content=self._content, usage=UsageRecord())


def test_llm_path_parses_valid_json_payload() -> None:
    payload = json.dumps(
        {
            "trust_profile": {
                "dimensions": {
                    "correctness": "GREEN",
                    "source_reliability": "RED",
                    "ambiguity": "GREEN",
                },
                "overall": "RED",
                "reviewer_summary": "Sources disagree on December.",
            },
            "unresolved_questions": ["Confirm fct is authoritative."],
        }
    )
    report = run_layer_c(
        _answer(),
        _layer_a(False),
        _layer_b("DISAGREEMENT"),
        llm_client=_StubLLM(payload),  # type: ignore[arg-type]
    )
    assert report.trust_profile.overall == "RED"
    assert "December" in report.trust_profile.reviewer_summary
    assert report.unresolved_questions == ["Confirm fct is authoritative."]


def test_llm_path_falls_back_on_invalid_json() -> None:
    """Malformed JSON → fallback to rule-based composition (no exception)."""
    report = run_layer_c(
        _answer(),
        _layer_a(False),
        _layer_b("DISAGREEMENT"),
        llm_client=_StubLLM("not json at all"),  # type: ignore[arg-type]
    )
    # Fallback rule-based produces RED for DISAGREEMENT
    assert report.trust_profile.overall == "RED"
    assert "Layer B" in report.trust_profile.reviewer_summary
