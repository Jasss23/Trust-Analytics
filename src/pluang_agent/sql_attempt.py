"""One attempt of the SQL Agent → execute → pre-flight cycle (R5).

This module is the state machine's per-attempt unit. It does NOT loop —
that's the workflow node's job. `run_one_attempt` calls the agent once,
executes the produced SQL, runs the pre-flight gate, and returns a
structured AttemptOutcome that tells the caller whether this attempt
succeeded and (if not) what the correction context for the next attempt
should be.

Splitting these concerns moves the retry policy out of the agent (V5 spec
clean-up) and gives the workflow a single place to reason about retry
budget exhaustion.
"""

from __future__ import annotations

from pathlib import Path

from pluang_agent.agents.sql_agent import SQLAgent
from pluang_agent.db import columns_for_tables
from pluang_agent.metrics import MetricsRegistry
from pluang_agent.models import (
    AttemptOutcome,
    BusinessQuestion,
    CorrectionContext,
    DerivationTrace,
    QuestionPlan,
    SQLAgentAnswer,
)
from pluang_agent.planner import apply_trace_to_answer
from pluang_agent.pre_flight import pre_flight_check
from pluang_agent.sql_runner import SQLSafetyError, execute_read_only

HARD_ERROR_CLASSES = {"auth", "quota", "transient"}


def run_one_attempt(
    sql_agent: SQLAgent,
    question: BusinessQuestion,
    db_path: Path,
    registry: MetricsRegistry,
    question_plan: QuestionPlan | None = None,
    derivation_trace: DerivationTrace | None = None,
    correction_context: CorrectionContext | None = None,
    reviewer_note: str | None = None,
) -> AttemptOutcome:
    """One pass: LLM call → execute → pre-flight → AttemptOutcome.

    Returns success when all three stages pass. Returns a retry-eligible
    outcome (with correction_context populated) on soft LLM failure,
    execution failure, or pre-flight failure. Returns llm_hard_failure
    (no correction_context) on auth/quota/transient — the workflow
    routes these straight to AUDIT_REQUIRED without retrying.

    R6: when `derivation_trace` is provided, the LLM-authored
    `source` and `source.why_chosen` are overwritten by planner-derived
    values (apply_trace_to_answer). The trace is also threaded into the
    pre-flight gate for trace-driven shape checks.
    """
    answer = sql_agent.answer(
        question,
        question_plan=question_plan,
        reviewer_note=reviewer_note,
        correction_context=correction_context,
    )

    # Branch on system_error first — the agent surfaces both hard and soft
    # failures through answer.system_error.
    if answer.system_error is not None:
        if answer.system_error.error_class in HARD_ERROR_CLASSES:
            return AttemptOutcome(
                answer=answer,
                status="llm_hard_failure",
                correction_context=None,
            )
        # Soft failure (class='output' or future soft classes). Build a
        # correction context so the next attempt knows what failed.
        return AttemptOutcome(
            answer=answer,
            status="llm_soft_failure",
            correction_context=CorrectionContext(
                prev_sql="",
                failure_kind="llm_soft_failure",
                failure_detail=answer.system_error.message,
                schema_hint=None,
                reviewer_note=reviewer_note,
            ),
        )

    # Execute the produced SQL.
    try:
        rows = execute_read_only(db_path, answer.sql)
    except SQLSafetyError as exc:
        schema_hint = columns_for_tables(db_path, _tables_referenced(answer.sql))
        return AttemptOutcome(
            answer=answer,
            status="exec_failure",
            correction_context=CorrectionContext(
                prev_sql=answer.sql,
                failure_kind="exec_failure",
                failure_detail=str(exc),
                schema_hint=schema_hint,
                reviewer_note=reviewer_note,
            ),
        )

    answer.result_rows = rows
    answer.metric_value = rows

    # R6: apply the trace post-hoc — overwrites LLM-authored source/why_chosen
    # with planner-derived values. Idempotent; no-op when trace is None.
    if derivation_trace is not None:
        apply_trace_to_answer(answer, derivation_trace)

    # Pre-flight gate — before QA sees the answer.
    pre = pre_flight_check(answer, question, registry, question_plan=question_plan)
    if not pre.passed:
        return AttemptOutcome(
            answer=answer,
            status="pre_flight_failure",
            correction_context=CorrectionContext(
                prev_sql=answer.sql,
                failure_kind="pre_flight_failure",
                failure_detail=f"{pre.issue}: {pre.hint or ''}",
                schema_hint=None,
                reviewer_note=reviewer_note,
            ),
        )

    return AttemptOutcome(answer=answer, status="success", correction_context=None)


def _tables_referenced(sql: str) -> list[str]:
    """Best-effort table-name extraction for schema-hint injection.

    Simple regex on FROM / JOIN — good enough for retry correction context.
    We don't need 100% accuracy: the schema hint will include the wrong
    tables harmlessly (the model ignores irrelevant entries) and the LLM
    can still see the full schema context above for canonical reference.
    """
    import re

    tokens: list[str] = []
    # Match FROM <table> and JOIN <table>. Skip subqueries (FROM ().
    pattern = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
    for match in pattern.finditer(sql):
        name = match.group(1)
        if name.lower() not in ("select", "where", "group", "order", "limit"):
            tokens.append(name)
    return tokens


def is_retry_eligible(outcome: AttemptOutcome) -> bool:
    """A retry would have a chance of helping (correction context present)."""
    return outcome.status in {
        "llm_soft_failure",
        "exec_failure",
        "pre_flight_failure",
    }


def mark_budget_exhausted(answer: SQLAgentAnswer, outcome: AttemptOutcome) -> None:
    """Convert the last (failed) attempt into a SystemError stamp so the
    pipeline escalates to AUDIT_REQUIRED with auto_retry_exhausted.

    Mutates the answer in place — the workflow uses this just before
    returning an exhausted item.
    """
    from pluang_agent.models import SystemError as _SystemError

    detail = ""
    if outcome.correction_context is not None:
        detail = outcome.correction_context.failure_detail
    answer.system_error = _SystemError(
        error_class="auto_retry_exhausted",
        message=(
            f"Unified retry budget exhausted after {outcome.status}. "
            f"Last failure: {detail[:300]}"
        ),
        suggested_action=(
            "Inspect the captured attempts in the audit hand-off package. "
            "Either fix the underlying metric registry entry / instructions "
            "warning, expand the schema context, or escalate to a human "
            "analyst for the question."
        ),
        raw=outcome.status,
    )
