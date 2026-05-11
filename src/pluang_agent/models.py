"""Typed contracts shared by agents, review, and sample outputs.

Frozen at R1 per the refactor spec. Once these schemas hold, the SQL Agent
and the Quality Agent can be refactored independently because their wire
contract is locked.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ReviewMode(StrEnum):
    INTERACTIVE = "interactive"
    DEMO_APPROVE = "demo-approve"
    DEMO_REJECT = "demo-reject"


class ReviewCategory(StrEnum):
    ANSWER_WRONG = "answer_wrong"
    SOURCE_WRONG = "source_wrong"
    QA_INSUFFICIENT = "qa_insufficient"
    EXTERNAL_DISAGREEMENT = "external_disagreement"


class TerminalState(StrEnum):
    """Terminal states per Decision 6.

    ESCALATED is intentionally absent — system errors fold into AUDIT_REQUIRED
    with `audit_reason='system_error'`, so the spec's two-terminal enum holds.
    """

    APPROVED = "approved"
    REINVESTIGATED = "reinvestigated"
    AUDIT_REQUIRED = "audit_required"


class SystemError(BaseModel):
    """A non-data failure (auth/quota/transient/output/exec/pre_flight/retry) —
    distinct from QA flags.

    When set on an answer, the pipeline halts that question and routes to
    AUDIT_REQUIRED with `audit_reason='system_error'`. The reviewer sees the
    error class + suggested action.

    R5: `exec_failure` and `pre_flight_failure` are recoverable in principle
    — the workflow uses them as retry triggers and only surfaces them on the
    answer when the unified retry budget is exhausted (then class becomes
    `auto_retry_exhausted`).
    """

    error_class: Literal[
        "auth",
        "quota",
        "transient",
        "output",
        "llm_error",
        "exec_failure",
        "pre_flight_failure",
        "auto_retry_exhausted",
        "planner_validation_failed",
        "answer_shape_validation_failed",
    ]
    message: str
    suggested_action: str
    raw: str | None = None


class BusinessQuestion(BaseModel):
    id: str
    text: str
    metric: str
    period: str


AnswerShape = Literal[
    "scalar",
    "breakdown",
    "multi_definition",
    "time_series",
    "period_over_period",
    "breakdown_comparison",
]


class PlanPeriod(BaseModel):
    start: str
    end: str


class PlanSource(BaseModel):
    table: str
    column: str
    period_column: str
    aggregator: Literal["SUM", "COUNT_DISTINCT", "RAW"] = "SUM"
    extra_filters: list[str] = Field(default_factory=list)
    reason: str


class PlanBreakdown(BaseModel):
    dimension: str
    exclude_aggregate_members: list[str] = Field(default_factory=list)


class QuestionPlan(BaseModel):
    question_id: str
    metric_intent: str
    period: PlanPeriod
    answer_shape: AnswerShape
    primary_source: PlanSource
    comparison_sources: list[PlanSource] = Field(default_factory=list)
    breakdown: PlanBreakdown | None = None
    required_output_columns: list[str] = Field(default_factory=list)
    required_definitions: list[str] = Field(default_factory=list)
    ambiguity_policy: str = "single_definition"
    source_policy: str = "canonical"
    validation_rules: list[str] = Field(default_factory=list)


class UsageRecord(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None
    model: str | None = None


# ---------------------------------------------------------------------------
# SQL Agent contract — per spec 3.1
# ---------------------------------------------------------------------------


class SourceProvenance(BaseModel):
    """Why a particular table was chosen as primary, and what alternatives exist."""

    primary_table: str
    why_chosen: str
    alternatives_available: list[str] = Field(default_factory=list)


class InterpretationChoice(BaseModel):
    """A point of metric/source ambiguity that the agent resolved deliberately.

    Surfacing these is the system's answer to the brief's "graceful handling
    of ambiguity" principle — the agent must declare the choice it made, the
    alternatives it rejected, and why.
    """

    choice: str
    alternatives: list[str] = Field(default_factory=list)
    rationale: str


class SQLAgentAnswer(BaseModel):
    question_id: str
    question: str
    metric_name: str
    metric_value: Any
    period: str
    source: SourceProvenance | None = None
    sql: str
    # filters: dict (e.g. {"month": "2025-10"}) or list[str] — both are
    # acceptable per real LLM output observed during R0 validation. We accept
    # either to avoid prompt-fragility lockstep.
    filters: dict[str, Any] | list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    logic: str
    result_rows: list[dict[str, Any]] = Field(default_factory=list)
    interpretation_choices: list[InterpretationChoice] = Field(default_factory=list)
    dq_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: UsageRecord | None = None
    system_error: SystemError | None = None


# ---------------------------------------------------------------------------
# QA Agent contract — three-layer per Decision 4
# ---------------------------------------------------------------------------


Verdict = Literal["GREEN", "YELLOW", "RED"]
"""Per-dimension and overall trust verdict."""


CheckResult = Literal["PASS", "FAIL", "NOT_APPLICABLE"]


class LayerACheck(BaseModel):
    """One rule-based check from Layer A. Designed for high precision over recall:
    if a check FAILs, there's definitely a problem; PASS does not mean clean."""

    name: str
    result: CheckResult
    detail: str | None = None
    evidence: list[str] = Field(default_factory=list)


class LayerAReport(BaseModel):
    checks: list[LayerACheck] = Field(default_factory=list)


class CrossSourceFinding(BaseModel):
    """One source's value for the metric, for cross-source comparison in Layer B."""

    source: str
    value: Any
    delta_vs_primary: float | None = None
    notes: str | None = None


class Hypothesis(BaseModel):
    """A grounded hypothesis about WHY sources disagree.

    `evidence` must be non-empty when a hypothesis is present (Decision 4:
    "Hypothesis must cite specific evidence"). `what_this_does_not_explain`
    is required so the reviewer sees the hypothesis's blast radius.
    """

    proposal: str
    evidence: list[str]
    confidence: Literal["HIGH", "MED", "LOW"]
    what_this_does_not_explain: str


class LayerBReport(BaseModel):
    cross_source_findings: list[CrossSourceFinding] = Field(default_factory=list)
    verdict: Literal["AGREE", "DISAGREEMENT", "NOT_APPLICABLE"]
    hypothesis: Hypothesis | None = None
    hypothesis_absence_note: str | None = None


class TrustDimensions(BaseModel):
    correctness: Verdict
    source_reliability: Verdict
    ambiguity: Verdict


class TrustProfile(BaseModel):
    dimensions: TrustDimensions
    overall: Verdict
    reviewer_summary: str


class LayerCReport(BaseModel):
    trust_profile: TrustProfile
    unresolved_questions: list[str] = Field(default_factory=list)


class QualityReport(BaseModel):
    question_id: str
    layer_a: LayerAReport
    layer_b: LayerBReport
    layer_c: LayerCReport


# ---------------------------------------------------------------------------
# Pipeline / human review
# ---------------------------------------------------------------------------


class ReviewDecision(BaseModel):
    question_id: str
    decision: Literal["approve", "reject"]
    category: ReviewCategory | None = None
    note: str | None = None
    terminal_state: TerminalState | None = None
    # When terminal_state == AUDIT_REQUIRED, audit_reason explains why.
    # 'system_error' is the absorbed-ESCALATED case; other reasons:
    # 'external_disagreement', 'retry_exhausted' (R5: unified budget),
    # 'auto_retry_exhausted'.
    audit_reason: str | None = None
    # R5: per-category retry counters were removed in favour of a single
    # unified budget on PipelineItem.remaining_budget. Categories still
    # drive *what* to do on retry, but not *how many* tries remain.


AttemptStatus = Literal[
    "success",
    "llm_hard_failure",
    "llm_soft_failure",
    "exec_failure",
    "pre_flight_failure",
]
"""Outcome classifications for one run of run_one_attempt (R5)."""


class CorrectionContext(BaseModel):
    """Carried into the next SQL Agent attempt when retrying after a failure.

    The agent's user prompt grows a `## Correction` block built from these
    fields. Schema-grounded retry (R5): `schema_hint` carries live PRAGMA
    table_info output for the tables referenced by the previous attempt, so
    the agent sees actual columns rather than guessing.
    """

    prev_sql: str
    failure_kind: Literal[
        "llm_soft_failure",
        "exec_failure",
        "pre_flight_failure",
        "human_reject",
    ]
    failure_detail: str
    schema_hint: str | None = None
    reviewer_note: str | None = None


class AttemptOutcome(BaseModel):
    """One pass through run_one_attempt: LLM call → execute → pre-flight.

    Always carries an `answer`. The `status` distinguishes success from the
    several retry-eligible failure modes; `correction_context` is set iff
    the status is retry-eligible.
    """

    answer: SQLAgentAnswer
    status: AttemptStatus
    correction_context: CorrectionContext | None = None


class AuditHandoff(BaseModel):
    """Hand-off package emitted when a question enters AUDIT_REQUIRED.

    Per Decision 6: audit_required is not 'failed' — it's an active hand-off
    to humans with all the context needed to make progress.
    """

    question_id: str
    attempted_answers: list[SQLAgentAnswer]
    quality_reports: list[QualityReport]
    reject_history: list[ReviewDecision]
    unresolved_questions: list[str]
    attempts: list[AttemptOutcome] = Field(default_factory=list)


class PipelineItem(BaseModel):
    question: BusinessQuestion
    question_plan: QuestionPlan | None = None
    answer: SQLAgentAnswer
    quality_report: QualityReport
    review_decision: ReviewDecision | None = None
    reinvestigated_answer: SQLAgentAnswer | None = None
    reinvestigated_quality_report: QualityReport | None = None
    audit_handoff: AuditHandoff | None = None
    # R5: unified retry budget governing auto-retries (exec / pre-flight)
    # AND human-rejection retries. Starts at auto_retry_budget, decremented
    # on each retry. Exhaustion → AUDIT_REQUIRED with auto_retry_exhausted.
    auto_retry_budget: int = 2
    remaining_budget: int = 2
    # All attempts made for this question, including the successful one.
    # Populated by the workflow; surfaced in AuditHandoff.attempts.
    attempts: list[AttemptOutcome] = Field(default_factory=list)


class PipelineResult(BaseModel):
    items: list[PipelineItem]
    review_mode: ReviewMode
    terminal_summary: dict[str, int]
