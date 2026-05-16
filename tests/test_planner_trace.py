"""Trace validator tests (R6).

The validator is the deterministic gate between LLM-proposed DerivationTrace
and the SQL Agent. It must reject hallucinated grains, missing rejection
reasons, dangling references, and any structural malformation — without
requiring the question to be one of the case-study 5.

Tests are bi-directional (per R6 hard rule): every validation rule has a
trigger that fails AND an inverse scenario that passes.
"""

from __future__ import annotations

from trust_analytics.metadata import DbtMetadata
from trust_analytics.models import (
    CandidateSource,
    DerivationTrace,
    GrainSpec,
    PlanPeriod,
    PlanSource,
    QuestionPlan,
)
from trust_analytics.planner import (
    apply_trace_to_answer,
    render_why_chosen,
    validate_derivation_trace,
)


def _metadata() -> DbtMetadata:
    """Synthetic dbt metadata so tests are not coupled to the real YAMLs."""
    return DbtMetadata(
        sources={
            "sources": [
                {
                    "tables": [
                        {
                            "name": "raw_events",
                            "columns": [
                                {"name": "event_id"},
                                {"name": "value"},
                                {"name": "d"},
                            ],
                        },
                    ]
                }
            ]
        },
        models={
            "models": [
                {
                    "name": "fact_table",
                    "columns": [
                        {"name": "asset_class"},
                        {"name": "gtv_idr"},
                        {"name": "d"},
                    ],
                },
                {
                    "name": "alt_table",
                    "columns": [
                        {"name": "asset_class"},
                        {"name": "gtv_idr"},
                        {"name": "month"},
                    ],
                },
            ]
        },
    )


def _plan(primary_table: str = "fact_table", source_policy: str = "canonical") -> QuestionPlan:
    return QuestionPlan(
        question_id="q",
        metric_intent="gtv_idr",
        period=PlanPeriod(start="2025-10-01", end="2025-11-01"),
        answer_shape="scalar",
        primary_source=PlanSource(
            table=primary_table,
            column="gtv_idr",
            period_column="d",
            aggregator="SUM",
            reason="canonical",
        ),
        source_policy=source_policy,
    )


def _trace(
    chosen_source: str = "fact_table",
    candidates: list[CandidateSource] | None = None,
    chosen_filters: list[str] | None = None,
    rendered_why_chosen: str | None = None,
    aggregator_rationale: str = "SUM because gtv_idr is per-day grain.",
    scope_predicates: list[str] | None = None,
) -> DerivationTrace:
    cands = candidates if candidates is not None else [
        CandidateSource(
            table=chosen_source,
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=True,
        ),
    ]
    return DerivationTrace(
        required_grain=GrainSpec(dimensions=["d"]),
        scope_predicates=scope_predicates if scope_predicates is not None else ["oct_2025"],
        candidate_sources=cands,
        chosen_source=chosen_source,
        chosen_filters=chosen_filters or ["d >= '2025-10-01'"],
        chosen_aggregator="SUM",
        aggregator_rationale=aggregator_rationale,
        rendered_why_chosen=rendered_why_chosen or f"Picked {chosen_source} for the test.",
    )


# --- Well-formed trace passes (baseline inverse) ---

def test_well_formed_trace_passes() -> None:
    trace = _trace()
    issues = validate_derivation_trace(trace, _plan(), _metadata())
    assert issues == []


# --- No candidate selected: trigger + inverse ---

def test_no_candidate_selected_fails() -> None:
    trace = _trace(
        candidates=[
            CandidateSource(
                table="fact_table",
                grain=GrainSpec(dimensions=["d"]),
                grain_match="exact",
                scope_feasibility={"oct_2025": "feasible_via=d filter"},
                selected=False,
                rejection_reason="hand of god",
            ),
        ],
    )
    issues = validate_derivation_trace(trace, _plan(), _metadata())
    assert any("no candidate has selected=true" in i for i in issues)


def test_exactly_one_selected_passes() -> None:
    # Same as well_formed_trace_passes — covered above. Listed here for parity.
    trace = _trace()
    assert validate_derivation_trace(trace, _plan(), _metadata()) == []


# --- More than one selected: trigger + inverse ---

def test_multiple_candidates_selected_fails() -> None:
    trace = _trace(
        candidates=[
            CandidateSource(
                table="fact_table",
                grain=GrainSpec(dimensions=["d"]),
                grain_match="exact",
                scope_feasibility={"oct_2025": "feasible_via=d filter"},
                selected=True,
            ),
            CandidateSource(
                table="alt_table",
                grain=GrainSpec(dimensions=["d"]),
                grain_match="exact",
                scope_feasibility={"oct_2025": "feasible_via=d filter"},
                selected=True,
            ),
        ],
    )
    issues = validate_derivation_trace(trace, _plan(), _metadata())
    assert any("more than one candidate selected" in i for i in issues)


# --- chosen_source not among candidates: trigger + inverse ---

def test_chosen_source_not_in_candidates_fails() -> None:
    trace = _trace(chosen_source="ghost_table")
    # Candidates list still uses 'fact_table' from the helper default, so
    # chosen_source 'ghost_table' is not among them.
    candidates = [
        CandidateSource(
            table="fact_table",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=False,
            rejection_reason="manual override",
        ),
    ]
    trace = trace.model_copy(update={"candidate_sources": candidates})
    issues = validate_derivation_trace(trace, _plan(), _metadata())
    assert any("not among" in i for i in issues)


# --- Rejection reason missing on non-selected: trigger + inverse ---

def test_non_selected_missing_rejection_reason_fails() -> None:
    candidates = [
        CandidateSource(
            table="fact_table",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=True,
        ),
        CandidateSource(
            table="alt_table",
            grain=GrainSpec(dimensions=["month"]),
            grain_match="too_coarse",
            scope_feasibility={"oct_2025": "infeasible: month grain"},
            selected=False,
            rejection_reason="",  # trigger condition: empty
        ),
    ]
    trace = _trace(candidates=candidates)
    issues = validate_derivation_trace(trace, _plan(), _metadata())
    assert any("missing rejection_reason" in i for i in issues)


def test_non_selected_with_rejection_reason_passes() -> None:
    candidates = [
        CandidateSource(
            table="fact_table",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=True,
        ),
        CandidateSource(
            table="alt_table",
            grain=GrainSpec(dimensions=["month"]),
            grain_match="too_coarse",
            scope_feasibility={"oct_2025": "infeasible: month grain"},
            selected=False,
            rejection_reason="coarser grain than required",
        ),
    ]
    trace = _trace(candidates=candidates)
    assert validate_derivation_trace(trace, _plan(), _metadata()) == []


# --- Candidate table not in dbt metadata (hallucinated): trigger + inverse ---

def test_hallucinated_candidate_table_fails() -> None:
    candidates = [
        CandidateSource(
            table="invented_by_llm",  # not in metadata
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=True,
        ),
    ]
    trace = _trace(chosen_source="invented_by_llm", candidates=candidates)
    issues = validate_derivation_trace(trace, _plan(primary_table="invented_by_llm"), _metadata())
    assert any("not found in dbt metadata" in i for i in issues)


def test_real_dbt_table_passes() -> None:
    # Covered by baseline test_well_formed_trace_passes which uses "fact_table"
    # known to the synthetic metadata. Listed for parity.
    assert validate_derivation_trace(_trace(), _plan(), _metadata()) == []


# --- Scope predicate not feasible on chosen: trigger + inverse ---

def test_chosen_predicate_infeasible_fails() -> None:
    candidates = [
        CandidateSource(
            table="fact_table",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "infeasible: column missing"},  # trigger
            selected=True,
        ),
    ]
    trace = _trace(candidates=candidates)
    issues = validate_derivation_trace(trace, _plan(), _metadata())
    assert any("not feasible" in i for i in issues)


def test_chosen_predicate_feasible_passes() -> None:
    # baseline trace has feasible_via=d filter → passes
    assert validate_derivation_trace(_trace(), _plan(), _metadata()) == []


# --- Scope predicate not covered by any candidate: trigger + inverse ---

def test_scope_predicate_uncovered_fails() -> None:
    candidates = [
        CandidateSource(
            table="fact_table",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=True,
        ),
    ]
    trace = _trace(
        candidates=candidates,
        scope_predicates=["oct_2025", "completed_only"],  # 'completed_only' uncovered
    )
    issues = validate_derivation_trace(trace, _plan(), _metadata())
    assert any("not covered by any candidate" in i for i in issues)


def test_scope_predicate_covered_passes() -> None:
    candidates = [
        CandidateSource(
            table="fact_table",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={
                "oct_2025": "feasible_via=d filter",
                "completed_only": "feasible_via=baked-in",
            },
            selected=True,
        ),
    ]
    trace = _trace(
        candidates=candidates,
        scope_predicates=["oct_2025", "completed_only"],
    )
    assert validate_derivation_trace(trace, _plan(), _metadata()) == []


# --- Filter references unknown column: trigger + inverse ---

def test_filter_unknown_column_fails() -> None:
    trace = _trace(chosen_filters=["mystery_col >= '2025-10-01'"])
    issues = validate_derivation_trace(trace, _plan(), _metadata())
    assert any("not present on" in i for i in issues)


def test_filter_known_column_passes() -> None:
    # baseline uses 'd' which exists on fact_table
    assert validate_derivation_trace(_trace(), _plan(), _metadata()) == []


# --- Aggregator rationale must mention grain: trigger + inverse ---

def test_aggregator_rationale_without_grain_fails() -> None:
    trace = _trace(aggregator_rationale="because we like SUM")
    issues = validate_derivation_trace(trace, _plan(), _metadata())
    assert any("grain" in i.lower() for i in issues)


def test_aggregator_rationale_mentioning_grain_passes() -> None:
    trace = _trace(
        aggregator_rationale="SUM because gtv_idr is at the daily grain.",
    )
    assert validate_derivation_trace(trace, _plan(), _metadata()) == []


# --- rendered_why_chosen mentions chosen_source: trigger + inverse ---

def test_rendered_why_chosen_does_not_mention_chosen_fails() -> None:
    trace = _trace(rendered_why_chosen="Some random text that omits the table name.")
    issues = validate_derivation_trace(trace, _plan(), _metadata())
    assert any("does not mention chosen_source" in i for i in issues)


def test_rendered_why_chosen_with_chosen_source_passes() -> None:
    trace = _trace(rendered_why_chosen="Picked fact_table for the test reason.")
    assert validate_derivation_trace(trace, _plan(), _metadata()) == []


# --- Canonical source policy mismatch: trigger + inverse ---

def test_canonical_source_policy_mismatch_fails() -> None:
    trace = _trace(chosen_source="alt_table")
    candidates = [
        CandidateSource(
            table="alt_table",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=True,
        ),
    ]
    # Insert a 'd' column on alt_table so the filter check passes (we want to
    # isolate the source_policy failure).
    metadata = _metadata()
    metadata.models["models"][1]["columns"].append({"name": "d"})
    trace = trace.model_copy(update={"candidate_sources": candidates})
    plan = _plan(primary_table="fact_table", source_policy="canonical")
    issues = validate_derivation_trace(trace, plan, metadata)
    assert any("source_policy=canonical" in i for i in issues)


def test_noncanonical_source_policy_inverse_passes() -> None:
    """INVERSE: source_policy != 'canonical' allows chosen_source to differ.

    Proves the check is plan-driven (responds to source_policy), not blanket."""
    candidates = [
        CandidateSource(
            table="alt_table",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=True,
        ),
    ]
    metadata = _metadata()
    metadata.models["models"][1]["columns"].append({"name": "d"})
    trace = _trace(chosen_source="alt_table", candidates=candidates)
    plan = _plan(primary_table="fact_table", source_policy="user_requested_noncanonical")
    issues = validate_derivation_trace(trace, plan, metadata)
    assert not any("source_policy=canonical" in i for i in issues)


# --- render_why_chosen renderer ---

def test_render_why_chosen_substantive() -> None:
    trace = _trace()
    rendered = render_why_chosen(trace)
    assert trace.chosen_source in rendered
    assert "Aggregator: SUM" in rendered
    assert "Rejected:" in rendered


# --- apply_trace_to_answer overwrites source + why_chosen ---

def test_apply_trace_overwrites_boilerplate_why_chosen() -> None:
    from trust_analytics.models import SourceProvenance, SQLAgentAnswer

    answer = SQLAgentAnswer(
        question_id="q",
        question="?",
        metric_name="m",
        metric_value=None,
        period="oct 2025",
        source=SourceProvenance(
            primary_table="wrong",
            why_chosen="boilerplate from system prompt skeleton",
            alternatives_available=[],
        ),
        sql="SELECT 1",
        logic="x",
    )
    trace = _trace()
    apply_trace_to_answer(answer, trace)
    # source got overwritten to the trace's chosen_source
    assert answer.source is not None
    assert answer.source.primary_table == "fact_table"
    # why_chosen is now substantive — not boilerplate
    assert "boilerplate" not in answer.source.why_chosen
    assert "fact_table" in answer.source.why_chosen
    # derivation_trace is attached to the answer
    assert answer.derivation_trace is not None
    assert answer.derivation_trace.chosen_source == "fact_table"
