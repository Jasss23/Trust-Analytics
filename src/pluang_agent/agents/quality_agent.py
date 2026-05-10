"""Quality Agent — three-layer A/B/C orchestrator (Decision 4).

R1 ships the orchestration shell. Layer A is real (rule-based); Layer B and C
are minimum-viable stubs at R1 — Layer B becomes generic metrics.yml-driven
reconciliation at R3, Layer C becomes LLM trust-profile composition at R2.

Per Decision 4 critical note: A/B/C are layers WITHIN one QA Agent. They are
NOT separate LangGraph nodes. The state machine sees a single QA call.
"""

from __future__ import annotations

from pathlib import Path

from pluang_agent.models import (
    LayerACheck,
    LayerAReport,
    LayerBReport,
    LayerCReport,
    QualityReport,
    SQLAgentAnswer,
    TrustDimensions,
    TrustProfile,
    Verdict,
)
from pluang_agent.quality_rules import run_layer_a


class QualityAgent:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def assess(self, answer: SQLAgentAnswer) -> QualityReport:
        if answer.system_error is not None:
            return self._system_error_report(answer)

        layer_a = run_layer_a(answer)
        layer_b = self._stub_layer_b(answer)
        layer_c = self._compose_layer_c(answer, layer_a, layer_b)

        return QualityReport(
            question_id=answer.question_id,
            layer_a=layer_a,
            layer_b=layer_b,
            layer_c=layer_c,
        )

    @staticmethod
    def _system_error_report(answer: SQLAgentAnswer) -> QualityReport:
        """When SQL Agent escalated, no real assessment is possible.

        Layer A emits a single FAIL noting the system error; B is N/A;
        C composes a RED trust profile and seeds unresolved_questions
        with the suggested action so the audit hand-off is actionable.
        """
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
        layer_b = LayerBReport(verdict="NOT_APPLICABLE", hypothesis_absence_note=(
            "Cross-source check skipped — SQL Agent escalated before producing an answer."
        ))
        layer_c = LayerCReport(
            trust_profile=TrustProfile(
                dimensions=TrustDimensions(correctness="RED", source_reliability="RED", ambiguity="RED"),
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

    @staticmethod
    def _stub_layer_b(answer: SQLAgentAnswer) -> LayerBReport:
        """R1 stub. R3 replaces this with metrics.yml-driven generic Layer B
        (look up alternative sources for the metric, run parallel SQL, compare,
        emit grounded hypothesis only when dbt SQL diff explains divergence)."""
        return LayerBReport(
            verdict="NOT_APPLICABLE",
            hypothesis_absence_note=(
                "Generic Layer B (metrics.yml-driven cross-source reconciliation) "
                "is not implemented at R1. R3 will populate this layer."
            ),
        )

    @staticmethod
    def _compose_layer_c(
        answer: SQLAgentAnswer,
        layer_a: LayerAReport,
        layer_b: LayerBReport,
    ) -> LayerCReport:
        """R1 stub composition: derive verdicts from Layer A pass/fail counts
        and Layer B verdict only. R2 replaces with an LLM-driven composition
        that takes structured A+B findings (no raw data) and produces
        reviewer_summary + unresolved_questions."""
        any_a_fail = any(check.result == "FAIL" for check in layer_a.checks)
        b_disagree = layer_b.verdict == "DISAGREEMENT"

        correctness: Verdict = "RED" if any_a_fail else "GREEN"
        source_reliability: Verdict = "RED" if b_disagree else "GREEN"
        ambiguity: Verdict = "YELLOW" if answer.interpretation_choices else "GREEN"

        # Overall = worst dimension (RED > YELLOW > GREEN).
        rank = {"GREEN": 0, "YELLOW": 1, "RED": 2}
        overall_rank = max(rank[correctness], rank[source_reliability], rank[ambiguity])
        overall: Verdict = ("GREEN", "YELLOW", "RED")[overall_rank]

        if any_a_fail:
            failed_names = [c.name for c in layer_a.checks if c.result == "FAIL"]
            summary = f"Layer A flagged: {', '.join(failed_names)}."
        elif answer.interpretation_choices:
            summary = (
                f"{len(answer.interpretation_choices)} interpretation choice(s) made; "
                "Layer A clean."
            )
        else:
            summary = "Layer A clean. Layer B reconciliation not yet implemented (R1 stub)."

        return LayerCReport(
            trust_profile=TrustProfile(
                dimensions=TrustDimensions(
                    correctness=correctness,
                    source_reliability=source_reliability,
                    ambiguity=ambiguity,
                ),
                overall=overall,
                reviewer_summary=summary,
            ),
            unresolved_questions=[],
        )
