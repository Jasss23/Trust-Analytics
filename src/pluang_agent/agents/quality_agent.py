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

from pluang_agent.layer_b import run_layer_b
from pluang_agent.llm import LLMClient
from pluang_agent.metrics import MetricsRegistry
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
    def __init__(
        self,
        db_path: Path,
        metrics_registry: MetricsRegistry | None = None,
        llm_client: LLMClient | None = None,
    ):
        self.db_path = db_path
        self.metrics_registry = metrics_registry or MetricsRegistry(entries={})
        self.llm_client = llm_client

    def assess(self, answer: SQLAgentAnswer) -> QualityReport:
        if answer.system_error is not None:
            return self._system_error_report(answer)

        layer_a = run_layer_a(answer)
        layer_b = run_layer_b(
            db_path=self.db_path,
            answer=answer,
            registry=self.metrics_registry,
            llm_client=self.llm_client,
        )
        layer_c = self._compose_layer_c(answer, layer_a, layer_b)

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

    @staticmethod
    def _compose_layer_c(
        answer: SQLAgentAnswer,
        layer_a: LayerAReport,
        layer_b: LayerBReport,
    ) -> LayerCReport:
        """Compose the trust profile from Layer A + Layer B structured findings.

        Decision 3: trust profile makes credibility dimensions explicit. Decision 4:
        Layer C sees only structured A+B output — never raw data.
        """
        a_fail_names = [c.name for c in layer_a.checks if c.result == "FAIL"]
        any_a_fail = bool(a_fail_names)
        b_disagree = layer_b.verdict == "DISAGREEMENT"
        b_n_a = layer_b.verdict == "NOT_APPLICABLE"

        correctness: Verdict = "RED" if any_a_fail else "GREEN"

        # source_reliability: RED on disagreement, YELLOW when N/A (we lack
        # evidence either way), GREEN when sources agree.
        if b_disagree:
            source_reliability: Verdict = "RED"
        elif b_n_a:
            source_reliability = "YELLOW"
        else:
            source_reliability = "GREEN"

        ambiguity: Verdict = "YELLOW" if answer.interpretation_choices else "GREEN"

        rank = {"GREEN": 0, "YELLOW": 1, "RED": 2}
        overall_rank = max(rank[correctness], rank[source_reliability], rank[ambiguity])
        overall: Verdict = ("GREEN", "YELLOW", "RED")[overall_rank]

        # Reviewer summary — structured prose drawn from A + B findings.
        summary_parts: list[str] = []
        if any_a_fail:
            summary_parts.append(f"Layer A flagged: {', '.join(a_fail_names)}.")
        else:
            summary_parts.append("Layer A clean.")

        if b_disagree:
            n_findings = len(layer_b.cross_source_findings)
            summary_parts.append(
                f"Layer B: {n_findings} cross-source findings show DISAGREEMENT."
            )
            if layer_b.hypothesis is not None:
                summary_parts.append(
                    f"Hypothesis ({layer_b.hypothesis.confidence}): {layer_b.hypothesis.proposal}"
                )
            elif layer_b.hypothesis_absence_note:
                summary_parts.append(f"No hypothesis: {layer_b.hypothesis_absence_note}")
        elif b_n_a:
            note = layer_b.hypothesis_absence_note or "Layer B not applicable."
            summary_parts.append(f"Layer B: {note}")
        else:
            summary_parts.append("Layer B: sources agree within threshold.")

        if answer.interpretation_choices:
            summary_parts.append(
                f"{len(answer.interpretation_choices)} interpretation choice(s) declared."
            )

        reviewer_summary = " ".join(summary_parts)

        # unresolved_questions — populated when verdict is RED, drawing from
        # the things the reviewer would need to know to act.
        unresolved: list[str] = []
        if any_a_fail:
            for name in a_fail_names:
                detail = next(
                    (c.detail for c in layer_a.checks if c.name == name and c.detail),
                    None,
                )
                if detail:
                    unresolved.append(f"Resolve Layer A failure '{name}': {detail}")
        if b_disagree and layer_b.hypothesis is None:
            unresolved.append(
                "Layer B found cross-source disagreement but no grounded hypothesis. "
                "Reviewer should investigate which source is authoritative."
            )

        return LayerCReport(
            trust_profile=TrustProfile(
                dimensions=TrustDimensions(
                    correctness=correctness,
                    source_reliability=source_reliability,
                    ambiguity=ambiguity,
                ),
                overall=overall,
                reviewer_summary=reviewer_summary,
            ),
            unresolved_questions=unresolved,
        )
