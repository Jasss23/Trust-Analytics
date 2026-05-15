"""Human review (Decision 6).

Reviewer audience is the data team (Decision 2). Output is technical and
information-dense: full Layer B findings table, full SQL with syntax
highlighting, full interpretation_choices, source provenance, hypothesis
detail, category-specific note coaching on rejection.

Per Decision 6: rejection requires a category + free-form note; routing is
determined by the category, never by parsing the note.
"""

from __future__ import annotations

from collections import Counter

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from pluang_agent.models import (
    DerivationTrace,
    Hypothesis,
    LayerBReport,
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


# ---------------------------------------------------------------------------
# Interactive review
# ---------------------------------------------------------------------------


def _collect_interactive(items: list[PipelineItem]) -> list[ReviewDecision]:
    decisions: list[ReviewDecision] = []
    for item in items:
        if item.answer.system_error is not None:
            decisions.append(_render_system_error_panel_and_decide(item))
            continue

        _render_review_panel(item)

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
            choices=[c.value for c in ReviewCategory],
            default=ReviewCategory.SOURCE_WRONG.value,
        )
        cat_enum = ReviewCategory(category)
        starter = _starter_note_for_category(cat_enum, item)
        note = Prompt.ask("Reviewer note", default=starter)
        decisions.append(
            ReviewDecision(
                question_id=item.question.id,
                decision="reject",
                category=cat_enum,
                note=note,
            )
        )
    return decisions


# ---------------------------------------------------------------------------
# Panel rendering
# ---------------------------------------------------------------------------


def _render_review_panel(item: PipelineItem) -> None:
    """Top-level reviewer panel. Renders a sequence of Rich primitives."""
    a = item.answer
    qa = item.quality_report
    tp = qa.layer_c.trust_profile

    # R6: Key Facts header strip — single dense table for the basics so the
    # reviewer doesn't have to hunt through prose for "what window / what
    # source / what filters / what aggregator".
    _render_key_facts(item)

    # R6: Derivation panel — structured proof of source choice from the trace.
    if a.derivation_trace is not None:
        _render_derivation_panel(a.derivation_trace)

    # Header — question + trust profile
    header_lines = [
        Text(item.question.text, style="bold"),
        Text(""),
        Text(f"Trust profile: {tp.overall}", style="bold"),
        Text(
            f"  correctness={tp.dimensions.correctness}  "
            f"source={tp.dimensions.source_reliability}  "
            f"ambiguity={tp.dimensions.ambiguity}"
        ),
        Text(""),
        Text(tp.reviewer_summary, style="italic"),
    ]
    if qa.layer_c.unresolved_questions:
        header_lines.append(Text(""))
        header_lines.append(Text("Unresolved questions:", style="bold yellow"))
        for q in qa.layer_c.unresolved_questions:
            header_lines.append(Text(f"  • {q}"))
    console.print(
        Panel(Group(*header_lines), title=f"[bold]{item.question.id}[/bold]", border_style="blue")
    )

    # Source provenance — kept as a fallback / quick-glance even when the
    # Derivation panel above covers the same ground in richer form.
    if a.source:
        console.print(
            Panel(
                Group(
                    Text(f"Primary table: {a.source.primary_table}", style="bold"),
                    Text(f"Why chosen: {a.source.why_chosen}"),
                    Text(
                        f"Alternatives available: "
                        f"{', '.join(a.source.alternatives_available) or '(none)'}"
                    ),
                ),
                title="Source",
                border_style="cyan",
            )
        )

    # Answer (metric_value)
    console.print(
        Panel(
            Text(_pretty_metric_value(a.metric_value)),
            title="Answer",
            border_style="green",
        )
    )

    # Full SQL with syntax highlighting — R6 pretty-formats via sqlglot first
    if a.sql:
        formatted_sql = _pretty_sql(a.sql)
        console.print(
            Panel(
                Syntax(formatted_sql, "sql", theme="ansi_dark", word_wrap=True),
                title="SQL",
                border_style="magenta",
            )
        )

    # QA Summary — visually emphasise the case study's flag-vs-hypothesis
    # distinction. FLAGS = what we deterministically know; HYPOTHESIS = what
    # we suspect (LLM-proposed, grounded in evidence, MAY be null).
    _render_qa_summary(qa)

    # Layer A — FLAGS (deterministic checks)
    a_total = len(qa.layer_a.checks)
    a_failed = sum(1 for c in qa.layer_a.checks if c.result == "FAIL")
    a_table = Table(
        title=f"Layer A — FLAGS (deterministic checks)  {a_total - a_failed}/{a_total} pass",
        show_lines=False,
        expand=True,
    )
    a_table.add_column("Check")
    a_table.add_column("Result")
    a_table.add_column("Detail / evidence")
    for c in qa.layer_a.checks:
        result_style = {"PASS": "green", "FAIL": "red", "NOT_APPLICABLE": "dim"}.get(c.result, "")
        ev = c.detail or ""
        if c.evidence:
            ev = (ev + " | " if ev else "") + "; ".join(c.evidence[:3])
        a_table.add_row(c.name, Text(c.result, style=result_style), ev)
    console.print(a_table)

    # Layer B — cross-source FLAGS + grounded HYPOTHESIS
    _render_layer_b_panel(qa.layer_b)

    # Interpretation choices
    if a.interpretation_choices:
        ic_lines: list[Text | str] = []
        for i, ic in enumerate(a.interpretation_choices, 1):
            ic_lines.append(Text(f"{i}. {ic.choice}", style="bold"))
            if ic.alternatives:
                ic_lines.append(Text(f"   Alternatives: {', '.join(ic.alternatives)}"))
            if ic.rationale:
                ic_lines.append(Text(f"   Rationale: {ic.rationale}", style="italic"))
        console.print(
            Panel(
                Group(*ic_lines),
                title="Interpretation choices",
                border_style="yellow",
            )
        )


def _render_qa_summary(qa) -> None:  # type: ignore[no-untyped-def]
    """One-line `FLAGS` vs `HYPOTHESIS` summary at the top of the QA section.

    The case study explicitly asks the Quality Agent to *flag first, then
    hypothesise where it can* — and to keep the two clearly distinguished.
    This summary block lets the reviewer see, in one glance:
      - Layer A flags: deterministic pass/fail count.
      - Layer B flags: verdict + #findings + max |delta|.
      - Hypothesis: confidence + one-line proposal, or 'null (no grounded basis)'.
    """
    layer_a_total = len(qa.layer_a.checks)
    layer_a_failed = sum(1 for c in qa.layer_a.checks if c.result == "FAIL")

    layer_b = qa.layer_b
    if layer_b.cross_source_findings:
        deltas = [
            abs(f.delta_vs_primary)
            for f in layer_b.cross_source_findings
            if f.delta_vs_primary is not None
        ]
        max_delta = max(deltas) if deltas else None
        b_summary = (
            f"{layer_b.verdict} — {len(layer_b.cross_source_findings)} findings"
            + (f" (max |Δ| {max_delta:.2f}%)" if max_delta is not None else "")
        )
    else:
        b_summary = layer_b.verdict

    hyp = layer_b.hypothesis
    if hyp is not None:
        conf_style = {"HIGH": "green", "MED": "yellow", "LOW": "red"}.get(
            hyp.confidence, ""
        )
        hyp_text = Text(f"{hyp.confidence}", style=f"bold {conf_style}").append(
            f" — {hyp.proposal}", style=""
        )
    elif layer_b.hypothesis_absence_note:
        hyp_text = Text("null — no grounded basis", style="dim italic")
    else:
        hyp_text = Text("(none)", style="dim")

    summary_table = Table(show_header=False, expand=True, box=None)
    summary_table.add_column("Kind", style="bold", no_wrap=True)
    summary_table.add_column("Detail", overflow="fold")
    summary_table.add_row(
        Text("FLAGS (what we know)", style="bold red"),
        Text(
            f"Layer A {layer_a_total - layer_a_failed}/{layer_a_total} pass"
            f"  |  Layer B {b_summary}"
        ),
    )
    summary_table.add_row(
        Text("HYPOTHESIS (what we suspect)", style="bold magenta"),
        hyp_text,
    )
    console.print(
        Panel(
            summary_table,
            title="QA Summary — flag first, hypothesise where grounded",
            border_style="bright_yellow",
        )
    )


def _render_layer_b_panel(layer_b: LayerBReport) -> None:
    if not layer_b.cross_source_findings and layer_b.verdict == "NOT_APPLICABLE":
        console.print(
            Panel(
                Text(layer_b.hypothesis_absence_note or "Layer B not applicable."),
                title=f"Layer B — FLAGS: {layer_b.verdict} (cross-source reconciliation)",
                border_style="dim",
            )
        )
        return

    table = Table(
        title=f"Layer B — FLAGS: {layer_b.verdict} (cross-source reconciliation)",
        show_lines=False,
        expand=True,
    )
    table.add_column("Source", overflow="fold")
    table.add_column("Value", overflow="fold")
    table.add_column("Δ vs primary", justify="right")
    table.add_column("Notes", overflow="fold")
    for f in layer_b.cross_source_findings:
        delta_text: Text | str
        if f.delta_vs_primary is None:
            delta_text = Text("—", style="dim")
        elif abs(f.delta_vs_primary) < 0.001:
            delta_text = Text("0%", style="green")
        elif abs(f.delta_vs_primary) < 1.0:
            delta_text = Text(f"{f.delta_vs_primary:+.3f}%", style="green")
        else:
            delta_text = Text(f"{f.delta_vs_primary:+.2f}%", style="red")
        table.add_row(f.source, _pretty_metric_value(f.value), delta_text, f.notes or "")
    console.print(table)

    if layer_b.hypothesis is not None:
        _render_hypothesis(layer_b.hypothesis)
    elif layer_b.hypothesis_absence_note:
        console.print(
            Panel(
                Text(layer_b.hypothesis_absence_note, style="dim italic"),
                title="HYPOTHESIS (LLM, evidence-grounded) — null: no grounded basis",
                border_style="dim",
            )
        )


def _render_hypothesis(h: Hypothesis) -> None:
    confidence_style = {"HIGH": "green", "MED": "yellow", "LOW": "red"}.get(h.confidence, "")
    lines: list[Text | str] = [
        Text(f"Proposal ({h.confidence}): ", style=f"bold {confidence_style}").append(
            h.proposal, style="bold"
        ),
        Text(""),
        Text("Evidence:", style="bold"),
    ]
    for e in h.evidence[:5]:
        lines.append(Text(f"  • {e}"))
    lines.append(Text(""))
    lines.append(
        Text("What this does NOT explain: ", style="bold").append(
            h.what_this_does_not_explain
        )
    )
    console.print(
        Panel(
            Group(*lines),
            title="HYPOTHESIS (LLM-suspected, evidence-grounded)",
            border_style="green",
        )
    )


def _render_system_error_panel_and_decide(item: PipelineItem) -> ReviewDecision:
    err = item.answer.system_error
    assert err is not None
    console.print(
        Panel(
            Group(
                Text("SYSTEM ERROR — pipeline escalated", style="bold red"),
                Text(""),
                Text(f"Question: {item.question.text}"),
                Text(f"Error class: {err.error_class}"),
                Text(f"Message: {err.message}"),
                Text(f"Suggested action: {err.suggested_action}"),
                Text(""),
                Text(
                    "This question has no answer to approve or reject. "
                    "It will be marked as audit_required (system_error).",
                    style="italic",
                ),
            ),
            title=f"[bold red]{item.question.id}[/bold red]",
            border_style="red",
        )
    )
    return ReviewDecision(
        question_id=item.question.id,
        decision="approve",
        terminal_state=TerminalState.AUDIT_REQUIRED,
        audit_reason="system_error",
    )


# ---------------------------------------------------------------------------
# Note coaching
# ---------------------------------------------------------------------------


def _starter_note_for_category(category: ReviewCategory, item: PipelineItem) -> str:
    """Generate a category-specific starter note prefilled into the Prompt.ask
    default. The reviewer can accept-as-is or edit before submitting.

    The starter mentions concrete artefacts from the item (alternative source
    table names, top Layer B finding, hypothesis confidence, etc.) so it
    coaches the reviewer toward an actionable note.
    """
    a = item.answer
    qa = item.quality_report

    if category == ReviewCategory.SOURCE_WRONG:
        alts = a.source.alternatives_available if a.source else []
        if alts:
            return (
                f"Use alternative source {alts[0]} (from alternatives_available); "
                f"current source disagrees with canonical primary."
            )
        return "Current source is not the canonical primary for this metric."

    if category == ReviewCategory.ANSWER_WRONG:
        # Look for the primary value in metric_value to seed something concrete
        return (
            "The metric_value or breakdown looks off because [explain what specifically]. "
            "Re-derive from the primary source with a corrected aggregation/filter."
        )

    if category == ReviewCategory.QA_INSUFFICIENT:
        return (
            "QA missed [check name]. Re-run with [specific check or threshold] before approval."
        )

    if category == ReviewCategory.EXTERNAL_DISAGREEMENT:
        # If Layer B has a hypothesis, mention it
        if qa.layer_b.hypothesis is not None:
            return (
                f"Disagrees with [external source]. Layer B hypothesis "
                f"({qa.layer_b.hypothesis.confidence}) does not resolve the dispute. "
                f"Audit needed."
            )
        return "Disagrees with [external source / dashboard / human-known truth]. Audit needed."

    return ""


# ---------------------------------------------------------------------------
# Reinvestigation diff (rendered post-pipeline from cli.py)
# ---------------------------------------------------------------------------


def render_reinvestigation_diffs(items: list[PipelineItem]) -> None:
    """For every item that was reinvestigated, render a side-by-side diff
    panel showing what changed between the original answer and the
    reinvestigated answer. Called after the pipeline completes."""
    diffs = [item for item in items if item.reinvestigated_answer is not None]
    if not diffs:
        return

    console.print()
    console.print(
        Text("Reinvestigation diffs", style="bold underline blue")
    )
    for item in diffs:
        _render_one_diff(item)


def _render_one_diff(item: PipelineItem) -> None:
    orig = item.answer
    new = item.reinvestigated_answer
    if new is None:
        return
    new_qa = item.reinvestigated_quality_report

    table = Table(
        title=f"{item.question.id} — original vs reinvestigated",
        expand=True,
        show_lines=True,
    )
    table.add_column("Field")
    table.add_column("Original")
    table.add_column("Reinvestigated")

    rows: list[tuple[str, str, str]] = [
        ("source.primary_table",
         orig.source.primary_table if orig.source else "(none)",
         new.source.primary_table if new.source else "(none)"),
        ("metric_value",
         _pretty_metric_value(orig.metric_value, max_chars=120),
         _pretty_metric_value(new.metric_value, max_chars=120)),
        ("trust profile",
         item.quality_report.layer_c.trust_profile.overall,
         new_qa.layer_c.trust_profile.overall if new_qa else "(no QA)"),
        ("sql (first line)",
         orig.sql.splitlines()[0] if orig.sql else "(empty)",
         new.sql.splitlines()[0] if new.sql else "(empty)"),
    ]
    for field, lhs, rhs in rows:
        if lhs != rhs:
            table.add_row(
                field,
                Text(lhs, style="dim"),
                Text(rhs, style="bold yellow"),
            )
        else:
            table.add_row(field, Text(lhs, style="dim"), Text(rhs, style="dim"))
    console.print(table)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# R6: Key Facts header + Derivation panel + pretty SQL
# ---------------------------------------------------------------------------


def _render_key_facts(item: PipelineItem) -> None:
    """A single dense table at the top of the panel summarising the basics —
    time window, source, grain, filters, aggregator. Computed from the
    DerivationTrace when present (planner-derived) and falls back to the
    QuestionPlan + answer fields otherwise.

    Design intent: reviewer should know "what was queried" within 5 seconds,
    without reading the SQL or the QA report."""

    table = Table(
        title=f"Key facts — {item.question.id}",
        show_header=False,
        expand=True,
        title_style="bold",
    )
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value", overflow="fold")

    plan = item.question_plan
    trace = item.answer.derivation_trace
    a = item.answer

    # Time window
    window = "(unknown)"
    if plan is not None:
        window = f"{plan.period.start} → {plan.period.end}"
    elif a.period:
        window = a.period
    table.add_row("Time window", window)

    # Source
    source = a.source.primary_table if a.source else (
        trace.chosen_source if trace else (plan.primary_source.table if plan else "(unknown)")
    )
    table.add_row("Source", source)

    # Grain
    grain_str = "(unknown)"
    if trace is not None and trace.required_grain.dimensions:
        grain_str = "(" + ", ".join(trace.required_grain.dimensions) + ")"
    elif plan is not None and plan.breakdown is not None:
        grain_str = plan.breakdown.dimension
    table.add_row("Grain", grain_str)

    # Filters
    filters_str: str
    if trace is not None and trace.chosen_filters:
        filters_str = "; ".join(trace.chosen_filters)
    elif isinstance(a.filters, list) and a.filters:
        filters_str = "; ".join(str(f) for f in a.filters)
    elif isinstance(a.filters, dict) and a.filters:
        filters_str = "; ".join(f"{k}={v}" for k, v in a.filters.items())
    else:
        filters_str = "(none declared)"
    table.add_row("Filters", filters_str)

    # Aggregator
    agg_str = "(unknown)"
    if trace is not None:
        agg_str = f"{trace.chosen_aggregator} — {trace.aggregator_rationale}"
    elif plan is not None:
        agg_str = f"{plan.primary_source.aggregator}({plan.primary_source.column})"
    table.add_row("Aggregator", agg_str)

    # Answer shape (helpful one-word context for what the reviewer should expect)
    if plan is not None:
        table.add_row("Answer shape", plan.answer_shape)

    console.print(table)


def _render_derivation_panel(trace: DerivationTrace) -> None:
    """Render the derivation trace as a table of candidate sources with the
    one selected highlighted and each rejection_reason shown verbatim.
    Replaces the LLM-narrative why_chosen with structured evidence."""

    table = Table(
        title="Derivation — candidates considered",
        expand=True,
        show_lines=False,
    )
    table.add_column("Table", style="bold")
    table.add_column("Grain", overflow="fold")
    table.add_column("Match")
    table.add_column("Scope feasibility", overflow="fold")
    table.add_column("Selected", justify="center")
    table.add_column("Rejection reason", overflow="fold")

    for c in trace.candidate_sources:
        grain = "(" + ", ".join(c.grain.dimensions) + ")"
        match_style = {
            "exact": "green",
            "rollup_needed": "yellow",
            "too_coarse": "red",
            "incompatible": "red",
        }.get(c.grain_match, "")
        feasibility_lines = []
        for predicate, value in c.scope_feasibility.items():
            mark = "ok  " if value.startswith("feasible_via") else "no  "
            feasibility_lines.append(f"{mark} {predicate}: {value}")
        feasibility_str = "\n".join(feasibility_lines) if feasibility_lines else "(none)"
        selected_cell = (
            Text("selected", style="bold green") if c.selected else Text("-", style="dim")
        )
        rejection = c.rejection_reason or ""
        table.add_row(
            c.table,
            grain,
            Text(c.grain_match, style=match_style),
            feasibility_str,
            selected_cell,
            rejection,
        )

    console.print(table)

    # Structured why_chosen panel — key:value layout instead of a wall of
    # italic prose. Same information as `trace.rendered_why_chosen` but
    # scannable in <5 seconds (R9 UX polish).
    chosen_candidate = next(
        (c for c in trace.candidate_sources if c.selected), None
    )
    rejected = [c for c in trace.candidate_sources if not c.selected]
    why_table = Table(show_header=False, expand=True, box=None)
    why_table.add_column("Field", style="bold cyan", no_wrap=True)
    why_table.add_column("Value", overflow="fold")
    why_table.add_row(
        "Required grain",
        "(" + ", ".join(trace.required_grain.dimensions) + ")"
        if trace.required_grain.dimensions
        else "(no grain — single-row)",
    )
    why_table.add_row(
        "Scope predicates",
        "; ".join(trace.scope_predicates) if trace.scope_predicates else "(none)",
    )
    why_table.add_row(
        "Chosen source",
        Text(trace.chosen_source, style="bold green"),
    )
    if chosen_candidate is not None:
        grain_match_style = {
            "exact": "green",
            "rollup_needed": "yellow",
            "too_coarse": "red",
            "incompatible": "red",
        }.get(chosen_candidate.grain_match, "")
        why_table.add_row(
            "  grain match",
            Text(chosen_candidate.grain_match, style=grain_match_style),
        )
        feasibility_lines = []
        for predicate, value in chosen_candidate.scope_feasibility.items():
            mark = "ok  " if value.startswith("feasible_via") else "no  "
            feasibility_lines.append(f"{mark} {predicate}: {value}")
        if feasibility_lines:
            why_table.add_row(
                "  scope feasibility",
                "\n".join(feasibility_lines),
            )
    if rejected:
        why_table.add_row(
            "Rejected candidates",
            "; ".join(f"{c.table} ({c.rejection_reason})" for c in rejected),
        )
    why_table.add_row(
        "Aggregator",
        f"{trace.chosen_aggregator} — {trace.aggregator_rationale}",
    )
    why_table.add_row(
        "Filters",
        "\n".join(trace.chosen_filters) if trace.chosen_filters else "(none)",
    )
    console.print(
        Panel(
            why_table,
            title="why_chosen (planner-derived)",
            border_style="cyan",
        )
    )


def _pretty_sql(sql: str) -> str:
    """Pretty-format SQL via sqlglot. Falls back to the raw string on parse
    error so we never break the panel just because the model emitted unusual
    syntax."""
    try:
        import sqlglot
        return sqlglot.transpile(sql, read="sqlite", write="sqlite", pretty=True)[0]
    except Exception:
        return sql


def _pretty_metric_value(v: object, max_chars: int = 600) -> str:
    """Compact str of metric_value for panels — JSON for dict/list, str for scalars."""
    import json

    try:
        s = json.dumps(v, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(v)
    if len(s) > max_chars:
        s = s[: max_chars - 3] + "..."
    return s
