"""Round-trip every R1-frozen contract through Pydantic.

These tests guard the freeze: if a schema change would break wire compat
between SQL Agent and QA Agent, this test fires before R2/R3 hit the same
bug at integration time.
"""

from __future__ import annotations

from pluang_agent.models import (
    AuditHandoff,
    BusinessQuestion,
    Hypothesis,
    InterpretationChoice,
    LayerACheck,
    LayerAReport,
    LayerBReport,
    LayerCReport,
    PipelineItem,
    PipelineResult,
    QualityReport,
    ReviewCategory,
    ReviewDecision,
    ReviewMode,
    SourceProvenance,
    SQLAgentAnswer,
    SystemError,
    TerminalState,
    TrustDimensions,
    TrustProfile,
    UsageRecord,
)


def _make_qa_report() -> QualityReport:
    return QualityReport(
        question_id="q1_gtv_idr_by_asset_oct_2025",
        layer_a=LayerAReport(
            checks=[
                LayerACheck(name="non_empty_result", result="PASS"),
                LayerACheck(name="no_negative_for_always_positive", result="PASS"),
            ]
        ),
        layer_b=LayerBReport(
            cross_source_findings=[],
            verdict="NOT_APPLICABLE",
            hypothesis_absence_note="R1 stub.",
        ),
        layer_c=LayerCReport(
            trust_profile=TrustProfile(
                dimensions=TrustDimensions(
                    correctness="GREEN", source_reliability="GREEN", ambiguity="GREEN"
                ),
                overall="GREEN",
                reviewer_summary="Layer A clean.",
            ),
            unresolved_questions=[],
        ),
    )


def _make_answer() -> SQLAgentAnswer:
    return SQLAgentAnswer(
        question_id="q1_gtv_idr_by_asset_oct_2025",
        question="What was total GTV (IDR) by asset class in October 2025?",
        metric_name="gtv_idr_by_asset_class",
        metric_value={"crypto": 1, "fx": 2},
        period="October 2025",
        source=SourceProvenance(
            primary_table="fct_trading_daily",
            why_chosen="Canonical completed-transaction source.",
            alternatives_available=["agg_monthly_biz_summary"],
        ),
        sql="SELECT 1",
        filters={"month": "2025-10"},
        assumptions=["completed only"],
        logic="sum gtv_idr by asset_class",
        result_rows=[{"asset_class": "crypto", "gtv_idr": 1}],
        interpretation_choices=[
            InterpretationChoice(
                choice="primary = fct",
                alternatives=["agg_monthly_biz_summary"],
                rationale="canonical",
            )
        ],
        dq_notes=[],
        warnings=[],
        usage=UsageRecord(prompt_tokens=10, completion_tokens=20, total_tokens=30, cost=0.001, model="x"),
    )


def test_sql_agent_answer_roundtrip() -> None:
    answer = _make_answer()
    dumped = answer.model_dump(mode="json")
    rehydrated = SQLAgentAnswer.model_validate(dumped)
    assert rehydrated == answer


def test_quality_report_roundtrip() -> None:
    qa = _make_qa_report()
    rehydrated = QualityReport.model_validate(qa.model_dump(mode="json"))
    assert rehydrated == qa


def test_filters_accepts_dict_or_list() -> None:
    """Real LLM output returned dict; we coerce in the agent but the
    contract itself accepts both, so partial-tolerance is on the schema."""
    a = SQLAgentAnswer.model_validate(_make_answer().model_dump() | {"filters": {"m": "2025-10"}})
    b = SQLAgentAnswer.model_validate(_make_answer().model_dump() | {"filters": ["m=2025-10"]})
    assert a.filters == {"m": "2025-10"}
    assert b.filters == ["m=2025-10"]


def test_pipeline_item_with_audit_handoff() -> None:
    answer = _make_answer()
    answer.system_error = SystemError(
        error_class="quota",
        message="quota out",
        suggested_action="add credit",
    )
    qa = _make_qa_report()
    decision = ReviewDecision(
        question_id=answer.question_id,
        decision="approve",
        terminal_state=TerminalState.AUDIT_REQUIRED,
        audit_reason="system_error",
        per_category_retry_counts={ReviewCategory.SOURCE_WRONG: 1},
    )
    handoff = AuditHandoff(
        question_id=answer.question_id,
        attempted_answers=[answer],
        quality_reports=[qa],
        reject_history=[decision],
        unresolved_questions=["add credit and retry"],
    )
    item = PipelineItem(
        question=BusinessQuestion(
            id=answer.question_id,
            text=answer.question,
            metric=answer.metric_name,
            period=answer.period,
        ),
        answer=answer,
        quality_report=qa,
        review_decision=decision,
        audit_handoff=handoff,
    )
    result = PipelineResult(
        items=[item],
        review_mode=ReviewMode.DEMO_APPROVE,
        terminal_summary={"audit_required": 1},
    )
    rehydrated = PipelineResult.model_validate(result.model_dump(mode="json"))
    assert rehydrated == result


def test_terminal_states_match_spec() -> None:
    """Decision 6: terminal_states are {approved, audit_required}; reinvestigated
    is a transitional terminal kept for the demo flow visibility."""
    states = {s.value for s in TerminalState}
    assert "approved" in states
    assert "audit_required" in states
    assert "escalated" not in states  # folded into audit_required(reason=system_error)


def test_hypothesis_requires_evidence_and_blast_radius() -> None:
    """Per Decision 4: hypothesis must cite evidence and declare what it does
    not explain. Pydantic enforces presence; non-empty evidence is asserted
    here as a floor."""
    h = Hypothesis(
        proposal="ops includes pending",
        evidence=["mart_ops_dashboard.sql filters status != 'failed'"],
        confidence="MED",
        what_this_does_not_explain="Why fct and biz also disagree by 1bp on December.",
    )
    assert h.evidence
    assert h.what_this_does_not_explain
