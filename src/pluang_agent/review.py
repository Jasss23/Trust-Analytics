"""Human review decision handling."""

from __future__ import annotations

from collections import Counter

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from pluang_agent.models import (
    PipelineItem,
    ReviewCategory,
    ReviewDecision,
    ReviewMode,
    TerminalState,
)

console = Console()


def collect_review_decisions(
    items: list[PipelineItem],
    review_mode: ReviewMode,
) -> list[ReviewDecision]:
    if review_mode == ReviewMode.DEMO_APPROVE:
        return [
            ReviewDecision(
                question_id=item.question.id,
                decision="approve",
                terminal_state=TerminalState.APPROVED,
            )
            for item in items
        ]
    if review_mode == ReviewMode.DEMO_REJECT:
        decisions: list[ReviewDecision] = []
        for item in items:
            if item.question.id == "q5_gtv_mom_trend_oct_dec_2025":
                decisions.append(
                    ReviewDecision(
                        question_id=item.question.id,
                        decision="reject",
                        category=ReviewCategory.SOURCE_WRONG,
                        note=(
                            "Trend answer should be reinvestigated because the December business "
                            "summary Total row disagrees with fct_trading_daily."
                        ),
                    )
                )
            else:
                decisions.append(
                    ReviewDecision(
                        question_id=item.question.id,
                        decision="approve",
                        terminal_state=TerminalState.APPROVED,
                    )
                )
        return decisions
    return _collect_interactive(items)


def summarize_terminal_states(items: list[PipelineItem]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in items:
        if item.review_decision and item.review_decision.terminal_state:
            counts[item.review_decision.terminal_state.value] += 1
        else:
            counts["unknown"] += 1
    return dict(sorted(counts.items()))


def _collect_interactive(items: list[PipelineItem]) -> list[ReviewDecision]:
    decisions: list[ReviewDecision] = []
    for item in items:
        console.print(
            Panel.fit(
                f"[bold]{item.question.text}[/bold]\n\n"
                f"Answer: {item.answer.metric_value}\n"
                f"Sources: {', '.join(item.answer.source_tables)}\n"
                f"QA: {item.quality_report.summary}",
                title=item.question.id,
            )
        )
        approve = typer.confirm("Approve this answer?", default=True)
        if approve:
            decisions.append(
                ReviewDecision(
                    question_id=item.question.id,
                    decision="approve",
                    terminal_state=TerminalState.APPROVED,
                )
            )
            continue

        category = Prompt.ask(
            "Rejection category",
            choices=[category.value for category in ReviewCategory],
            default=ReviewCategory.SOURCE_WRONG.value,
        )
        note = Prompt.ask("Reviewer note")
        decisions.append(
            ReviewDecision(
                question_id=item.question.id,
                decision="reject",
                category=ReviewCategory(category),
                note=note,
            )
        )
    return decisions

