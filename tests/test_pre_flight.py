"""Pre-flight gate tests (R5).

Catches broken results before QA runs. High-precision-over-recall: a check
FAILs only when there's a definitely-wrong condition.
"""

from __future__ import annotations

from pluang_agent.metrics import MetricEntry, MetricsRegistry, SourceSpec
from pluang_agent.models import BusinessQuestion, SourceProvenance, SQLAgentAnswer
from pluang_agent.pre_flight import pre_flight_check


def _question() -> BusinessQuestion:
    return BusinessQuestion(
        id="test_q",
        text="test",
        metric="test_metric",
        period="October 2025",
    )


def _registry(entry: MetricEntry | None = None) -> MetricsRegistry:
    return MetricsRegistry(entries={entry.id: entry} if entry else {})


def _entry(
    primary_column: str = "gtv_idr",
    expected_min: float | None = None,
    expected_max: float | None = None,
) -> MetricEntry:
    return MetricEntry(
        id="test_q",
        metric_name="test_metric",
        cross_source="disabled",
        period_start="2025-10-01",
        period_end="2025-11-01",
        primary=SourceSpec(
            table="t",
            column=primary_column,
            period_column="d",
            extra_filters=(),
            breakdown=None,
            aggregator="SUM",
        ),
        expected_min=expected_min,
        expected_max=expected_max,
    )


def _answer(rows: list[dict]) -> SQLAgentAnswer:
    return SQLAgentAnswer(
        question_id="test_q",
        question="?",
        metric_name="test_metric",
        metric_value=rows,
        period="October 2025",
        source=SourceProvenance(primary_table="fact_trading", why_chosen="x", alternatives_available=[]),
        sql="SELECT 1",
        logic="x",
        result_rows=rows,
    )


def test_passes_on_clean_answer() -> None:
    entry = _entry(expected_min=100.0, expected_max=1_000_000.0)
    answer = _answer([{"gtv_idr": 50_000.0}])
    result = pre_flight_check(answer, _question(), _registry(entry))
    assert result.passed is True
    assert result.issue is None


def test_fails_on_empty_result() -> None:
    answer = _answer([])
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is False
    assert result.issue == "empty_result"
    assert result.hint is not None
    assert "period" in result.hint.lower() or "filter" in result.hint.lower()


def test_fails_on_negative_always_positive_metric() -> None:
    answer = _answer([{"gtv_idr": -100.0}])
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is False
    assert result.issue == "negative_metric"


def test_passes_on_negative_mom_change_column() -> None:
    """mom_change / delta columns are exempt from always-positive check."""
    answer = _answer([{"gtv_idr": 100.0, "mom_change_pct": -50.0}])
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is True


def test_fails_on_out_of_range_above() -> None:
    entry = _entry(expected_min=100.0, expected_max=1000.0)
    answer = _answer([{"gtv_idr": 5000.0}])
    result = pre_flight_check(answer, _question(), _registry(entry))
    assert result.passed is False
    assert result.issue == "out_of_range_above"
    assert result.hint is not None
    assert "double counting" in result.hint.lower() or "above" in result.hint.lower()


def test_fails_on_out_of_range_below() -> None:
    entry = _entry(expected_min=100.0, expected_max=1000.0)
    answer = _answer([{"gtv_idr": 5.0}])
    result = pre_flight_check(answer, _question(), _registry(entry))
    assert result.passed is False
    assert result.issue == "out_of_range_below"


def test_fails_on_all_null_primary_column() -> None:
    """When the registry's primary column is all-NULL across every returned
    row, the SQL likely queried the wrong slice (e.g. asset-class filter on
    a Total-only column)."""
    entry = _entry(primary_column="mtu")
    answer = _answer([{"mtu": None}, {"mtu": None}])
    result = pre_flight_check(answer, _question(), _registry(entry))
    assert result.passed is False
    assert result.issue == "all_null_primary"


def test_passes_when_some_primary_values_present() -> None:
    """If any row has a populated primary value, it's not all-null — pass."""
    entry = _entry(primary_column="mtu")
    answer = _answer([{"mtu": None}, {"mtu": 12453}])
    result = pre_flight_check(answer, _question(), _registry(entry))
    assert result.passed is True


def test_bypasses_when_system_error_set() -> None:
    """System-errored answers should not be re-checked — the workflow
    already routes them."""
    answer = _answer([])
    from pluang_agent.models import SystemError as _SystemError

    answer.system_error = _SystemError(
        error_class="quota",
        message="quota out",
        suggested_action="add credit",
    )
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is True


# ===========================================================================
# Plan-driven shape checks (R6 hard rule: each check has a TRIGGER test that
# fails AND an INVERSE test that passes). Synthetic plans + synthetic answers
# — NOT tied to Q1-5 — to prove these mechanisms are generic.
# ===========================================================================


from pluang_agent.models import (  # noqa: E402
    AnswerShape,
    CandidateSource,
    DerivationTrace,
    GrainSpec,
    InterpretationChoice,
    PlanBreakdown,
    PlanPeriod,
    PlanSource,
    QuestionPlan,
)


def _plan(
    answer_shape: AnswerShape = "scalar",
    primary_table: str = "fact_trading",
    breakdown: PlanBreakdown | None = None,
    required_output_columns: list[str] | None = None,
    required_definitions: list[str] | None = None,
    comparison_sources: list[PlanSource] | None = None,
) -> QuestionPlan:
    return QuestionPlan(
        question_id="test_q",
        metric_intent="m",
        period=PlanPeriod(start="2025-10-01", end="2025-11-01"),
        answer_shape=answer_shape,
        primary_source=PlanSource(
            table=primary_table,
            column="gtv_idr",
            period_column="d",
            aggregator="SUM",
            extra_filters=[],
            reason="canonical",
        ),
        comparison_sources=comparison_sources or [],
        breakdown=breakdown,
        required_output_columns=required_output_columns or [],
        required_definitions=required_definitions or [],
    )


def _populated_answer(rows: list[dict], sql: str = "SELECT 1 FROM fact_trading WHERE d >= '2025-10-01'") -> SQLAgentAnswer:
    return SQLAgentAnswer(
        question_id="test_q",
        question="?",
        metric_name="m",
        metric_value=rows,
        period="October 2025",
        source=SourceProvenance(primary_table="fact_trading", why_chosen="x", alternatives_available=[]),
        sql=sql,
        logic="x",
        result_rows=rows,
    )


# --- source_missing: trigger + inverse ---

def test_source_missing_trigger() -> None:
    """Plan present, answer.source is None → fail."""
    plan = _plan()
    answer = _populated_answer([{"gtv_idr": 100.0}])
    answer.source = None  # the trigger condition
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is False
    assert result.issue == "source_missing"


def test_source_missing_inverse_passes() -> None:
    """Plan present, answer.source set → pass (the very basic happy path)."""
    plan = _plan()
    answer = _populated_answer([{"gtv_idr": 100.0}])
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is True


# --- source_mismatch: trigger + inverse ---

def test_source_mismatch_trigger() -> None:
    """plan.primary_source.table != answer.source.primary_table → fail."""
    plan = _plan(primary_table="canonical_table")
    answer = _populated_answer(
        [{"gtv_idr": 100.0}],
        sql="SELECT gtv_idr FROM wrong_table WHERE d >= '2025-10-01'",
    )
    answer.source = SourceProvenance(
        primary_table="wrong_table", why_chosen="x", alternatives_available=[]
    )
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is False
    assert result.issue == "source_mismatch"


def test_source_mismatch_inverse_passes() -> None:
    """When plan and answer agree on primary table → pass."""
    plan = _plan(primary_table="fact_trading")
    answer = _populated_answer([{"gtv_idr": 100.0}])  # answer.source.primary_table = 't'
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is True


# --- sql_source_mismatch: trigger + inverse ---

def test_sql_source_mismatch_trigger() -> None:
    """plan.primary_source.table not in answer.sql → fail."""
    plan = _plan(primary_table="fact_trading")
    answer = _populated_answer(
        [{"gtv_idr": 100.0}],
        sql="SELECT gtv_idr FROM unrelated_view WHERE d >= '2025-10-01'",
    )
    # source field matches plan but SQL queries a different table
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is False
    assert result.issue == "sql_source_mismatch"


def test_sql_source_mismatch_inverse_passes() -> None:
    """SQL actually queries the plan's primary table → pass."""
    plan = _plan(primary_table="fact_trading")
    answer = _populated_answer([{"gtv_idr": 100.0}], sql="SELECT gtv_idr FROM fact_trading WHERE d >= '2025-10-01'")
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is True


# --- required_columns_missing: trigger + inverse ---

def test_required_columns_missing_trigger() -> None:
    """Plan declares output columns; answer rows lack one → fail."""
    plan = _plan(required_output_columns=["asset_class", "gtv_idr"])
    answer = _populated_answer([{"gtv_idr": 100.0}])  # missing 'asset_class'
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is False
    assert result.issue == "required_columns_missing"


def test_required_columns_missing_inverse_passes() -> None:
    """All required columns present → pass."""
    plan = _plan(required_output_columns=["asset_class", "gtv_idr"])
    answer = _populated_answer([{"asset_class": "crypto", "gtv_idr": 100.0}])
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is True


# --- aggregate_member_in_breakdown: TRIGGER + INVERSE (key bi-directional case) ---

def test_aggregate_member_trigger_when_plan_excludes() -> None:
    """Plan excludes 'Total' from breakdown, answer rows include a 'Total' row → fail."""
    plan = _plan(
        answer_shape="breakdown",
        breakdown=PlanBreakdown(dimension="asset_class", exclude_aggregate_members=["Total"]),
        required_output_columns=["asset_class", "gtv_idr"],
    )
    answer = _populated_answer(
        [
            {"asset_class": "crypto", "gtv_idr": 100.0},
            {"asset_class": "Total", "gtv_idr": 500.0},
        ]
    )
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is False
    assert result.issue == "aggregate_member_in_breakdown"


def test_aggregate_member_passes_when_plan_does_NOT_exclude() -> None:
    """INVERSE: question asks for grand total (or otherwise wants Total).
    Plan has empty exclude_aggregate_members. Answer rows with 'Total' → pass.

    This is the test that proves the mechanism is plan-driven, not
    hardcoded to 'Total is always bad'. If a question genuinely asks for
    'overall GTV', the plan will not exclude Total, and the check must
    silently let that answer through."""
    plan = _plan(
        answer_shape="breakdown",
        breakdown=PlanBreakdown(dimension="asset_class", exclude_aggregate_members=[]),
        required_output_columns=["asset_class", "gtv_idr"],
    )
    answer = _populated_answer([{"asset_class": "Total", "gtv_idr": 500.0}])
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is True


# --- missing_required_definitions: trigger + inverse ---
# Note: planner validator enforces required_definitions ⊆ required_output_columns,
# so a missing definition is also a missing column. The required_columns_missing
# check fires first and is the canonical surface; the multi_definition-specific
# check is defence-in-depth for the (rare) case where the column appears in
# required_output_columns but the answer row still lacks it.

def test_multi_definition_with_missing_column_fails_as_columns_missing() -> None:
    """TRIGGER: multi_definition answer missing one required column → fails
    via required_columns_missing (which is the upstream surface)."""
    plan = _plan(
        answer_shape="multi_definition",
        required_output_columns=["a", "b", "c"],
        required_definitions=["a", "b", "c"],
    )
    answer = _populated_answer(
        [{"a": 1, "b": 2}],  # missing 'c'
    )
    answer.interpretation_choices = [
        InterpretationChoice(choice="A primary", alternatives=["B", "C"], rationale="x")
    ]
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is False
    assert result.issue == "required_columns_missing"


def test_multi_definition_complete_inverse_passes() -> None:
    """All required definitions present + interpretation_choices populated → pass."""
    plan = _plan(
        answer_shape="multi_definition",
        required_output_columns=["a", "b", "c"],
        required_definitions=["a", "b", "c"],
    )
    answer = _populated_answer([{"a": 1, "b": 2, "c": 3}])
    answer.interpretation_choices = [
        InterpretationChoice(choice="A primary", alternatives=["B", "C"], rationale="x")
    ]
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is True


# --- missing_interpretation_choices: trigger + inverse ---
# NOTE: this check fires only on answer_shape='multi_definition'. The INVERSE
# is "scalar question with interpretation_choices=[] passes" — that's the
# happy-path direction proving the check is shape-driven, not blanket.

def test_missing_interpretation_choices_trigger() -> None:
    plan = _plan(
        answer_shape="multi_definition",
        required_output_columns=["a", "b"],
        required_definitions=["a", "b"],
    )
    answer = _populated_answer([{"a": 1, "b": 2}])
    # interpretation_choices intentionally empty
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is False
    assert result.issue == "missing_interpretation_choices"


def test_scalar_with_empty_interpretation_choices_passes() -> None:
    """INVERSE: scalar shape, no ambiguity expected → pass even with empty
    interpretation_choices."""
    plan = _plan(answer_shape="scalar", required_output_columns=["gtv_idr"])
    answer = _populated_answer([{"gtv_idr": 100.0}])  # interpretation_choices=[]
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is True


# --- comparison_source_missing: trigger + inverse ---

def test_comparison_source_missing_trigger() -> None:
    """breakdown_comparison plan with no comparison_sources → fail."""
    plan = _plan(
        answer_shape="breakdown_comparison",
        breakdown=PlanBreakdown(dimension="asset_class", exclude_aggregate_members=[]),
        required_output_columns=["asset_class", "primary_gtv_idr", "canonical_gtv_idr", "absolute_delta_idr", "delta_pct"],
        comparison_sources=[],  # the trigger
    )
    answer = _populated_answer(
        [{"asset_class": "crypto", "primary_gtv_idr": 100, "canonical_gtv_idr": 90, "absolute_delta_idr": 10, "delta_pct": 11.0}]
    )
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is False
    assert result.issue == "comparison_source_missing"


def test_comparison_source_present_passes() -> None:
    """INVERSE: breakdown_comparison with a comparison source declared → pass."""
    plan = _plan(
        answer_shape="breakdown_comparison",
        breakdown=PlanBreakdown(dimension="asset_class", exclude_aggregate_members=[]),
        required_output_columns=["asset_class", "primary_gtv_idr", "canonical_gtv_idr", "absolute_delta_idr", "delta_pct"],
        comparison_sources=[
            PlanSource(table="t", column="gtv_idr", period_column="d", aggregator="SUM", reason="canonical alt")
        ],
    )
    answer = _populated_answer(
        [{"asset_class": "crypto", "primary_gtv_idr": 100, "canonical_gtv_idr": 90, "absolute_delta_idr": 10, "delta_pct": 11.0}]
    )
    result = pre_flight_check(answer, _question(), _registry(), question_plan=plan)
    assert result.passed is True


# ===========================================================================
# R6 trace-driven checks: bi-directional
# ===========================================================================


def _trace(
    chosen_source: str = "fact_trading",
    chosen_filters: list[str] | None = None,
    candidates: list[CandidateSource] | None = None,
    scope_predicates: list[str] | None = None,
) -> DerivationTrace:
    if candidates is None:
        candidates = [
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
        scope_predicates=scope_predicates or ["oct_2025"],
        candidate_sources=candidates,
        chosen_source=chosen_source,
        chosen_filters=chosen_filters or ["d >= '2025-10-01'"],
        chosen_aggregator="SUM",
        aggregator_rationale="SUM because gtv_idr is per-day grain, summable across days.",
        rendered_why_chosen=f"Picked {chosen_source} ...",
    )


# --- trace_complete: trigger + inverse ---

def test_trace_no_candidates_trigger() -> None:
    """Empty candidate_sources → fail."""
    answer = _populated_answer([{"gtv_idr": 100.0}])
    # Build a trace with empty candidate list by direct construction
    answer.derivation_trace = DerivationTrace(
        required_grain=GrainSpec(dimensions=["d"]),
        scope_predicates=["oct_2025"],
        candidate_sources=[],
        chosen_source="fact_trading",
        chosen_filters=["d >= '2025-10-01'"],
        chosen_aggregator="SUM",
        aggregator_rationale="per-day grain",
        rendered_why_chosen="x",
    )
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is False
    assert result.issue == "trace_no_candidates"


def test_trace_complete_passes() -> None:
    """INVERSE: well-formed trace → pass."""
    answer = _populated_answer([{"gtv_idr": 100.0}])
    answer.derivation_trace = _trace()
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is True


# --- filters_match_trace: trigger + inverse ---

def test_filters_missing_from_sql_trigger() -> None:
    """Trace mandates 'asset_class != Total' but SQL omits it → fail."""
    answer = _populated_answer(
        [{"gtv_idr": 100.0}],
        sql="SELECT SUM(gtv_idr) FROM fact_trading WHERE d >= '2025-10-01'",
    )
    answer.derivation_trace = _trace(
        chosen_filters=["d >= '2025-10-01'", "asset_class != 'Total'"],
    )
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is False
    assert result.issue == "filters_missing_from_sql"


def test_filters_present_in_sql_passes() -> None:
    """INVERSE: SQL contains every declared filter → pass."""
    answer = _populated_answer(
        [{"gtv_idr": 100.0}],
        sql="SELECT SUM(gtv_idr) FROM fact_trading WHERE d >= '2025-10-01' AND asset_class != 'Total'",
    )
    answer.derivation_trace = _trace(
        chosen_filters=["d >= '2025-10-01'", "asset_class != 'Total'"],
    )
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is True


# --- ambiguity_surfaced: TRIGGER + INVERSE (key data-driven check) ---

def test_ambiguity_not_surfaced_trigger() -> None:
    """Trace has 2 equally-feasible exact-grain candidates → interpretation_choices
    MUST be populated. Empty → fail."""
    answer = _populated_answer([{"gtv_idr": 100.0}])
    candidates = [
        CandidateSource(
            table="fact_trading",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=True,
        ),
        CandidateSource(
            table="fact_alt",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=False,
            rejection_reason="preferred canonical primary, but both feasible",
        ),
    ]
    answer.derivation_trace = _trace(
        chosen_source="fact_trading",
        chosen_filters=["d >= '2025-10-01'"],
        candidates=candidates,
    )
    # interpretation_choices intentionally empty → must fail
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is False
    assert result.issue == "ambiguity_not_surfaced"


def test_ambiguity_surfaced_when_populated_passes() -> None:
    """INVERSE-1: same ambiguous trace but with interpretation_choices populated → pass."""
    answer = _populated_answer([{"gtv_idr": 100.0}])
    candidates = [
        CandidateSource(
            table="fact_trading",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=True,
        ),
        CandidateSource(
            table="fact_alt",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=False,
            rejection_reason="canonical alt",
        ),
    ]
    answer.derivation_trace = _trace(
        chosen_source="fact_trading",
        chosen_filters=["d >= '2025-10-01'"],
        candidates=candidates,
    )
    answer.interpretation_choices = [
        InterpretationChoice(
            choice="fact_trading primary",
            alternatives=["fact_alt"],
            rationale="canonical",
        )
    ]
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is True


def test_single_feasible_candidate_skips_ambiguity_check() -> None:
    """INVERSE-2: only one feasible candidate → no ambiguity to surface,
    empty interpretation_choices is fine. Proves the check is data-driven,
    not blanket."""
    answer = _populated_answer([{"gtv_idr": 100.0}])
    candidates = [
        CandidateSource(
            table="fact_trading",
            grain=GrainSpec(dimensions=["d"]),
            grain_match="exact",
            scope_feasibility={"oct_2025": "feasible_via=d filter"},
            selected=True,
        ),
        CandidateSource(
            table="fact_alt",
            grain=GrainSpec(dimensions=["m"]),
            grain_match="too_coarse",
            scope_feasibility={"oct_2025": "infeasible: month grain"},
            selected=False,
            rejection_reason="grain too coarse",
        ),
    ]
    answer.derivation_trace = _trace(
        chosen_source="fact_trading",
        chosen_filters=["d >= '2025-10-01'"],
        candidates=candidates,
    )
    # No interpretation_choices, but only one feasible → pass.
    result = pre_flight_check(answer, _question(), _registry())
    assert result.passed is True
