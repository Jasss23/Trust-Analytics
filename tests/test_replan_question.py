"""Tests for `planner.replan_question` (R8).

Bi-directional per the R6 hard rule:
  TRIGGER tests: reviewer notes that DO motivate a plan change → revised
                 plan reflects the change → validator passes.
  INVERSE tests: reviewer notes that do NOT motivate a plan change (SQL
                 typo, vague complaint) → LLM leaves plan unchanged →
                 workflow re-runs SQL Agent on the same plan.
                 ALSO: hallucinated revisions → validator rejects →
                 system_error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from trust_analytics.llm import LLMResponse
from trust_analytics.metadata import DbtMetadata
from trust_analytics.metrics import MetricsRegistry
from trust_analytics.models import (
    BusinessQuestion,
    PlanPeriod,
    PlanSource,
    QuestionPlan,
    SourceProvenance,
    SQLAgentAnswer,
    UsageRecord,
)
from trust_analytics.planner import (
    _propose_and_validate_revision,
    replan_question,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _metadata() -> DbtMetadata:
    """Synthetic metadata with two tables for revision tests."""
    return DbtMetadata(
        sources={},
        models={
            "models": [
                {
                    "name": "fact_table",
                    "columns": [
                        {"name": "d"},
                        {"name": "gtv_idr"},
                        {"name": "gtv_usd"},
                        {"name": "asset_class"},
                        {"name": "transaction_count"},
                    ],
                },
                {
                    "name": "ops_dashboard",
                    "columns": [
                        {"name": "month"},
                        {"name": "gtv_idr"},
                        {"name": "asset_class"},
                    ],
                },
            ]
        },
    )


def _question() -> BusinessQuestion:
    return BusinessQuestion(
        id="adhoc_test",
        text="how many rows in fact_table for all time",
        metric="gtv_idr",
        period="unspecified",
    )


def _previous_plan(
    metric_intent: str = "gtv_idr",
    primary_table: str = "fact_table",
    primary_column: str = "gtv_idr",
    aggregator: str = "SUM",
    answer_shape: str = "scalar",
    required_output_columns: list[str] | None = None,
) -> QuestionPlan:
    return QuestionPlan(
        question_id="adhoc_test",
        metric_intent=metric_intent,
        period=PlanPeriod(start="2025-10-01", end="2025-11-01"),
        answer_shape=answer_shape,  # type: ignore[arg-type]
        primary_source=PlanSource(
            table=primary_table,
            column=primary_column,
            period_column="d",
            aggregator=aggregator,  # type: ignore[arg-type]
            reason="canonical",
        ),
        required_output_columns=required_output_columns or [primary_column],
        source_policy="schema_grounded",
    )


def _previous_answer(rows: list[dict] | None = None, sql: str = "SELECT SUM(gtv_idr) FROM fact_table") -> SQLAgentAnswer:
    return SQLAgentAnswer(
        question_id="adhoc_test",
        question="how many rows?",
        metric_name="gtv_idr",
        metric_value=rows or [{"gtv_idr": 1000000.0}],
        period="unspecified",
        source=SourceProvenance(primary_table="fact_table", why_chosen="x", alternatives_available=[]),
        sql=sql,
        logic="sum gtv",
        result_rows=rows or [{"gtv_idr": 1000000.0}],
    )


def _registry() -> MetricsRegistry:
    """No registry entries for the synthetic question."""
    return MetricsRegistry(entries={})


@dataclass
class _StubLLM:
    """Returns canned responses keyed by stage_tag prefix. Tracks calls."""

    revise_payload: str = ""
    trace_payload: str = ""
    calls: list[str] | None = None
    available: bool = True

    def __post_init__(self):
        if self.calls is None:
            self.calls = []

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        self.calls.append(stage_tag)
        if stage_tag.startswith("planner_revise"):
            return LLMResponse(content=self.revise_payload, usage=UsageRecord())
        if stage_tag.startswith("planner_trace"):
            return LLMResponse(content=self.trace_payload, usage=UsageRecord())
        return LLMResponse(content="{}", usage=UsageRecord())


def _make_revised_plan_json(
    *,
    metric_intent: str,
    primary_column: str,
    aggregator: str,
    required_output_columns: list[str],
    primary_table: str = "fact_table",
    revision_note: str | None = None,
) -> str:
    payload: dict = {
        "question_id": "adhoc_test",
        "metric_intent": metric_intent,
        "period": {"start": "2025-10-01", "end": "2025-11-01"},
        "answer_shape": "scalar",
        "primary_source": {
            "table": primary_table,
            "column": primary_column,
            "period_column": "d",
            "aggregator": aggregator,
            "extra_filters": [],
            "reason": "revised per reviewer note",
        },
        "comparison_sources": [],
        "breakdown": None,
        "required_output_columns": required_output_columns,
        "required_definitions": [],
        "ambiguity_policy": "single_definition",
        "source_policy": "schema_grounded",
        "validation_rules": [],
    }
    if revision_note is not None:
        payload["revision_note"] = revision_note
    return json.dumps(payload)


def _make_trace_json(chosen_source: str = "fact_table", chosen_column: str = "row_count", aggregator: str = "COUNT_DISTINCT") -> str:
    return json.dumps(
        {
            "required_grain": {"dimensions": ["d"]},
            "scope_predicates": [],
            "candidate_sources": [
                {
                    "table": chosen_source,
                    "grain": {"dimensions": ["d"]},
                    "grain_match": "exact",
                    "scope_feasibility": {},
                    "selected": True,
                    "rejection_reason": None,
                },
            ],
            "chosen_source": chosen_source,
            "chosen_filters": [],
            "chosen_aggregator": aggregator,
            "aggregator_rationale": "per-day grain row aggregation",
            "rendered_why_chosen": f"Picked {chosen_source}.",
        }
    )


# ===========================================================================
# TRIGGER tests: reviewer note motivates a plan change
# ===========================================================================


def test_count_not_sum_revises_plan() -> None:
    """The exact case the user surfaced: reviewer note 'what I need is row
    count, why used sum' → revised plan must change aggregator + columns.

    primary_source.column must be a real column on the table (validator
    enforces this). For a row-count, COUNT_DISTINCT over the period column
    `d` is a valid plan shape; the SQL alias `row_count` lives in
    required_output_columns, not primary_source.column."""
    revised_json = _make_revised_plan_json(
        metric_intent="row_count",
        primary_column="d",  # real column on fact_table
        aggregator="COUNT_DISTINCT",
        required_output_columns=["row_count"],
    )
    trace_json = _make_trace_json(aggregator="COUNT_DISTINCT")
    llm = _StubLLM(revise_payload=revised_json, trace_payload=trace_json)
    result = replan_question(
        question=_question(),
        previous_plan=_previous_plan(),
        previous_answer=_previous_answer(),
        reviewer_note="what i need is how many rows, why used sum",
        registry=_registry(),
        metadata=_metadata(),
        llm_client=llm,
    )
    assert result.system_error is None, f"unexpected system_error: {result.system_error}"
    assert result.plan is not None
    assert result.plan.metric_intent == "row_count"
    assert result.plan.primary_source.aggregator == "COUNT_DISTINCT"
    assert result.plan.required_output_columns == ["row_count"]
    # Trace was re-derived with the new plan
    assert result.derivation_trace is not None
    # The revise stage was actually called
    assert any(c.startswith("planner_revise") for c in llm.calls)


def test_use_different_source_revises_primary_table() -> None:
    """Reviewer note 'use ops_dashboard instead' → revised plan changes
    primary_source.table."""
    revised_json = _make_revised_plan_json(
        metric_intent="gtv_idr",
        primary_column="gtv_idr",
        aggregator="SUM",
        required_output_columns=["gtv_idr"],
        primary_table="ops_dashboard",
    )
    # Adjust period_column for ops_dashboard which uses 'month' not 'd'
    payload = json.loads(revised_json)
    payload["primary_source"]["period_column"] = "month"
    revised_json = json.dumps(payload)

    trace_json = _make_trace_json(chosen_source="ops_dashboard", aggregator="SUM")
    llm = _StubLLM(revise_payload=revised_json, trace_payload=trace_json)
    result = replan_question(
        question=_question(),
        previous_plan=_previous_plan(),
        previous_answer=_previous_answer(),
        reviewer_note="use ops_dashboard instead, that's the canonical source",
        registry=_registry(),
        metadata=_metadata(),
        llm_client=llm,
    )
    assert result.system_error is None, f"unexpected system_error: {result.system_error}"
    assert result.plan is not None
    assert result.plan.primary_source.table == "ops_dashboard"


# ===========================================================================
# INVERSE tests: note doesn't motivate plan change
# ===========================================================================


def test_unchanged_plan_with_revision_note_passes_validator() -> None:
    """INVERSE: reviewer note says 'SQL has a typo' — not a plan issue.
    LLM emits plan unchanged + revision_note. Validator accepts."""
    unchanged_json = _make_revised_plan_json(
        metric_intent="gtv_idr",
        primary_column="gtv_idr",
        aggregator="SUM",
        required_output_columns=["gtv_idr"],
        revision_note="SQL syntax issue; plan is correct.",
    )
    llm = _StubLLM(revise_payload=unchanged_json)
    revised = _propose_and_validate_revision(
        question=_question(),
        previous_plan=_previous_plan(),
        previous_answer=_previous_answer(),
        reviewer_note="the SQL has a typo on column gtv_idr — also it's spelled gtv_idr",
        registry=_registry(),
        metadata=_metadata(),
        llm_client=llm,
    )
    assert revised.system_error is None
    assert revised.plan is not None
    # The plan content matches the previous plan (no aggregator change)
    assert revised.plan.primary_source.aggregator == "SUM"
    # And the revision_note was extracted from the payload
    assert revised.revision_note == "SQL syntax issue; plan is correct."


# ===========================================================================
# Failure modes
# ===========================================================================


def test_hallucinated_table_in_revised_plan_fails_validator() -> None:
    """If the LLM emits a revised plan that references a non-existent
    table, the validator rejects and `replan_question` returns
    `plan_revision_failed`. Bi-directional inverse: a real table passes
    (covered by test_count_not_sum_revises_plan)."""
    bad_payload = _make_revised_plan_json(
        metric_intent="gtv_idr",
        primary_column="gtv_idr",
        aggregator="SUM",
        required_output_columns=["gtv_idr"],
        primary_table="ghost_table",  # not in metadata
    )
    llm = _StubLLM(revise_payload=bad_payload, trace_payload="{}")
    result = replan_question(
        question=_question(),
        previous_plan=_previous_plan(),
        previous_answer=_previous_answer(),
        reviewer_note="use a different source",
        registry=_registry(),
        metadata=_metadata(),
        llm_client=llm,
    )
    assert result.system_error is not None
    assert result.system_error.error_class == "plan_revision_failed"
    assert "not found in metadata" in result.system_error.message.lower() or "ghost_table" in result.system_error.message


def test_question_id_rewrite_is_rejected() -> None:
    """The validator must not allow the LLM to rewrite question_id (would
    break per-question state keying in the workflow)."""
    payload_dict = json.loads(_make_revised_plan_json(
        metric_intent="gtv_idr",
        primary_column="gtv_idr",
        aggregator="SUM",
        required_output_columns=["gtv_idr"],
    ))
    payload_dict["question_id"] = "different_id"
    bad_payload = json.dumps(payload_dict)
    llm = _StubLLM(revise_payload=bad_payload)
    revised = _propose_and_validate_revision(
        question=_question(),
        previous_plan=_previous_plan(),
        previous_answer=_previous_answer(),
        reviewer_note="any note",
        registry=_registry(),
        metadata=_metadata(),
        llm_client=llm,
    )
    assert revised.system_error is not None
    assert revised.system_error.error_class == "plan_revision_failed"


def test_llm_call_failure_returns_system_error() -> None:
    """If the LLM call itself fails (auth/quota/etc.), the failure is
    surfaced cleanly."""
    from trust_analytics.llm import LLMAuthError

    class _Raises:
        available = True

        def chat_json(self, system, user, *, stage_tag=""):
            raise LLMAuthError("bad key")

    revised = _propose_and_validate_revision(
        question=_question(),
        previous_plan=_previous_plan(),
        previous_answer=_previous_answer(),
        reviewer_note="any note",
        registry=_registry(),
        metadata=_metadata(),
        llm_client=_Raises(),
    )
    assert revised.system_error is not None
    # The hard LLM error_class propagates through
    assert revised.system_error.error_class == "auth"
