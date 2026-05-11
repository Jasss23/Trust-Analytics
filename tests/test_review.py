"""HITL panel formatter tests (R6).

Test the formatter helpers (not the full Rich-rendered TTY output) so we
verify the structural elements appear without needing a real terminal.

Each formatter is exercised on (a) a populated payload and (b) the
minimal-info fallback case, to make sure the panel never crashes when
some fields are absent.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from pluang_agent import review as review_mod
from pluang_agent.models import (
    BusinessQuestion,
    CandidateSource,
    DerivationTrace,
    GrainSpec,
    LayerACheck,
    LayerAReport,
    LayerBReport,
    LayerCReport,
    PipelineItem,
    PlanBreakdown,
    PlanPeriod,
    PlanSource,
    QualityReport,
    QuestionPlan,
    SourceProvenance,
    SQLAgentAnswer,
    TrustDimensions,
    TrustProfile,
)


def _capture(fn, *args, **kwargs) -> str:
    """Run a Rich-rendering function with a captured console and return the
    flat text it printed. Lets us assert on substrings."""
    buf = StringIO()
    captured = Console(file=buf, width=200, record=True)
    orig = review_mod.console
    review_mod.console = captured
    try:
        fn(*args, **kwargs)
    finally:
        review_mod.console = orig
    return buf.getvalue()


def _qa() -> QualityReport:
    return QualityReport(
        question_id="q",
        layer_a=LayerAReport(checks=[LayerACheck(name="non_empty_result", result="PASS")]),
        layer_b=LayerBReport(cross_source_findings=[], verdict="NOT_APPLICABLE"),
        layer_c=LayerCReport(
            trust_profile=TrustProfile(
                dimensions=TrustDimensions(
                    correctness="GREEN", source_reliability="GREEN", ambiguity="GREEN"
                ),
                overall="GREEN",
                reviewer_summary="clean",
            ),
        ),
    )


def _plan() -> QuestionPlan:
    return QuestionPlan(
        question_id="q",
        metric_intent="gtv_idr",
        period=PlanPeriod(start="2025-10-01", end="2025-11-01"),
        answer_shape="breakdown",
        primary_source=PlanSource(
            table="fact_trading",
            column="gtv_idr",
            period_column="d",
            aggregator="SUM",
            reason="canonical",
        ),
        breakdown=PlanBreakdown(dimension="asset_class", exclude_aggregate_members=["Total"]),
        required_output_columns=["asset_class", "gtv_idr"],
    )


def _trace() -> DerivationTrace:
    return DerivationTrace(
        required_grain=GrainSpec(dimensions=["d", "asset_class"]),
        scope_predicates=["October 2025", "completed_only"],
        candidate_sources=[
            CandidateSource(
                table="fact_trading",
                grain=GrainSpec(dimensions=["d", "asset_class"]),
                grain_match="exact",
                scope_feasibility={
                    "October 2025": "feasible_via=d filter",
                    "completed_only": "feasible_via=baked-in warning",
                },
                selected=True,
            ),
            CandidateSource(
                table="alt_monthly",
                grain=GrainSpec(dimensions=["month", "asset_class"]),
                grain_match="too_coarse",
                scope_feasibility={
                    "October 2025": "feasible_via=month filter",
                    "completed_only": "infeasible: includes pending",
                },
                selected=False,
                rejection_reason="monthly grain too coarse + includes pending",
            ),
        ],
        chosen_source="fact_trading",
        chosen_filters=["d >= '2025-10-01'", "d < '2025-11-01'"],
        chosen_aggregator="SUM",
        aggregator_rationale="SUM because gtv_idr is per-(d, asset_class) row, summable across days.",
        rendered_why_chosen="Picked fact_trading because grain matches and scope is feasible.",
    )


def _item_with_trace() -> PipelineItem:
    answer = SQLAgentAnswer(
        question_id="q",
        question="What was GTV (IDR) by asset class in October 2025?",
        metric_name="gtv_idr",
        metric_value=[{"asset_class": "crypto", "gtv_idr": 1.0}],
        period="October 2025",
        source=SourceProvenance(
            primary_table="fact_trading",
            why_chosen="Picked fact_trading...",
            alternatives_available=["alt_monthly"],
        ),
        sql="SELECT asset_class, SUM(gtv_idr) AS gtv_idr FROM fact_trading WHERE d >= '2025-10-01' AND d < '2025-11-01' GROUP BY asset_class",
        filters=["transaction_date in October 2025"],
        assumptions=[],
        logic="aggregate gtv_idr by asset_class",
        result_rows=[{"asset_class": "crypto", "gtv_idr": 1.0}],
        derivation_trace=_trace(),
    )
    return PipelineItem(
        question=BusinessQuestion(id="q", text="What was GTV (IDR) by asset class in October 2025?", metric="gtv_idr", period="October 2025"),
        question_plan=_plan(),
        answer=answer,
        quality_report=_qa(),
    )


def _item_without_trace() -> PipelineItem:
    answer = SQLAgentAnswer(
        question_id="q",
        question="?",
        metric_name="gtv_idr",
        metric_value=[],
        period="October 2025",
        source=SourceProvenance(primary_table="fact_trading", why_chosen="x", alternatives_available=[]),
        sql="SELECT 1",
        logic="x",
        result_rows=[],
    )
    return PipelineItem(
        question=BusinessQuestion(id="q", text="?", metric="gtv_idr", period="October 2025"),
        answer=answer,
        quality_report=_qa(),
    )


# --- Key Facts header ---

def test_key_facts_with_trace_renders_all_rows() -> None:
    """Verify every key fact row appears when the trace is populated."""
    output = _capture(review_mod._render_key_facts, _item_with_trace())
    assert "Time window" in output
    assert "2025-10-01" in output and "2025-11-01" in output
    assert "fact_trading" in output
    assert "asset_class" in output  # grain dimension
    assert "d >= '2025-10-01'" in output  # filter from trace
    assert "SUM" in output  # aggregator
    assert "breakdown" in output  # answer shape


def test_key_facts_without_trace_falls_back_to_plan_or_answer() -> None:
    """Verify the panel doesn't crash and shows fallback values when the
    trace is None."""
    output = _capture(review_mod._render_key_facts, _item_without_trace())
    assert "Key facts" in output
    assert "fact_trading" in output  # from answer.source
    # No trace, no plan → grain falls back to (unknown)
    assert "unknown" in output.lower() or "(none" in output.lower()


# --- Derivation panel ---

def test_derivation_panel_shows_all_candidates_with_match_status() -> None:
    output = _capture(review_mod._render_derivation_panel, _trace())
    # Both candidate tables present
    assert "fact_trading" in output
    assert "alt_monthly" in output
    # Match status appears
    assert "exact" in output
    assert "too_coarse" in output
    # Selection marker
    assert "selected" in output
    # Rejection reason
    assert "monthly grain too coarse" in output
    # rendered_why_chosen panel
    assert "Picked fact_trading" in output


# --- Pretty SQL ---

def test_pretty_sql_formats_single_line() -> None:
    """sqlglot pretty-formats single-line SQL into multi-line indented form."""
    one_liner = (
        "SELECT asset_class, SUM(gtv_idr) AS gtv_idr "
        "FROM fact_trading "
        "WHERE d >= '2025-10-01' AND d < '2025-11-01' "
        "GROUP BY asset_class"
    )
    formatted = review_mod._pretty_sql(one_liner)
    # sqlglot pretty output spans multiple lines
    assert "\n" in formatted
    # Contains the core tokens (case-insensitive comparison — sqlglot
    # uppercases keywords but keeps identifiers lowercase)
    flat = formatted.lower().replace("\n", " ")
    assert "from fact_trading" in flat


def test_pretty_sql_falls_back_on_parse_error() -> None:
    """Unparseable SQL just returns the raw string — no crash."""
    garbage = "not actually sql ;;;;; SELECT lol"
    formatted = review_mod._pretty_sql(garbage)
    # Either pretty or raw — but must not raise. Output is a string.
    assert isinstance(formatted, str)


# --- Note coaching ---

def test_starter_note_for_source_wrong_mentions_alternative() -> None:
    """When the answer has alternatives_available, the source_wrong starter
    note coaches the reviewer to suggest one."""
    from pluang_agent.models import ReviewCategory

    item = _item_with_trace()
    starter = review_mod._starter_note_for_category(ReviewCategory.SOURCE_WRONG, item)
    # Mentions the alternative
    assert "alt_monthly" in starter or "alternative" in starter.lower()


def test_starter_note_categories_all_return_non_empty() -> None:
    """Every rejection category returns a coaching string."""
    from pluang_agent.models import ReviewCategory

    item = _item_with_trace()
    for cat in ReviewCategory:
        starter = review_mod._starter_note_for_category(cat, item)
        assert isinstance(starter, str)
        # at least the EXTERNAL_DISAGREEMENT case returns a hint when
        # hypothesis is absent; we accept empty for unmapped paths, but
        # the 4 defined categories should all be non-empty.
        if cat in (
            ReviewCategory.ANSWER_WRONG,
            ReviewCategory.SOURCE_WRONG,
            ReviewCategory.QA_INSUFFICIENT,
            ReviewCategory.EXTERNAL_DISAGREEMENT,
        ):
            assert starter, f"starter for {cat} was empty"
