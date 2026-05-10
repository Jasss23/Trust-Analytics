"""Human review (Decision 6).

Reviewer audience is the data team (Decision 2). Output is technical: SQL,
source rationale, layer-by-layer trust profile, interpretation choices.

Demo modes are kept for sample-output generation; interactive mode runs at
submission time. Per Decision 6, rejection requires a category + note; routing
is determined by the category, never by parsing the note.
"""

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
        if item.answer.system_error is not None:
            err = item.answer.system_error
            console.print(
                Panel.fit(
                    f"[bold red]SYSTEM ERROR — pipeline escalated[/bold red]\n\n"
                    f"Question: {item.question.text}\n"
                    f"Error class: {err.error_class}\n"
                    f"Message: {err.message}\n"
                    f"Suggested action: {err.suggested_action}\n\n"
                    f"This question has no answer to approve or reject. "
                    f"It will be marked as audit_required (system_error).",
                    title=item.question.id,
                )
            )
            decisions.append(
                ReviewDecision(
                    question_id=item.question.id,
                    decision="approve",
                    terminal_state=TerminalState.AUDIT_REQUIRED,
                    audit_reason="system_error",
                )
            )
            continue

        tp = item.quality_report.layer_c.trust_profile
        layer_a = item.quality_report.layer_a
        a_summary = ", ".join(
            f"{c.name}={c.result}" for c in layer_a.checks
        ) or "(no checks)"
        ic_lines = "\n".join(
            f"  • {ic.choice} (rationale: {ic.rationale})"
            for ic in item.answer.interpretation_choices
        ) or "  (none)"
        source_block = (
            f"Source: {item.answer.source.primary_table} — {item.answer.source.why_chosen}"
            if item.answer.source
            else "Source: (not populated)"
        )
        console.print(
            Panel.fit(
                f"[bold]{item.question.text}[/bold]\n\n"
                f"Answer: {item.answer.metric_value}\n"
                f"{source_block}\n"
                f"SQL: {item.answer.sql.splitlines()[0] if item.answer.sql else '(empty)'} ...\n"
                f"\n[bold]Trust profile: {tp.overall}[/bold]\n"
                f"  correctness={tp.dimensions.correctness} "
                f"source={tp.dimensions.source_reliability} "
                f"ambiguity={tp.dimensions.ambiguity}\n"
                f"  {tp.reviewer_summary}\n"
                f"\nLayer A: {a_summary}\n"
                f"Layer B verdict: {item.quality_report.layer_b.verdict}\n"
                f"Interpretation choices:\n{ic_lines}",
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
