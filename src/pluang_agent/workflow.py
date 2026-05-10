"""LangGraph orchestration for the analytics pipeline.

Per Decision 6: 4-category reject routing, per-category retry counter (limit 1),
external_disagreement → audit_required immediately, retry-exhausted →
audit_required, system_error → audit_required(reason='system_error').

audit_required emits a hand-off package, not a 'failed' state. The package
contains all attempted answers, all reject notes, and unresolved_questions
(seeded from Layer C of the latest QA report).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from pluang_agent.agents.quality_agent import QualityAgent
from pluang_agent.agents.sql_agent import SQLAgent
from pluang_agent.models import (
    AuditHandoff,
    BusinessQuestion,
    PipelineItem,
    PipelineResult,
    QualityReport,
    ReviewCategory,
    ReviewDecision,
    ReviewMode,
    SQLAgentAnswer,
    TerminalState,
)
from pluang_agent.review import collect_review_decisions, summarize_terminal_states


class PipelineState(TypedDict, total=False):
    questions: list[BusinessQuestion]
    items: list[PipelineItem]
    decisions: list[ReviewDecision]
    review_mode: ReviewMode


def run_pipeline(
    questions: list[BusinessQuestion],
    sql_agent: SQLAgent,
    quality_agent: QualityAgent,
    review_mode: ReviewMode,
) -> PipelineResult:
    graph = StateGraph(PipelineState)

    def answer_questions(state: PipelineState) -> dict[str, Any]:
        items: list[PipelineItem] = []
        for question in state["questions"]:
            answer = sql_agent.answer(question)
            items.append(
                PipelineItem(
                    question=question,
                    answer=answer,
                    quality_report=quality_agent.assess(answer),
                )
            )
        return {"items": items}

    def review_items(state: PipelineState) -> dict[str, Any]:
        decisions = collect_review_decisions(state["items"], state["review_mode"])
        by_id = {decision.question_id: decision for decision in decisions}
        for item in state["items"]:
            decision = by_id[item.question.id]
            item.review_decision = decision
            # System errors fold into AUDIT_REQUIRED with audit_reason set,
            # regardless of the reviewer demo decision (you can't approve a
            # non-answer).
            if item.answer.system_error is not None:
                decision.terminal_state = TerminalState.AUDIT_REQUIRED
                decision.audit_reason = "system_error"
                item.audit_handoff = _build_audit_handoff(
                    item.question.id,
                    [item.answer],
                    [item.quality_report],
                    [decision],
                )
        return {"decisions": decisions, "items": state["items"]}

    def reinvestigate_rejections(state: PipelineState) -> dict[str, Any]:
        for item in state["items"]:
            decision = item.review_decision
            if not decision or decision.decision == "approve":
                continue
            if decision.terminal_state == TerminalState.AUDIT_REQUIRED:
                # Already routed (system_error or earlier audit) — skip.
                continue

            category = decision.category
            if category == ReviewCategory.EXTERNAL_DISAGREEMENT:
                _route_to_audit(item, decision, reason="external_disagreement")
                continue

            # Per-category retry counter — Decision 6.
            already_tried = decision.per_category_retry_counts.get(category, 0) if category else 0
            if already_tried >= 1:
                _route_to_audit(item, decision, reason="retry_exhausted")
                continue
            if category is not None:
                decision.per_category_retry_counts[category] = already_tried + 1

            if category == ReviewCategory.QA_INSUFFICIENT:
                # Re-run QA only (Layer B+C in spirit; R1 stub re-runs the
                # full assess). The SQL Agent answer is unchanged.
                item.reinvestigated_answer = item.answer
                item.reinvestigated_quality_report = quality_agent.assess(item.answer)
            else:
                # answer_wrong / source_wrong — re-prompt SQL Agent with the
                # reviewer note in scope. R2 will further differentiate
                # source_wrong (pin alt source) from answer_wrong.
                item.reinvestigated_answer = sql_agent.answer(
                    item.question, reviewer_note=decision.note
                )
                item.reinvestigated_quality_report = quality_agent.assess(
                    item.reinvestigated_answer
                )
            decision.terminal_state = TerminalState.REINVESTIGATED
        return {"items": state["items"]}

    graph.add_node("answer_questions", answer_questions)
    graph.add_node("review_items", review_items)
    graph.add_node("reinvestigate_rejections", reinvestigate_rejections)
    graph.set_entry_point("answer_questions")
    graph.add_edge("answer_questions", "review_items")
    graph.add_edge("review_items", "reinvestigate_rejections")
    graph.add_edge("reinvestigate_rejections", END)
    compiled = graph.compile()

    final_state = compiled.invoke(
        {
            "questions": questions,
            "review_mode": review_mode,
        }
    )
    items = final_state["items"]
    return PipelineResult(
        items=items,
        review_mode=review_mode,
        terminal_summary=summarize_terminal_states(items),
    )


def _route_to_audit(
    item: PipelineItem, decision: ReviewDecision, *, reason: str
) -> None:
    decision.terminal_state = TerminalState.AUDIT_REQUIRED
    decision.audit_reason = reason
    answers = [item.answer]
    reports = [item.quality_report]
    if item.reinvestigated_answer is not None:
        answers.append(item.reinvestigated_answer)
    if item.reinvestigated_quality_report is not None:
        reports.append(item.reinvestigated_quality_report)
    item.audit_handoff = _build_audit_handoff(
        item.question.id, answers, reports, [decision]
    )


def _build_audit_handoff(
    question_id: str,
    attempted_answers: list[SQLAgentAnswer],
    quality_reports: list[QualityReport],
    reject_history: list[ReviewDecision],
) -> AuditHandoff:
    """Hand-off package per Decision 6. unresolved_questions seeded from the
    latest Layer C; if none, populated with a generic placeholder so the
    reviewer always sees an actionable list."""
    unresolved: list[str] = []
    if quality_reports:
        unresolved = list(quality_reports[-1].layer_c.unresolved_questions)
    if not unresolved:
        unresolved = ["No unresolved_questions populated by Layer C; reviewer should manually scope follow-up."]
    return AuditHandoff(
        question_id=question_id,
        attempted_answers=attempted_answers,
        quality_reports=quality_reports,
        reject_history=reject_history,
        unresolved_questions=unresolved,
    )


def write_pipeline_outputs(result: PipelineResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    answers = [item.answer.model_dump(mode="json") for item in result.items]
    quality = [item.quality_report.model_dump(mode="json") for item in result.items]
    (output_dir / "sql_agent_answers.json").write_text(
        _json_dump(answers),
        encoding="utf-8",
    )
    (output_dir / "quality_report.json").write_text(
        _json_dump(quality),
        encoding="utf-8",
    )
    suffix = (
        "approval" if result.review_mode == ReviewMode.DEMO_APPROVE else "rejection_reinvestigation"
    )
    (output_dir / f"review_{suffix}.log").write_text(_review_log(result), encoding="utf-8")


def _json_dump(value: Any) -> str:
    import json

    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _review_log(result: PipelineResult) -> str:
    lines = [
        f"Review mode: {result.review_mode.value}",
        f"Terminal summary: {result.terminal_summary}",
        "",
    ]
    for item in result.items:
        decision = item.review_decision
        lines.append(f"Question: {item.question.id}")
        lines.append(f"Decision: {decision.decision if decision else 'missing'}")
        if decision and decision.category:
            lines.append(f"Category: {decision.category.value}")
            lines.append(f"Note: {decision.note}")
        if decision and decision.per_category_retry_counts:
            lines.append(f"Retry counts: {dict(decision.per_category_retry_counts)}")
        if decision and decision.terminal_state:
            lines.append(f"Terminal state: {decision.terminal_state.value}")
        if decision and decision.audit_reason:
            lines.append(f"Audit reason: {decision.audit_reason}")
        if item.answer.system_error is not None:
            err = item.answer.system_error
            lines.append(f"System error: [{err.error_class}] {err.message}")
            lines.append(f"Suggested action: {err.suggested_action}")
        for warning in item.answer.warnings:
            lines.append(f"Warning: {warning.splitlines()[0][:200]}")
        tp = item.quality_report.layer_c.trust_profile
        lines.append(
            f"Trust profile: {tp.overall} "
            f"(correctness={tp.dimensions.correctness} "
            f"source={tp.dimensions.source_reliability} "
            f"ambiguity={tp.dimensions.ambiguity})"
        )
        lines.append(f"QA summary: {tp.reviewer_summary}")
        if item.reinvestigated_quality_report:
            tp2 = item.reinvestigated_quality_report.layer_c.trust_profile
            lines.append(f"Reinvestigated trust profile: {tp2.overall} — {tp2.reviewer_summary}")
        if item.audit_handoff:
            lines.append(f"Audit hand-off unresolved_questions: {item.audit_handoff.unresolved_questions}")
        lines.append("")
    return "\n".join(lines)
