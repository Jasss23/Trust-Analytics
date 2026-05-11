"""End-to-end workflow reinvestigation test (R8).

Replays the gap that surfaced after R7: an ad-hoc question whose initial
plan inferred a wrong metric, reviewer rejects with answer_wrong + a note
explaining what was wrong, workflow LLM-revises the plan, SQL Agent
writes new SQL, pre-flight passes, terminal = reinvestigated (NOT
audit_required).

Plus the inverse: SQL-typo-only reviewer note → plan unchanged via
revision_note → SQL Agent retries on same plan → reinvestigated.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pluang_agent.agents.quality_agent import QualityAgent
from pluang_agent.agents.sql_agent import SQLAgent
from pluang_agent.llm import LLMResponse
from pluang_agent.metadata import DbtMetadata
from pluang_agent.metrics import MetricsRegistry
from pluang_agent.models import (
    BusinessQuestion,
    ReviewMode,
    TerminalState,
    UsageRecord,
)
from pluang_agent.workflow import run_pipeline

# ---------------------------------------------------------------------------
# Fixtures: real DB + synthetic metadata
# ---------------------------------------------------------------------------


@pytest.fixture()
def real_db(tmp_path: Path) -> Path:
    db = tmp_path / "wf.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE fct_trading_daily (transaction_date TEXT, asset_class TEXT, gtv_idr REAL)")
    conn.execute(
        "INSERT INTO fct_trading_daily VALUES "
        "('2025-10-15', 'crypto', 1000.0), "
        "('2025-10-20', 'crypto', 2000.0), "
        "('2025-10-25', 'gold', 500.0)"
    )
    conn.commit()
    conn.close()
    return db


def _metadata() -> DbtMetadata:
    return DbtMetadata(
        sources={},
        models={
            "models": [
                {
                    "name": "fct_trading_daily",
                    "columns": [
                        {"name": "transaction_date"},
                        {"name": "asset_class"},
                        {"name": "gtv_idr"},
                    ],
                },
            ]
        },
    )


class _Stub:
    """Scripted stub keyed by stage_tag prefix. Tracks every call. Lets
    individual tests script the LLM responses for each phase."""

    def __init__(self):
        self.available = True
        self.calls: list[str] = []
        self.responses: dict[str, list[str]] = {}

    def queue(self, stage_prefix: str, payload: str) -> None:
        self.responses.setdefault(stage_prefix, []).append(payload)

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        self.calls.append(stage_tag)
        for prefix, payloads in self.responses.items():
            if stage_tag.startswith(prefix) and payloads:
                return LLMResponse(content=payloads.pop(0), usage=UsageRecord())
        # Default — pre-flight / QA layers that we don't strictly script
        if stage_tag.startswith("qa_layer_b"):
            return LLMResponse(content=_qa_layer_b_payload(), usage=UsageRecord())
        if stage_tag.startswith("qa_layer_c"):
            return LLMResponse(content=_qa_layer_c_payload(), usage=UsageRecord())
        return LLMResponse(content="{}", usage=UsageRecord())


def _trace_payload(chosen_aggregator: str = "SUM") -> str:
    return json.dumps(
        {
            "required_grain": {"dimensions": ["transaction_date"]},
            "scope_predicates": [],
            "candidate_sources": [
                {
                    "table": "fct_trading_daily",
                    "grain": {"dimensions": ["transaction_date"]},
                    "grain_match": "exact",
                    "scope_feasibility": {},
                    "selected": True,
                    "rejection_reason": None,
                },
            ],
            "chosen_source": "fct_trading_daily",
            "chosen_filters": [],
            "chosen_aggregator": chosen_aggregator,
            "aggregator_rationale": f"{chosen_aggregator} aggregates per-day grain rows.",
            "rendered_why_chosen": "Picked fct_trading_daily per test.",
        }
    )


def _sql_payload(sql: str, alias: str, value: object) -> str:
    return json.dumps(
        {
            "question_id": "adhoc_test",
            "question": "test",
            "metric_name": "m",
            "metric_value": [{alias: value}],
            "period": "October 2025",
            "source": {
                "primary_table": "fct_trading_daily",
                "why_chosen": "planner-derived",
                "alternatives_available": [],
            },
            "sql": sql,
            "filters": [],
            "assumptions": [],
            "logic": "x",
            "result_rows": [],
            "interpretation_choices": [],
            "dq_notes": [],
            "warnings": [],
        }
    )


def _revised_plan_payload(
    *,
    metric_intent: str,
    primary_column: str,
    aggregator: str,
    required_output_columns: list[str],
    revision_note: str | None = None,
) -> str:
    payload: dict = {
        "question_id": "adhoc_test",
        "metric_intent": metric_intent,
        "period": {"start": "2025-10-01", "end": "2025-11-01"},
        "answer_shape": "scalar",
        "primary_source": {
            "table": "fct_trading_daily",
            "column": primary_column,
            "period_column": "transaction_date",
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


def _qa_layer_b_payload() -> str:
    return json.dumps(
        {
            "proposal": "n/a",
            "evidence": ["single source"],
            "confidence": "LOW",
            "what_this_does_not_explain": "n/a",
        }
    )


def _qa_layer_c_payload() -> str:
    return json.dumps(
        {
            "dimensions": {
                "correctness": "GREEN",
                "source_reliability": "GREEN",
                "ambiguity": "GREEN",
            },
            "overall": "GREEN",
            "reviewer_summary": "Clean.",
            "unresolved_questions": [],
        }
    )


@pytest.fixture()
def patch_review(monkeypatch: pytest.MonkeyPatch):
    """Override the human review step to inject a scripted decision."""
    from pluang_agent import workflow as wf
    from pluang_agent.models import ReviewCategory, ReviewDecision

    state: dict[str, object] = {}

    def _fake_collect(items, review_mode):
        # First call: reject the first item with the configured note + category.
        # Subsequent calls: approve everything (not used in single-question tests).
        cat = state.get("category", ReviewCategory.ANSWER_WRONG)
        note = state.get("note", "say more about why")
        return [
            ReviewDecision(
                question_id=item.question.id,
                decision="reject",
                category=cat,
                note=note,
            )
            for item in items
        ]

    monkeypatch.setattr(wf, "collect_review_decisions", _fake_collect)
    return state


# ===========================================================================
# TRIGGER: reviewer note motivates plan change → workflow re-plans → success
# ===========================================================================


def test_reinvestigation_revises_plan_for_count_not_sum(
    monkeypatch, real_db: Path, patch_review
) -> None:
    """Replays the user's reported case end-to-end:
    1. Initial plan infers metric=gtv_idr → SUM(gtv_idr) → answer wrong intent
    2. Reviewer rejects with note 'I want row count, not sum'
    3. Workflow LLM-revises plan to COUNT_DISTINCT(d)
    4. SQL Agent writes COUNT, pre-flight passes
    5. Terminal = reinvestigated, NOT audit_required
    """
    from pluang_agent.models import ReviewCategory

    stub = _Stub()
    # Phase 1 (initial planning): trace for SUM(gtv_idr)
    stub.queue("planner_trace:", _trace_payload(chosen_aggregator="SUM"))
    # Phase 1 (initial SQL): SUM(gtv_idr)
    stub.queue(
        "sql_agent:",
        _sql_payload("SELECT SUM(gtv_idr) AS gtv_idr FROM fct_trading_daily", "gtv_idr", 3500.0),
    )
    # Phase 2 (revision): LLM revises plan to COUNT_DISTINCT
    stub.queue(
        "planner_revise:",
        _revised_plan_payload(
            metric_intent="row_count",
            primary_column="transaction_date",
            aggregator="COUNT_DISTINCT",
            required_output_columns=["row_count"],
        ),
    )
    # Phase 2 (re-derived trace): COUNT_DISTINCT trace
    stub.queue("planner_trace:", _trace_payload(chosen_aggregator="COUNT_DISTINCT"))
    # Phase 2 (re-generated SQL): COUNT(*) AS row_count.
    # NOTE: stage_tag is `sql_agent_retry:` when correction_context is set
    # (reinvestigation flow always passes a human_reject correction context).
    stub.queue(
        "sql_agent_retry:",
        _sql_payload(
            "SELECT COUNT(*) AS row_count FROM fct_trading_daily", "row_count", 3
        ),
    )

    patch_review["category"] = ReviewCategory.ANSWER_WRONG
    patch_review["note"] = "what i need is how many rows, why used sum"

    sql_agent = SQLAgent(
        db_path=real_db,
        metadata=_metadata(),
        llm_client=stub,  # type: ignore[arg-type]
        metrics_registry=MetricsRegistry(entries={}),
    )
    quality_agent = QualityAgent(
        db_path=real_db,
        metrics_registry=MetricsRegistry(entries={}),
        llm_client=stub,  # type: ignore[arg-type]
    )
    question = BusinessQuestion(
        id="adhoc_test",
        text="how many rows in fct_trading_daily",
        metric="gtv_idr",
        period="October 2025",
    )
    result = run_pipeline([question], sql_agent, quality_agent, ReviewMode.INTERACTIVE)
    item = result.items[0]
    assert item.review_decision is not None
    assert item.review_decision.terminal_state == TerminalState.REINVESTIGATED, (
        f"expected REINVESTIGATED, got {item.review_decision.terminal_state} "
        f"(audit_reason={item.review_decision.audit_reason!r})"
    )
    # Reinvestigated answer has the new shape
    assert item.reinvestigated_answer is not None
    assert "row_count" in str(item.reinvestigated_answer.metric_value).lower()
    # And the revise stage was actually called
    assert any(c.startswith("planner_revise:") for c in stub.calls)


# ===========================================================================
# INVERSE: SQL-only complaint, plan unchanged via revision_note
# ===========================================================================


def test_reinvestigation_unchanged_plan_via_revision_note(
    monkeypatch, real_db: Path, patch_review
) -> None:
    """INVERSE: reviewer note about a SQL typo (not a plan issue). LLM
    emits plan unchanged + revision_note. Workflow re-runs SQL Agent
    against the same plan; pre-flight passes; terminal = reinvestigated."""
    from pluang_agent.models import ReviewCategory

    stub = _Stub()
    # Phase 1
    stub.queue("planner_trace:", _trace_payload(chosen_aggregator="SUM"))
    stub.queue(
        "sql_agent:",
        _sql_payload(
            "SELECT SUM(gtv_idr) AS gtv_idr FROM fct_trading_daily", "gtv_idr", 3500.0
        ),
    )
    # Phase 2: revision = plan unchanged + revision_note
    stub.queue(
        "planner_revise:",
        _revised_plan_payload(
            metric_intent="gtv_idr",
            primary_column="gtv_idr",
            aggregator="SUM",
            required_output_columns=["gtv_idr"],
            revision_note="SQL has a typo; plan is correct.",
        ),
    )
    stub.queue("planner_trace:", _trace_payload(chosen_aggregator="SUM"))
    # Phase 2 SQL: same shape, different (corrected) SQL — reinvestigation
    # passes a correction context so the stage_tag is `sql_agent_retry:`.
    stub.queue(
        "sql_agent_retry:",
        _sql_payload(
            "SELECT SUM(gtv_idr) AS gtv_idr FROM fct_trading_daily", "gtv_idr", 3500.0
        ),
    )

    patch_review["category"] = ReviewCategory.ANSWER_WRONG
    patch_review["note"] = "the SQL has a typo on column name, please fix"

    sql_agent = SQLAgent(
        db_path=real_db,
        metadata=_metadata(),
        llm_client=stub,  # type: ignore[arg-type]
        metrics_registry=MetricsRegistry(entries={}),
    )
    quality_agent = QualityAgent(
        db_path=real_db,
        metrics_registry=MetricsRegistry(entries={}),
        llm_client=stub,  # type: ignore[arg-type]
    )
    question = BusinessQuestion(
        id="adhoc_test",
        text="total gtv in fct_trading_daily",
        metric="gtv_idr",
        period="October 2025",
    )
    result = run_pipeline([question], sql_agent, quality_agent, ReviewMode.INTERACTIVE)
    item = result.items[0]
    assert item.review_decision is not None
    assert item.review_decision.terminal_state == TerminalState.REINVESTIGATED
    # Plan unchanged → reinvestigated answer has same metric_intent shape
    assert item.reinvestigated_answer is not None


# ===========================================================================
# Failure mode: LLM produces invalid revision → audit with plan_revision_failed
# ===========================================================================


def test_reinvestigation_invalid_revision_routes_to_audit(
    monkeypatch, real_db: Path, patch_review
) -> None:
    """If the LLM emits a revised plan that fails validation (hallucinated
    table), the workflow routes to audit_required with
    audit_reason='plan_revision_failed'."""
    from pluang_agent.models import ReviewCategory

    stub = _Stub()
    stub.queue("planner_trace:", _trace_payload())
    stub.queue(
        "sql_agent:",
        _sql_payload("SELECT SUM(gtv_idr) AS gtv_idr FROM fct_trading_daily", "gtv_idr", 3500.0),
    )
    # Revision attempt with non-existent table — both attempts return same bad payload
    bad_payload = json.dumps(
        {
            "question_id": "adhoc_test",
            "metric_intent": "gtv_idr",
            "period": {"start": "2025-10-01", "end": "2025-11-01"},
            "answer_shape": "scalar",
            "primary_source": {
                "table": "ghost_table",
                "column": "gtv_idr",
                "period_column": "transaction_date",
                "aggregator": "SUM",
                "extra_filters": [],
                "reason": "hallucinated",
            },
            "comparison_sources": [],
            "breakdown": None,
            "required_output_columns": ["gtv_idr"],
            "required_definitions": [],
            "ambiguity_policy": "single_definition",
            "source_policy": "schema_grounded",
            "validation_rules": [],
        }
    )
    stub.queue("planner_revise:", bad_payload)
    stub.queue("planner_revise_retry:", bad_payload)

    patch_review["category"] = ReviewCategory.ANSWER_WRONG
    patch_review["note"] = "use a different source"

    sql_agent = SQLAgent(
        db_path=real_db,
        metadata=_metadata(),
        llm_client=stub,  # type: ignore[arg-type]
        metrics_registry=MetricsRegistry(entries={}),
    )
    quality_agent = QualityAgent(
        db_path=real_db,
        metrics_registry=MetricsRegistry(entries={}),
        llm_client=stub,  # type: ignore[arg-type]
    )
    question = BusinessQuestion(
        id="adhoc_test",
        text="gtv in fct_trading_daily",
        metric="gtv_idr",
        period="October 2025",
    )
    result = run_pipeline([question], sql_agent, quality_agent, ReviewMode.INTERACTIVE)
    item = result.items[0]
    assert item.review_decision is not None
    assert item.review_decision.terminal_state == TerminalState.AUDIT_REQUIRED
    assert item.review_decision.audit_reason == "plan_revision_failed"
