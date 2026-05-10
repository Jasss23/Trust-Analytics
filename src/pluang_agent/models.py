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
    """A non-data failure (auth/quota/transient/output) — distinct from QA flags.

    When set on an answer, the pipeline halts that question and routes to
    AUDIT_REQUIRED with `audit_reason='system_error'`. The reviewer sees the
    error class + suggested action; retrying without fixing the integration
    would just re-fail.
    """

    error_class: Literal["auth", "quota", "transient", "output", "llm_error"]
    message: str
    suggested_action: str
    raw: str | None = None


class BusinessQuestion(BaseModel):
    id: str
    text: str
    metric: str
    period: str


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
    # Per-category retry counters (Decision 6: retry-once per category).
    # Stored on the decision so it travels with the item across nodes.
    per_category_retry_counts: dict[ReviewCategory, int] = Field(default_factory=dict)
    terminal_state: TerminalState | None = None
    # When terminal_state == AUDIT_REQUIRED, audit_reason explains why.
    # 'system_error' is the absorbed-ESCALATED case; other reasons can be
    # added (e.g., 'external_disagreement', 'retry_exhausted').
    audit_reason: str | None = None


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


class PipelineItem(BaseModel):
    question: BusinessQuestion
    answer: SQLAgentAnswer
    quality_report: QualityReport
    review_decision: ReviewDecision | None = None
    reinvestigated_answer: SQLAgentAnswer | None = None
    reinvestigated_quality_report: QualityReport | None = None
    audit_handoff: AuditHandoff | None = None


class PipelineResult(BaseModel):
    items: list[PipelineItem]
    review_mode: ReviewMode
    terminal_summary: dict[str, int]
