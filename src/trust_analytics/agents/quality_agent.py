"""Quality Agent — three-layer A/B/C orchestrator (Decision 4).

R3 ships generic Layer B (metrics.yml-driven cross-source reconciliation +
LLM-grounded hypothesis on disagreement). Layer A is real (rule-based);
Layer C remains rule-based composition for now (will become LLM-driven at R4
if scope allows — current rule logic is faithful to the trust-profile spec).

Per Decision 4: A/B/C are layers WITHIN one QA Agent. The state machine sees
a single QA call. C only ever sees structured A+B output, never raw data.
"""

from __future__ import annotations

from pathlib import Path

from trust_analytics.layer_b import run_layer_b
from trust_analytics.layer_c import run_layer_c
from trust_analytics.llm import LLMClient
from trust_analytics.metrics import MetricsRegistry
from trust_analytics.models import (
    LayerACheck,
    LayerAReport,
    LayerBReport,
    LayerCReport,
    QualityReport,
    QuestionPlan,
    SQLAgentAnswer,
    TrustDimensions,
    TrustProfile,
)
from trust_analytics.quality_rules import run_layer_a


class QualityAgent:
    def __init__(
        self,
        db_path: Path,
        metrics_registry: MetricsRegistry | None = None,
        llm_client: LLMClient | None = None,
    ):
        self.db_path = db_path
        self.metrics_registry = metrics_registry or MetricsRegistry(entries={})
        self.llm_client = llm_client

    def assess(
        self,
        answer: SQLAgentAnswer,
        question_plan: QuestionPlan | None = None,
    ) -> QualityReport:
        if answer.system_error is not None:
            return self._system_error_report(answer)

        metric_entry = self.metrics_registry.get(answer.question_id)
        layer_a = run_layer_a(
            answer,
            metric_entry=metric_entry,
            question_plan=question_plan,
        )
        layer_b = run_layer_b(
            db_path=self.db_path,
            answer=answer,
            registry=self.metrics_registry,
            llm_client=self.llm_client,
            question_plan=question_plan,
        )
        layer_c = run_layer_c(answer, layer_a, layer_b, llm_client=self.llm_client)

        return QualityReport(
            question_id=answer.question_id,
            layer_a=layer_a,
            layer_b=layer_b,
            layer_c=layer_c,
        )

    @staticmethod
    def _system_error_report(answer: SQLAgentAnswer) -> QualityReport:
        """When SQL Agent escalated, no real assessment is possible."""
        assert answer.system_error is not None  # for type-checker
        err = answer.system_error
        layer_a = LayerAReport(
            checks=[
                LayerACheck(
                    name=f"system_error_{err.error_class}",
                    result="FAIL",
                    detail=err.message,
                    evidence=[err.suggested_action],
                )
            ]
        )
        layer_b = LayerBReport(
            verdict="NOT_APPLICABLE",
            hypothesis_absence_note=(
                "Cross-source check skipped — SQL Agent escalated before producing an answer."
            ),
        )
        layer_c = LayerCReport(
            trust_profile=TrustProfile(
                dimensions=TrustDimensions(
                    correctness="RED", source_reliability="RED", ambiguity="RED"
                ),
                overall="RED",
                reviewer_summary=f"Pipeline halted — {err.error_class}: {err.message}",
            ),
            unresolved_questions=[err.suggested_action],
        )
        return QualityReport(
            question_id=answer.question_id,
            layer_a=layer_a,
            layer_b=layer_b,
            layer_c=layer_c,
        )
