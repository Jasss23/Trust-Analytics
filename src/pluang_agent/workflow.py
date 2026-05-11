"""LangGraph orchestration for the analytics pipeline.

R5: Unified retry budget on PipelineItem.remaining_budget covers
schema-grounded SQL exec retry, pre-flight retry, AND human-rejection
retry. Categories still drive *what* to do on human reject; the budget
governs *how many* tries are left.

Per Decision 6: 4-category reject routing, external_disagreement →
audit_required immediately, retry-exhausted → audit_required,
system_error → audit_required(reason='system_error').

audit_required emits a hand-off package, not a 'failed' state. The package
contains all attempted answers, all reject notes, all per-attempt outcomes,
and unresolved_questions (seeded from Layer C of the latest QA report).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from pluang_agent.agents.quality_agent import QualityAgent
from pluang_agent.agents.sql_agent import SQLAgent
from pluang_agent.models import (
    AttemptOutcome,
    AuditHandoff,
    BusinessQuestion,
    CorrectionContext,
    PipelineItem,
    PipelineResult,
    QualityReport,
    ReviewCategory,
    ReviewDecision,
    ReviewMode,
    SQLAgentAnswer,
    SystemError,
    TerminalState,
)
from pluang_agent.planner import plan_question
from pluang_agent.review import collect_review_decisions, summarize_terminal_states
from pluang_agent.sql_attempt import mark_budget_exhausted, run_one_attempt


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
    registry = sql_agent.metrics_registry
    db_path = sql_agent.db_path
    metadata = sql_agent.metadata

    def answer_questions(state: PipelineState) -> dict[str, Any]:
        items: list[PipelineItem] = []
        for question in state["questions"]:
            planned = plan_question(
                question,
                registry,
                metadata,
                llm_client=sql_agent.llm_client,
            )
            if planned.system_error is not None or planned.plan is None:
                answer = _planner_error_answer(question, planned.system_error)
                qa_report = quality_agent.assess(answer)
                items.append(
                    PipelineItem(
                        question=question,
                        question_plan=None,
                        answer=answer,
                        quality_report=qa_report,
                        auto_retry_budget=2,
                        remaining_budget=2,
                        attempts=[],
                    )
                )
                continue
            question_plan = planned.plan
            derivation_trace = planned.derivation_trace
            attempts: list[AttemptOutcome] = []
            budget = 2  # R5: unified initial budget for one PipelineItem
            correction: CorrectionContext | None = None
            outcome: AttemptOutcome
            while True:
                outcome = run_one_attempt(
                    sql_agent=sql_agent,
                    question=question,
                    db_path=db_path,
                    registry=registry,
                    question_plan=question_plan,
                    derivation_trace=derivation_trace,
                    correction_context=correction,
                )
                attempts.append(outcome)
                if outcome.status == "success":
                    break
                if outcome.status == "llm_hard_failure":
                    # Auth/quota/transient — don't retry, escalate via existing
                    # system_error path.
                    break
                if budget <= 0:
                    mark_budget_exhausted(outcome.answer, outcome)
                    break
                budget -= 1
                correction = outcome.correction_context
            final_answer = outcome.answer
            items.append(
                PipelineItem(
                    question=question,
                    question_plan=question_plan,
                    answer=final_answer,
                    quality_report=quality_agent.assess(final_answer, question_plan=question_plan),
                    auto_retry_budget=2,
                    remaining_budget=budget,
                    attempts=attempts,
                )
            )
        return {"items": items}

    def review_items(state: PipelineState) -> dict[str, Any]:
        decisions = collect_review_decisions(state["items"], state["review_mode"])
        by_id = {decision.question_id: decision for decision in decisions}
        for item in state["items"]:
            decision = by_id[item.question.id]
            item.review_decision = decision
            # System errors fold into AUDIT_REQUIRED regardless of demo decision —
            # you can't approve a non-answer.
            if item.answer.system_error is not None:
                decision.terminal_state = TerminalState.AUDIT_REQUIRED
                if item.answer.system_error.error_class == "auto_retry_exhausted":
                    decision.audit_reason = "auto_retry_exhausted"
                else:
                    decision.audit_reason = "system_error"
                item.audit_handoff = _build_audit_handoff(
                    item.question.id,
                    [item.answer],
                    [item.quality_report],
                    [decision],
                    item.attempts,
                )
        return {"decisions": decisions, "items": state["items"]}

    def reinvestigate_rejections(state: PipelineState) -> dict[str, Any]:
        for item in state["items"]:
            decision = item.review_decision
            if not decision or decision.decision == "approve":
                continue
            if decision.terminal_state == TerminalState.AUDIT_REQUIRED:
                # Already routed (system_error / auto_retry_exhausted / earlier audit).
                continue

            category = decision.category
            if category == ReviewCategory.EXTERNAL_DISAGREEMENT:
                _route_to_audit(item, decision, reason="external_disagreement")
                continue

            # R5: unified budget. If exhausted, route to audit_required.
            if item.remaining_budget <= 0:
                _route_to_audit(item, decision, reason="retry_exhausted")
                continue
            item.remaining_budget -= 1

            if category == ReviewCategory.QA_INSUFFICIENT:
                # Re-run QA only — SQL answer unchanged.
                item.reinvestigated_answer = item.answer
                item.reinvestigated_quality_report = quality_agent.assess(
                    item.answer,
                    question_plan=item.question_plan,
                )
            else:
                # answer_wrong / source_wrong — re-prompt SQL Agent with the
                # reviewer note as the correction context, then re-run QA.
                planned = plan_question(
                    item.question,
                    registry,
                    metadata,
                    reviewer_note=decision.note,
                    llm_client=sql_agent.llm_client,
                )
                if planned.system_error is not None or planned.plan is None:
                    item.reinvestigated_answer = _planner_error_answer(
                        item.question,
                        planned.system_error,
                    )
                    item.reinvestigated_quality_report = quality_agent.assess(
                        item.reinvestigated_answer
                    )
                    _route_to_audit(item, decision, reason="planner_validation_failed")
                    continue
                item.question_plan = planned.plan
                correction = CorrectionContext(
                    prev_sql=item.answer.sql,
                    failure_kind="human_reject",
                    failure_detail=(
                        f"Reviewer rejected (category={category.value if category else 'unknown'})."
                    ),
                    schema_hint=None,
                    reviewer_note=decision.note,
                )
                outcome = run_one_attempt(
                    sql_agent=sql_agent,
                    question=item.question,
                    db_path=db_path,
                    registry=registry,
                    question_plan=item.question_plan,
                    derivation_trace=planned.derivation_trace,
                    correction_context=correction,
                    reviewer_note=decision.note,
                )
                item.attempts.append(outcome)
                item.reinvestigated_answer = outcome.answer
                if outcome.status != "success":
                    _stamp_failed_reinvestigation(outcome)
                    item.reinvestigated_quality_report = quality_agent.assess(outcome.answer)
                    _route_to_audit(item, decision, reason=outcome.status)
                    continue
                item.reinvestigated_quality_report = quality_agent.assess(
                    outcome.answer,
                    question_plan=item.question_plan,
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
        item.question.id, answers, reports, [decision], item.attempts
    )


def _build_audit_handoff(
    question_id: str,
    attempted_answers: list[SQLAgentAnswer],
    quality_reports: list[QualityReport],
    reject_history: list[ReviewDecision],
    attempts: list[AttemptOutcome],
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
        attempts=attempts,
    )


def _planner_error_answer(
    question: BusinessQuestion,
    error: SystemError | None,
) -> SQLAgentAnswer:
    err = error or SystemError(
        error_class="planner_validation_failed",
        message="Question planner failed without a structured error.",
        suggested_action="Inspect planner inputs and retry.",
    )
    return SQLAgentAnswer(
        question_id=question.id,
        question=question.text,
        metric_name=question.metric,
        metric_value=None,
        period=question.period,
        source=None,
        sql="",
        filters=[],
        assumptions=[],
        logic="No SQL generated — planner validation failed.",
        result_rows=[],
        interpretation_choices=[],
        dq_notes=[],
        warnings=[err.message],
        system_error=err,
    )


def _stamp_failed_reinvestigation(outcome: AttemptOutcome) -> None:
    if outcome.answer.system_error is not None:
        return
    detail = ""
    if outcome.correction_context is not None:
        detail = outcome.correction_context.failure_detail
    error_class = (
        "answer_shape_validation_failed"
        if outcome.status == "pre_flight_failure"
        else outcome.status
    )
    if error_class == "llm_soft_failure":
        error_class = "output"
    if error_class not in {
        "output",
        "exec_failure",
        "pre_flight_failure",
        "answer_shape_validation_failed",
    }:
        error_class = "llm_error"
    outcome.answer.system_error = SystemError(
        error_class=error_class,  # type: ignore[arg-type]
        message=f"Reinvestigation attempt did not produce an acceptable answer: {detail}",
        suggested_action=(
            "Inspect the failed reinvestigation attempt and validated question plan. "
            "Do not approve this item until the answer satisfies shape/source validation."
        ),
        raw=outcome.status,
    )


def write_pipeline_outputs(result: PipelineResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    answers = [item.answer.model_dump(mode="json") for item in result.items]
    plans = [
        item.question_plan.model_dump(mode="json") if item.question_plan is not None else None
        for item in result.items
    ]
    quality = [item.quality_report.model_dump(mode="json") for item in result.items]
    (output_dir / "question_plans.json").write_text(
        _json_dump(plans),
        encoding="utf-8",
    )
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
        lines.append(
            f"Retry budget: used {item.auto_retry_budget - item.remaining_budget}/"
            f"{item.auto_retry_budget} (remaining {item.remaining_budget})"
        )
        if item.attempts:
            lines.append(f"Attempts: {[a.status for a in item.attempts]}")
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
        if item.reinvestigated_quality_report and item.reinvestigated_answer:
            tp2 = item.reinvestigated_quality_report.layer_c.trust_profile
            lines.append(f"Reinvestigated trust profile: {tp2.overall} — {tp2.reviewer_summary}")
            lines.append("Reinvestigation diff:")
            orig_src = item.answer.source.primary_table if item.answer.source else "(none)"
            new_src = (
                item.reinvestigated_answer.source.primary_table
                if item.reinvestigated_answer.source
                else "(none)"
            )
            orig_overall = item.quality_report.layer_c.trust_profile.overall
            new_overall = tp2.overall
            change_marker = lambda a, b: " (changed)" if a != b else ""  # noqa: E731
            lines.append(f"  source.primary_table: {orig_src} → {new_src}{change_marker(orig_src, new_src)}")
            lines.append(f"  trust profile: {orig_overall} → {new_overall}{change_marker(orig_overall, new_overall)}")
            orig_sql_first = item.answer.sql.splitlines()[0] if item.answer.sql else "(empty)"
            new_sql_first = (
                item.reinvestigated_answer.sql.splitlines()[0]
                if item.reinvestigated_answer.sql
                else "(empty)"
            )
            lines.append(
                f"  sql (first line): {orig_sql_first[:100]}...{change_marker(orig_sql_first, new_sql_first)}"
            )
        if item.audit_handoff:
            lines.append(f"Audit hand-off unresolved_questions: {item.audit_handoff.unresolved_questions}")
        lines.append("")
    return "\n".join(lines)
