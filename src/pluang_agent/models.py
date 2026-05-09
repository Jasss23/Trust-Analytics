"""Typed contracts shared by agents, review, and sample outputs."""

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
    APPROVED = "approved"
    REINVESTIGATED = "reinvestigated"
    AUDIT_REQUIRED = "audit_required"


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


class SQLAgentAnswer(BaseModel):
    question_id: str
    question: str
    metric_name: str
    metric_value: Any
    period: str
    source_tables: list[str]
    filters: list[str]
    assumptions: list[str]
    logic: str
    sql: str
    result_rows: list[dict[str, Any]] = Field(default_factory=list)
    ambiguity_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: UsageRecord | None = None


class QualityFlag(BaseModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    known_issue: str
    evidence: list[str] = Field(default_factory=list)


class QualityHypothesis(BaseModel):
    flag_code: str
    suspected_cause: str
    evidence: list[str] = Field(default_factory=list)


class CrossCheck(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail"]
    evidence: list[str] = Field(default_factory=list)


class QualityReport(BaseModel):
    question_id: str
    flags: list[QualityFlag] = Field(default_factory=list)
    hypotheses: list[QualityHypothesis] = Field(default_factory=list)
    cross_checks: list[CrossCheck] = Field(default_factory=list)
    summary: str


class ReviewDecision(BaseModel):
    question_id: str
    decision: Literal["approve", "reject"]
    category: ReviewCategory | None = None
    note: str | None = None
    retry_count: int = 0
    terminal_state: TerminalState | None = None


class PipelineItem(BaseModel):
    question: BusinessQuestion
    answer: SQLAgentAnswer
    quality_report: QualityReport
    review_decision: ReviewDecision | None = None
    reinvestigated_answer: SQLAgentAnswer | None = None
    reinvestigated_quality_report: QualityReport | None = None


class PipelineResult(BaseModel):
    items: list[PipelineItem]
    review_mode: ReviewMode
    terminal_summary: dict[str, int]
