"""Quality Agent implementation."""

from __future__ import annotations

from pathlib import Path

from pluang_agent.models import QualityReport, SQLAgentAnswer
from pluang_agent.quality_rules import generic_value_flags, reconciliation_checks


class QualityAgent:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def assess(self, answer: SQLAgentAnswer) -> QualityReport:
        flags = generic_value_flags(answer)
        recon_flags, hypotheses, cross_checks = reconciliation_checks(self.db_path, answer)
        flags.extend(recon_flags)

        if flags:
            summary = f"{len(flags)} quality flag(s) found. Hypotheses are included only where evidence exists."
        else:
            summary = "No quality flags found by deterministic checks or configured source reconciliation."

        return QualityReport(
            question_id=answer.question_id,
            flags=flags,
            hypotheses=hypotheses,
            cross_checks=cross_checks,
            summary=summary,
        )
