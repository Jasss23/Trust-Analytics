"""Question planner and planner QA gate.

R6 — two-phase hybrid planner:

  Phase 1 (skeleton, deterministic): registry lookup or text heuristics
  produce a typed `QuestionPlan` that pins answer_shape, primary_source,
  required_output_columns. This phase is unchanged from R5 / Codex's
  planner-validated commit.

  Phase 2 (trace, LLM-proposed + validator-gated): the LLM proposes a
  `DerivationTrace` from the schema + registry + skeleton plan. The trace
  defends the source choice: required grain, scope predicates, candidate
  sources with grain_match + scope_feasibility, chosen source/filters/
  aggregator. A deterministic validator structurally checks the trace
  against the dbt metadata (real grains, real columns) and the skeleton
  plan (chosen source must match the plan's primary unless source_policy
  permits divergence). Validation failure surfaces as a SystemError; the
  caller may retry (planner has a small internal budget) or escalate.

  Phase 3 (render, deterministic): compute `why_chosen` text from the
  validated trace via a template — no free-form LLM authorship survives
  into the answer.

The point: `why_chosen` is feasible-how, not LLM-narrative. The SQL Agent
receives the trace, conforms its SQL to it, and never authors `why_chosen`
again.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from trust_analytics.llm import LLMClient, LLMError, LLMOutputError
from trust_analytics.metadata import DbtMetadata, describe_schema_context
from trust_analytics.metrics import MetricsRegistry, SourceSpec
from trust_analytics.models import (
    BusinessQuestion,
    DerivationTrace,
    PlanBreakdown,
    PlanPeriod,
    PlanSource,
    QuestionPlan,
    SQLAgentAnswer,
    SystemError,
)

_PROMPTS_DIR = Path(__file__).parents[2] / "prompts"
_TRACE_BUDGET = 1  # one retry on validation failure (planner's own budget)
_REVISE_BUDGET = 1


def _load_trace_prompt() -> str:
    return (_PROMPTS_DIR / "planner_trace.md").read_text(encoding="utf-8")


def _load_revise_prompt() -> str:
    return (_PROMPTS_DIR / "planner_revise.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class PlannerResult:
    plan: QuestionPlan | None
    derivation_trace: DerivationTrace | None = None
    system_error: SystemError | None = None


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def plan_question(
    question: BusinessQuestion,
    registry: MetricsRegistry,
    metadata: DbtMetadata,
    reviewer_note: str | None = None,
    llm_client: LLMClient | None = None,
) -> PlannerResult:
    """Phase 1+2+3: skeleton plan → LLM trace → validator → rendered why_chosen.

    `llm_client` is optional. When None, only the skeleton plan is produced
    (legacy R5 behaviour; the SQL Agent falls back to authoring `why_chosen`).
    When set, the planner additionally proposes and validates a
    `DerivationTrace` and a deterministically rendered `why_chosen`.
    """
    entry = registry.get(question.id)
    if entry is not None:
        plan = _plan_from_registry(question, entry, reviewer_note)
    else:
        plan = _plan_from_question(question)

    issues = validate_question_plan(plan, question, metadata, registry)
    if issues:
        return PlannerResult(
            plan=None,
            system_error=SystemError(
                error_class="planner_validation_failed",
                message="; ".join(issues[:5]),
                suggested_action=(
                    "Review the generated question plan. The SQL Agent was not "
                    "called because the plan failed deterministic validation."
                ),
                raw=plan.model_dump_json(),
            ),
        )

    if llm_client is None:
        # Legacy path: skeleton only. SQL Agent will author why_chosen.
        return PlannerResult(plan=plan)

    # Phase 2 + 3: trace + render
    trace_result = _propose_and_validate_trace(
        question=question,
        plan=plan,
        registry=registry,
        metadata=metadata,
        llm_client=llm_client,
    )
    if trace_result.system_error is not None:
        return PlannerResult(plan=plan, system_error=trace_result.system_error)
    return PlannerResult(plan=plan, derivation_trace=trace_result.trace)


@dataclass(frozen=True)
class _TraceResult:
    trace: DerivationTrace | None
    system_error: SystemError | None = None


def _propose_and_validate_trace(
    question: BusinessQuestion,
    plan: QuestionPlan,
    registry: MetricsRegistry,
    metadata: DbtMetadata,
    llm_client: LLMClient,
) -> _TraceResult:
    """Run up to 1 + _TRACE_BUDGET LLM calls to produce a valid trace.

    The planner has its own small retry budget here so that a single hallu-
    cinated grain doesn't fail the whole question — but we don't let it
    spiral. Exhaustion returns a SystemError; the workflow then routes the
    question to AUDIT_REQUIRED.
    """
    system_prompt = _load_trace_prompt()
    correction: str | None = None
    last_error_detail: str = ""
    for attempt in range(_TRACE_BUDGET + 1):
        user_prompt = _render_trace_user_prompt(question, plan, registry, metadata, correction)
        stage = (
            f"planner_trace:{question.id}"
            if attempt == 0
            else f"planner_trace_retry:{question.id}"
        )
        try:
            response = llm_client.chat_json(system_prompt, user_prompt, stage_tag=stage)
        except LLMError as exc:
            return _TraceResult(
                trace=None,
                system_error=SystemError(
                    error_class=getattr(exc, "error_class", "llm_error"),  # type: ignore[arg-type]
                    message=f"Planner trace call failed: {exc}",
                    suggested_action=(
                        "Investigate the LLM call before retrying. If quota / "
                        "auth, fix the credentials. If transient, re-run."
                    ),
                    raw=type(exc).__name__,
                ),
            )

        try:
            payload = _loads_json_object(response.content)
            trace = DerivationTrace.model_validate(payload)
        except (json.JSONDecodeError, ValueError, LLMOutputError) as exc:
            last_error_detail = f"trace_parse_error: {exc!s}"
            correction = (
                f"Your previous output failed to parse as a DerivationTrace: "
                f"{last_error_detail}. Return a valid JSON object exactly "
                f"matching the contract."
            )
            continue

        issues = validate_derivation_trace(trace, plan, metadata)
        if not issues:
            return _TraceResult(trace=trace)
        last_error_detail = "; ".join(issues[:6])
        correction = (
            f"Your previous trace failed validation: {last_error_detail}. "
            "Address each issue exactly. Do NOT re-emit the same trace."
        )

    return _TraceResult(
        trace=None,
        system_error=SystemError(
            error_class="trace_validation_failed",
            message=f"Trace validator rejected proposals after {_TRACE_BUDGET + 1} "
            f"attempt(s). Last failure: {last_error_detail}",
            suggested_action=(
                "Inspect the planner_trace prompt and the candidate the LLM "
                "produced. The trace validator caught structural violations "
                "(grain mismatch / missing rejection_reason / chosen source "
                "not in candidates / scope predicate uncovered)."
            ),
            raw=last_error_detail[:500],
        ),
    )


def _render_trace_user_prompt(
    question: BusinessQuestion,
    plan: QuestionPlan,
    registry: MetricsRegistry,
    metadata: DbtMetadata,
    correction: str | None,
) -> str:
    schema_ctx = describe_schema_context(metadata)
    entry = registry.get(question.id)
    registry_block = (
        _render_registry_for_trace(entry)
        if entry is not None
        else "(no registry entry — derive candidates from schema context only)"
    )
    plan_block = plan.model_dump_json(indent=2)
    correction_block = ""
    if correction:
        correction_block = (
            "\n\n## Correction\nYour previous attempt failed. Address "
            f"specifically:\n{correction}\n"
        )
    return (
        "## Schema context\n"
        f"{schema_ctx}\n\n"
        "## Metric registry entry\n"
        f"{registry_block}\n\n"
        "## Skeleton plan (from deterministic phase 1)\n"
        f"```json\n{plan_block}\n```\n\n"
        "## Business question\n"
        f"{question.text}\n\n"
        f"Question id: {question.id}\n"
        f"Metric: {question.metric}\n"
        f"Period: {question.period}\n"
        f"{correction_block}\n"
        "Return the DerivationTrace JSON now."
    )


def _render_registry_for_trace(entry) -> str:
    """Lightweight registry rendering for the trace prompt — same shape as the
    SQL Agent prompt's registry block but standalone to avoid coupling."""

    def fmt_source(label: str, src: SourceSpec) -> list[str]:
        return [
            f"  {label}: table={src.table} column={src.column} "
            f"period_column={src.period_column} aggregator={src.aggregator} "
            f"extra_filters={list(src.extra_filters)}",
        ]

    lines = [
        f"id: {entry.id}",
        f"metric_name: {entry.metric_name}",
        f"cross_source: {entry.cross_source}",
        f"period: {entry.period_start} → {entry.period_end}",
        "sources:",
    ]
    lines.extend(fmt_source("primary", entry.primary))
    for i, alt in enumerate(entry.alternatives):
        lines.extend(fmt_source(f"alt_{i}", alt))
    if entry.notes_for_layer_b:
        lines.append(f"notes_for_layer_b: {entry.notes_for_layer_b}")
    return "\n".join(lines)


def _loads_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise LLMOutputError(
                f"No JSON object found in trace LLM output: {text[:300]}"
            ) from None
        return json.loads(match.group(0))


# ---------------------------------------------------------------------------
# Trace validator + renderer (deterministic) — R6
# ---------------------------------------------------------------------------


def validate_derivation_trace(
    trace: DerivationTrace,
    plan: QuestionPlan,
    metadata: DbtMetadata,
) -> list[str]:
    """Structural checks on a proposed DerivationTrace.

    Pure structural verification — no semantic interpretation. Catches:
    - chosen_source must be a candidate marked selected=True
    - exactly one candidate selected
    - non-selected candidates must have a non-empty rejection_reason
    - chosen candidate's scope_feasibility must cover every scope_predicate
      with a `feasible_via=...` value
    - candidate tables and chosen_source must exist in dbt metadata
    - chosen_filters must reference columns that exist on chosen_source
    - aggregator_rationale must reference column grain (mention "grain",
      "per", "row", "daily", "monthly", etc.) — keyword check, not vibes
    - rendered_why_chosen non-empty, references chosen_source
    - chosen_source must match plan.primary_source.table when
      source_policy='canonical'

    Plan-driven, not question-id-driven. The list of issues is returned;
    empty list means valid.
    """
    issues: list[str] = []
    table_columns = _table_columns(metadata)

    # Selection sanity
    selected_count = sum(1 for c in trace.candidate_sources if c.selected)
    if selected_count == 0:
        issues.append("no candidate has selected=true")
    if selected_count > 1:
        issues.append(f"more than one candidate selected ({selected_count})")
    if trace.chosen_source not in {c.table for c in trace.candidate_sources}:
        issues.append(
            f"chosen_source ({trace.chosen_source!r}) is not among "
            f"candidate_sources tables"
        )
    selected_candidates = [c for c in trace.candidate_sources if c.selected]
    if selected_candidates and selected_candidates[0].table != trace.chosen_source:
        issues.append(
            f"the selected=true candidate ({selected_candidates[0].table!r}) "
            f"differs from chosen_source ({trace.chosen_source!r})"
        )

    # Non-selected must have rejection_reason
    for c in trace.candidate_sources:
        if not c.selected and not (c.rejection_reason or "").strip():
            issues.append(
                f"non-selected candidate {c.table!r} missing rejection_reason"
            )

    # Candidate tables exist in dbt metadata
    for c in trace.candidate_sources:
        if c.table not in table_columns:
            issues.append(
                f"candidate table {c.table!r} not found in dbt metadata"
            )

    # Chosen candidate's scope_feasibility covers all scope_predicates feasibly
    if selected_candidates:
        chosen = selected_candidates[0]
        for predicate in trace.scope_predicates:
            value = chosen.scope_feasibility.get(predicate)
            if value is None:
                issues.append(
                    f"chosen candidate missing scope_feasibility entry for "
                    f"predicate {predicate!r}"
                )
            elif not value.strip().startswith("feasible_via"):
                issues.append(
                    f"chosen candidate scope predicate {predicate!r} is not "
                    f"feasible: {value}"
                )

    # Every scope_predicate must appear in at least one candidate's dict
    if trace.scope_predicates:
        coverage_union: set[str] = set()
        for c in trace.candidate_sources:
            coverage_union.update(c.scope_feasibility.keys())
        for p in trace.scope_predicates:
            if p not in coverage_union:
                issues.append(
                    f"scope predicate {p!r} not covered by any candidate's "
                    f"scope_feasibility dict"
                )

    # Filters reference real columns on chosen_source
    chosen_cols = table_columns.get(trace.chosen_source, set())
    for f in trace.chosen_filters:
        # Heuristic: extract leading identifier of each filter clause.
        m = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_]*)", f)
        if m is None:
            issues.append(f"chosen_filter {f!r} has no leading identifier")
            continue
        col = m.group(1)
        if col not in chosen_cols and chosen_cols:
            issues.append(
                f"chosen_filter references column {col!r} not present on "
                f"{trace.chosen_source!r}"
            )

    # Aggregator rationale references grain (keyword family — not "vibes")
    rationale = trace.aggregator_rationale.lower()
    grain_tokens = ("grain", "per ", "per-", "row", "daily", "monthly", "snapshot")
    if not any(tok in rationale for tok in grain_tokens):
        issues.append(
            "aggregator_rationale does not reference column grain "
            "(must mention grain/per/row/daily/monthly/snapshot)"
        )

    # rendered_why_chosen non-empty + references chosen_source
    if not trace.rendered_why_chosen.strip():
        issues.append("rendered_why_chosen is empty")
    elif trace.chosen_source not in trace.rendered_why_chosen:
        issues.append(
            f"rendered_why_chosen does not mention chosen_source "
            f"({trace.chosen_source!r})"
        )

    # Plan source_policy='canonical' → chosen_source must match plan primary
    if plan.source_policy == "canonical":
        if trace.chosen_source != plan.primary_source.table:
            issues.append(
                f"source_policy=canonical requires chosen_source == "
                f"plan.primary_source.table ({plan.primary_source.table!r}); "
                f"got {trace.chosen_source!r}"
            )

    return issues


def render_why_chosen(trace: DerivationTrace) -> str:
    """Deterministic renderer — produces a substantive process-trace sentence
    from the validated trace. Used when the trace is the source of truth and
    we want `source.why_chosen` to be machine-derived, not LLM-narrative.

    Note: the LLM also produces `trace.rendered_why_chosen` (the model's own
    rendering). We prefer the validator-derived rendering here so the
    contract is stable regardless of model behaviour."""
    grain_str = "(" + ", ".join(trace.required_grain.dimensions) + ")"
    chosen = next(
        (c for c in trace.candidate_sources if c.selected and c.table == trace.chosen_source),
        None,
    )
    chosen_grain = (
        "(" + ", ".join(chosen.grain.dimensions) + ")" if chosen else "?"
    )
    rejected = [
        f"{c.table} ({c.rejection_reason or 'no reason given'})"
        for c in trace.candidate_sources
        if not c.selected
    ]
    rejected_str = "; ".join(rejected) if rejected else "no alternatives considered"
    filters_str = "; ".join(trace.chosen_filters) if trace.chosen_filters else "(no filters)"
    return (
        f"Picked {trace.chosen_source} because grain {chosen_grain} matches "
        f"required {grain_str}, and every scope predicate "
        f"({', '.join(trace.scope_predicates) or 'none'}) is feasible on it. "
        f"Rejected: {rejected_str}. Filters: {filters_str}. "
        f"Aggregator: {trace.chosen_aggregator} ({trace.aggregator_rationale})."
    )


def apply_trace_to_answer(answer, trace: DerivationTrace) -> None:
    """Overwrite the LLM-authored `source` + `why_chosen` with the planner-
    derived values from the trace. Idempotent. Mutates in place.

    `answer.source.alternatives_available` keeps the trace's full candidate
    list (minus chosen) so reviewers see what was considered.
    """
    from trust_analytics.models import SourceProvenance  # local import to avoid cycle

    alternatives = [
        c.table for c in trace.candidate_sources if c.table != trace.chosen_source
    ]
    rendered = render_why_chosen(trace)
    answer.source = SourceProvenance(
        primary_table=trace.chosen_source,
        why_chosen=rendered,
        alternatives_available=alternatives,
    )
    answer.derivation_trace = trace.model_copy(update={"rendered_why_chosen": rendered})


def validate_question_plan(
    plan: QuestionPlan,
    question: BusinessQuestion,
    metadata: DbtMetadata,
    registry: MetricsRegistry,
) -> list[str]:
    issues: list[str] = []
    if plan.question_id != question.id:
        issues.append("plan question_id does not match input question")
    if not _valid_date(plan.period.start) or not _valid_date(plan.period.end):
        issues.append("plan period must use concrete YYYY-MM-DD bounds")
    if plan.period.start >= plan.period.end:
        issues.append("plan period start must be before end")

    table_columns = _table_columns(metadata)
    for label, src in [("primary_source", plan.primary_source), *[
        (f"comparison_sources[{i}]", src) for i, src in enumerate(plan.comparison_sources)
    ]]:
        cols = table_columns.get(src.table)
        if cols is None:
            issues.append(f"{label}.table not found in metadata: {src.table}")
            continue
        if src.column not in cols:
            issues.append(f"{label}.column not found in {src.table}: {src.column}")
        if src.period_column not in cols:
            issues.append(f"{label}.period_column not found in {src.table}: {src.period_column}")
        if plan.breakdown is not None and src.table == plan.primary_source.table:
            if plan.breakdown.dimension not in cols:
                issues.append(
                    f"breakdown.dimension not found in {src.table}: {plan.breakdown.dimension}"
                )

    text = question.text.lower()
    if _asks_breakdown(text) and plan.answer_shape not in {"breakdown", "breakdown_comparison"}:
        issues.append("question asks for a breakdown but plan answer_shape is not breakdown-like")
    if _asks_comparison(text) and not plan.comparison_sources:
        issues.append("question asks for comparison but plan has no comparison source")
    if _asks_period_over_period(text) and plan.answer_shape != "period_over_period":
        issues.append("question asks for trend/MoM but plan is not period_over_period")

    required = set(plan.required_output_columns)
    if plan.answer_shape == "multi_definition":
        if len(plan.required_definitions) < 2:
            issues.append("multi_definition plan must require at least two definitions")
        if not set(plan.required_definitions).issubset(required):
            issues.append("multi_definition required definitions must be output columns")
    if plan.answer_shape == "period_over_period":
        for col in ("mom_change_idr", "mom_change_pct"):
            if col not in required:
                issues.append(f"period_over_period missing required output column {col}")
    if plan.answer_shape == "breakdown_comparison":
        for col in ("absolute_delta_idr", "delta_pct"):
            if col not in required:
                issues.append(f"breakdown_comparison missing required output column {col}")
        if not plan.comparison_sources:
            issues.append("breakdown_comparison requires a comparison source")
    if plan.breakdown is not None and "Total" in _aggregate_members_for_table(plan.primary_source.table):
        if "Total" not in plan.breakdown.exclude_aggregate_members:
            issues.append("breakdown over table with Total row must exclude Total")

    entry = registry.get(question.id)
    if entry is not None and plan.source_policy == "canonical":
        if plan.primary_source.table != entry.primary.table:
            issues.append("canonical plan primary source must match registry primary source")

    return issues


def _plan_from_registry(
    question: BusinessQuestion,
    entry,
    reviewer_note: str | None,
) -> QuestionPlan:
    text = " ".join([question.text.lower(), reviewer_note.lower() if reviewer_note else ""])
    primary = _source_from_spec(entry.primary, "Registry canonical primary source.")
    comparisons = [_source_from_spec(src, src.notes or "Registry alternative source.") for src in entry.alternatives]
    breakdown = _breakdown_from_source(entry.primary)

    if _is_multi_definition_entry(entry):
        required_definitions = [
            "aum_defined_mtu",
            "raw_completed_unique_traders",
            "mixpanel_mtu",
        ]
        return QuestionPlan(
            question_id=question.id,
            metric_intent=entry.metric_name,
            period=PlanPeriod(start=entry.period_start, end=entry.period_end),
            answer_shape="multi_definition",
            primary_source=primary,
            comparison_sources=comparisons,
            breakdown=None,
            required_output_columns=required_definitions,
            required_definitions=required_definitions,
            ambiguity_policy="return_all_definitions",
            source_policy="canonical_with_definitional_alternatives",
            validation_rules=[
                "return one value for each required definition",
                "metric_value must be derived from executed SQL rows",
            ],
        )

    if _asks_period_over_period(text):
        value_col = entry.primary.column
        return QuestionPlan(
            question_id=question.id,
            metric_intent=entry.metric_name,
            period=PlanPeriod(start=entry.period_start, end=entry.period_end),
            answer_shape="period_over_period",
            primary_source=primary,
            comparison_sources=comparisons,
            breakdown=None,
            required_output_columns=["month", value_col, "mom_change_idr", "mom_change_pct"],
            source_policy="canonical",
            validation_rules=[
                "return one row per period bucket",
                "include absolute and percent period-over-period change",
                "metric_value must be derived from executed SQL rows",
            ],
        )

    if breakdown is not None:
        return QuestionPlan(
            question_id=question.id,
            metric_intent=entry.metric_name,
            period=PlanPeriod(start=entry.period_start, end=entry.period_end),
            answer_shape="breakdown",
            primary_source=primary,
            comparison_sources=comparisons,
            breakdown=breakdown,
            required_output_columns=[breakdown.dimension, entry.primary.column],
            source_policy="canonical",
            validation_rules=[
                "do not include aggregate members in breakdown output",
                "metric_value must be derived from executed SQL rows",
            ],
        )

    return QuestionPlan(
        question_id=question.id,
        metric_intent=entry.metric_name,
        period=PlanPeriod(start=entry.period_start, end=entry.period_end),
        answer_shape="scalar",
        primary_source=primary,
        comparison_sources=comparisons,
        required_output_columns=[entry.primary.column],
        source_policy="canonical",
        validation_rules=["metric_value must be derived from executed SQL rows"],
    )


def _plan_from_question(question: BusinessQuestion) -> QuestionPlan:
    text = question.text.lower()
    period = infer_period_from_text(text) or PlanPeriod(start="2025-10-01", end="2025-11-01")
    metric = infer_metric_intent(text, question.metric)
    requested_ops = "ops" in text or "dashboard" in text
    breakdown = PlanBreakdown(
        dimension="asset_class",
        exclude_aggregate_members=["Total"] if requested_ops else [],
    ) if _asks_breakdown(text) else None

    primary = _heuristic_source(metric, requested_ops)
    comparisons: list[PlanSource] = []
    if _asks_comparison(text):
        comparisons.append(_heuristic_source(metric, requested_ops=False))

    shape = "scalar"
    if breakdown and comparisons:
        shape = "breakdown_comparison"
    elif breakdown:
        shape = "breakdown"
    elif _asks_period_over_period(text):
        shape = "period_over_period"

    required = _required_columns(shape, metric, primary, breakdown)
    return QuestionPlan(
        question_id=question.id,
        metric_intent=metric,
        period=period,
        answer_shape=shape,  # type: ignore[arg-type]
        primary_source=primary,
        comparison_sources=comparisons,
        breakdown=breakdown,
        required_output_columns=required,
        source_policy="user_requested_noncanonical" if requested_ops else "schema_grounded",
        validation_rules=[
            "do not include aggregate members in breakdown output",
            "metric_value must be derived from executed SQL rows",
        ],
    )


def _source_from_spec(src: SourceSpec, reason: str) -> PlanSource:
    return PlanSource(
        table=src.table,
        column=src.column,
        period_column=src.period_column,
        aggregator=src.aggregator,
        extra_filters=list(src.extra_filters),
        reason=reason,
    )


def _breakdown_from_source(src: SourceSpec) -> PlanBreakdown | None:
    if src.breakdown is None or src.breakdown == "month_bucket":
        return None
    return PlanBreakdown(
        dimension=src.breakdown,
        exclude_aggregate_members=_aggregate_members_for_table(src.table),
    )


def _aggregate_members_for_table(table: str) -> list[str]:
    if table in {"agg_monthly_biz_summary", "mart_ops_dashboard"}:
        return ["Total"]
    return []


def _is_multi_definition_entry(entry) -> bool:
    note = (entry.notes_for_layer_b or "").lower()
    return "three definitions" in note or "multiple valid definitions" in note


def _asks_breakdown(text: str) -> bool:
    return " by " in text or "across " in text or "breakdown" in text


def _asks_comparison(text: str) -> bool:
    return any(token in text for token in ("compare", "compared", "versus", " vs ", "difference"))


def _asks_period_over_period(text: str) -> bool:
    return any(token in text for token in ("month-on-month", "mom", "trend", "period-over-period"))


def infer_metric_intent(text: str, fallback: str = "adhoc_metric") -> str:
    """Heuristic mapping from a question's text to a metric name.

    Public since R7 because `questions.synthesize_business_question` reuses
    this for ad-hoc questions; the same heuristic that drives the planner's
    own `_plan_from_question` fallback drives metric inference for the
    `trust-analytics ask` CLI surface.
    """
    text = text.lower()
    if "gtv" in text and "usd" in text:
        return "gtv_usd"
    if "gtv" in text:
        return "gtv_idr"
    if "transaction" in text and "count" in text:
        return "transaction_count"
    if "mtu" in text or "monthly transacting" in text:
        return "monthly_transacting_users"
    return fallback


def _heuristic_source(metric: str, requested_ops: bool) -> PlanSource:
    if requested_ops:
        column = {
            "gtv_idr": "gtv_idr",
            "gtv_usd": "gtv_usd_reported",
            "transaction_count": "total_transactions",
            "monthly_transacting_users": "mtu_mixpanel",
        }.get(metric, "gtv_idr")
        return PlanSource(
            table="mart_ops_dashboard",
            column=column,
            period_column="month",
            aggregator="SUM",
            extra_filters=[],
            reason="User explicitly requested Ops dashboard definition.",
        )
    column = {
        "gtv_idr": "gtv_idr",
        "gtv_usd": "gtv_usd",
        "transaction_count": "transaction_count",
    }.get(metric, "gtv_idr")
    return PlanSource(
        table="fct_trading_daily",
        column=column,
        period_column="transaction_date",
        aggregator="SUM",
        extra_filters=[],
        reason="Canonical completed-transaction source.",
    )


def _required_columns(
    shape: str,
    metric: str,
    primary: PlanSource,
    breakdown: PlanBreakdown | None,
) -> list[str]:
    value_col = primary.column if metric != "transaction_count" else "transaction_count"
    if shape == "breakdown_comparison":
        prefix = "ops" if primary.table == "mart_ops_dashboard" else "primary"
        return [
            breakdown.dimension if breakdown else "dimension",
            f"{prefix}_{value_col}",
            f"canonical_{value_col}",
            "absolute_delta_idr",
            "delta_pct",
        ]
    if shape == "breakdown":
        return [breakdown.dimension if breakdown else "dimension", value_col]
    if shape == "period_over_period":
        return ["month", value_col, "mom_change_idr", "mom_change_pct"]
    return [value_col]


def infer_period_from_text(text: str) -> PlanPeriod | None:
    """Heuristic extraction of a (start, end) period from question text.

    Matches month-year pairs like "October 2025" or "October 2025 to December
    2025"; returns the inclusive-start, exclusive-end window covering the
    first to (next month of the) last match. Returns None when no month is
    found.

    Public since R7 because `questions.synthesize_business_question` reuses
    this for ad-hoc question construction.
    """
    text = text.lower()
    months = [(m, y) for m, y in re.findall(
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})",
        text,
    )]
    if not months:
        return None
    start_month, start_year = months[0]
    end_month, end_year = months[-1]
    start = date(int(start_year), MONTHS[start_month], 1)
    end = _next_month(date(int(end_year), MONTHS[end_month], 1))
    return PlanPeriod(start=start.isoformat(), end=end.isoformat())


def _next_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _table_columns(metadata: DbtMetadata) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for source_group in metadata.sources.get("sources", []):
        for table in source_group.get("tables", []):
            out[table["name"]] = {col["name"] for col in table.get("columns", [])}
    for model in metadata.models.get("models", []):
        out[model["name"]] = {col["name"] for col in model.get("columns", [])}
    return out


# ---------------------------------------------------------------------------
# R8: LLM-driven plan revision on reinvestigation
# ---------------------------------------------------------------------------


def replan_question(
    question: BusinessQuestion,
    previous_plan: QuestionPlan,
    previous_answer: SQLAgentAnswer,
    reviewer_note: str,
    registry: MetricsRegistry,
    metadata: DbtMetadata,
    llm_client: LLMClient,
) -> PlannerResult:
    """Revise a plan with reviewer feedback (LLM) + re-derive its trace.

    Used by the workflow's `reinvestigate_rejections` node for
    `answer_wrong` / `source_wrong` categories. The reviewer note may
    require a plan change (different metric, aggregator, source, shape);
    the LLM proposes the revision; the existing deterministic validator
    gates it; the trace phase re-runs against the new plan.

    Returns:
        PlannerResult with revised plan + new derivation_trace on success,
        or `system_error` if revision / validation / trace fails.
    """
    revised_result = _propose_and_validate_revision(
        question=question,
        previous_plan=previous_plan,
        previous_answer=previous_answer,
        reviewer_note=reviewer_note,
        registry=registry,
        metadata=metadata,
        llm_client=llm_client,
    )
    if revised_result.system_error is not None:
        return PlannerResult(plan=None, system_error=revised_result.system_error)

    revised_plan = revised_result.plan
    assert revised_plan is not None  # validator passed → plan is non-None

    # Re-derive a fresh trace against the revised plan. Trace is plan-
    # specific (it defends the plan's source/aggregator/grain choices);
    # using the stale trace would defeat the whole point of revision.
    trace_result = _propose_and_validate_trace(
        question=question,
        plan=revised_plan,
        registry=registry,
        metadata=metadata,
        llm_client=llm_client,
    )
    if trace_result.system_error is not None:
        return PlannerResult(plan=revised_plan, system_error=trace_result.system_error)
    return PlannerResult(plan=revised_plan, derivation_trace=trace_result.trace)


@dataclass(frozen=True)
class _ReviseResult:
    plan: QuestionPlan | None
    revision_note: str | None = None  # set when LLM emitted plan unchanged
    system_error: SystemError | None = None


def _propose_and_validate_revision(
    question: BusinessQuestion,
    previous_plan: QuestionPlan,
    previous_answer: SQLAgentAnswer,
    reviewer_note: str,
    registry: MetricsRegistry,
    metadata: DbtMetadata,
    llm_client: LLMClient,
) -> _ReviseResult:
    """LLM-revise the plan + run the deterministic validator. Up to
    _REVISE_BUDGET retries on validation failure."""
    system_prompt = _load_revise_prompt()
    correction: str | None = None
    last_error_detail = ""
    for attempt in range(_REVISE_BUDGET + 1):
        user_prompt = _render_revise_user_prompt(
            question, previous_plan, previous_answer, reviewer_note, registry, metadata, correction
        )
        stage = (
            f"planner_revise:{question.id}"
            if attempt == 0
            else f"planner_revise_retry:{question.id}"
        )
        try:
            response = llm_client.chat_json(system_prompt, user_prompt, stage_tag=stage)
        except LLMError as exc:
            return _ReviseResult(
                plan=None,
                system_error=SystemError(
                    error_class=getattr(exc, "error_class", "llm_error"),  # type: ignore[arg-type]
                    message=f"Plan revision LLM call failed: {exc}",
                    suggested_action="Investigate the LLM call. Auth/quota issues fail at this layer too.",
                    raw=type(exc).__name__,
                ),
            )

        try:
            payload = _loads_json_object(response.content)
        except (json.JSONDecodeError, LLMOutputError) as exc:
            last_error_detail = f"revise_parse_error: {exc!s}"
            correction = (
                f"Your previous output failed to parse as a QuestionPlan: "
                f"{last_error_detail}. Return a valid JSON object exactly "
                f"matching the contract."
            )
            continue

        revision_note = payload.pop("revision_note", None)
        try:
            revised_plan = QuestionPlan.model_validate(payload)
        except ValueError as exc:
            last_error_detail = f"revise_validation_error: {exc!s}"
            correction = (
                f"Your previous output failed Pydantic validation: "
                f"{last_error_detail}. Fix the structure."
            )
            continue

        # Idempotence safety: question_id must be preserved.
        if revised_plan.question_id != previous_plan.question_id:
            last_error_detail = (
                f"revised plan changed question_id ({previous_plan.question_id!r} → "
                f"{revised_plan.question_id!r}); question_id must be preserved."
            )
            correction = last_error_detail
            continue

        issues = validate_question_plan(revised_plan, question, metadata, registry)
        if not issues:
            return _ReviseResult(plan=revised_plan, revision_note=revision_note)
        last_error_detail = "; ".join(issues[:6])
        correction = (
            f"Your revised plan failed validation: {last_error_detail}. "
            "Address each issue. Only change fields the reviewer note implies "
            "should change."
        )

    return _ReviseResult(
        plan=None,
        system_error=SystemError(
            error_class="plan_revision_failed",
            message=(
                f"Plan revision validator rejected proposals after "
                f"{_REVISE_BUDGET + 1} attempt(s). Last failure: {last_error_detail}"
            ),
            suggested_action=(
                "Inspect the planner_revise prompt + the reviewer note. The "
                "revised plan failed structural validation (hallucinated "
                "table/column, malformed period, shape invariant violation, "
                "or question_id rewrite)."
            ),
            raw=last_error_detail[:500],
        ),
    )


def _render_revise_user_prompt(
    question: BusinessQuestion,
    previous_plan: QuestionPlan,
    previous_answer: SQLAgentAnswer,
    reviewer_note: str,
    registry: MetricsRegistry,
    metadata: DbtMetadata,
    correction: str | None,
) -> str:
    schema_ctx = describe_schema_context(metadata)
    entry = registry.get(question.id)
    registry_block = (
        _render_registry_for_trace(entry)
        if entry is not None
        else "(no registry entry for this question — revise from schema context only)"
    )
    plan_block = previous_plan.model_dump_json(indent=2)
    # Summarise the previous answer — full result_rows would be huge.
    rows_preview = previous_answer.result_rows[:3]
    answer_summary = {
        "chosen_source": previous_answer.source.primary_table if previous_answer.source else None,
        "sql": previous_answer.sql,
        "first_rows": rows_preview,
        "row_count": len(previous_answer.result_rows),
    }
    answer_block = json.dumps(answer_summary, indent=2, default=str)
    correction_block = ""
    if correction:
        correction_block = (
            "\n\n## Correction\nYour previous revision attempt failed. Address:\n"
            f"{correction}\n"
        )
    return (
        "## Schema context\n"
        f"{schema_ctx}\n\n"
        "## Metric registry entry\n"
        f"{registry_block}\n\n"
        "## Previous QuestionPlan\n"
        f"```json\n{plan_block}\n```\n\n"
        "## Previous SQL Agent answer (summary)\n"
        f"```json\n{answer_block}\n```\n\n"
        "## Business question\n"
        f"{question.text}\n\n"
        f"Question id: {question.id}\n"
        f"Inferred metric: {question.metric}\n"
        f"Inferred period: {question.period}\n\n"
        "## Reviewer rejection\n"
        f"Note: {reviewer_note}\n"
        f"{correction_block}\n"
        "Return the revised QuestionPlan JSON now."
    )
