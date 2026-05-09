"""LangGraph orchestration for the analytics pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from pluang_agent.agents.quality_agent import QualityAgent
from pluang_agent.agents.sql_agent import SQLAgent
from pluang_agent.models import (
    BusinessQuestion,
    PipelineItem,
    PipelineResult,
    ReviewCategory,
    ReviewDecision,
    ReviewMode,
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
            item.review_decision = by_id[item.question.id]
        return {"decisions": decisions, "items": state["items"]}

    def reinvestigate_rejections(state: PipelineState) -> dict[str, Any]:
        for item in state["items"]:
            decision = item.review_decision
            if not decision or decision.decision == "approve":
                continue
            if decision.category == ReviewCategory.EXTERNAL_DISAGREEMENT:
                decision.terminal_state = TerminalState.AUDIT_REQUIRED
                continue
            if decision.retry_count >= 1:
                decision.terminal_state = TerminalState.AUDIT_REQUIRED
                continue

            decision.retry_count += 1
            if decision.category == ReviewCategory.QA_INSUFFICIENT:
                item.reinvestigated_answer = item.answer
                item.reinvestigated_quality_report = quality_agent.assess(item.answer)
            else:
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
            lines.append(f"Retry count: {decision.retry_count}")
        if decision and decision.terminal_state:
            lines.append(f"Terminal state: {decision.terminal_state.value}")
        lines.append(f"QA summary: {item.quality_report.summary}")
        if item.reinvestigated_quality_report:
            lines.append(f"Reinvestigated QA summary: {item.reinvestigated_quality_report.summary}")
        lines.append("")
    return "\n".join(lines)
