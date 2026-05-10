"""Layer C — Trust profile composition (LLM-driven, with rule-based fallback).

Per Decision 4: Layer C "takes structured findings from A and B and composes
the reviewer-facing trust profile. C does NOT see raw data — it sees only
structured findings. This isolation is what makes C's output auditable."

This module ships:
- `run_layer_c()` — primary entry. Calls the LLM with the prompt at
  prompts/qa_layer_c_compose.md and structured A+B inputs.
- `_compose_layer_c_rule_based()` — deterministic fallback used when (a) no LLM
  client is provided, or (b) the LLM call fails or returns invalid JSON. Mirrors
  the verdict rubric in the prompt so behaviour is consistent across modes.
"""

from __future__ import annotations

import json
from pathlib import Path

from pluang_agent.llm import LLMClient, LLMOutputError
from pluang_agent.models import (
    LayerAReport,
    LayerBReport,
    LayerCReport,
    SQLAgentAnswer,
    TrustDimensions,
    TrustProfile,
    Verdict,
)

_PROMPTS_DIR = Path(__file__).parents[2] / "prompts"


def run_layer_c(
    answer: SQLAgentAnswer,
    layer_a: LayerAReport,
    layer_b: LayerBReport,
    llm_client: LLMClient | None = None,
) -> LayerCReport:
    """Compose a TrustProfile from Layer A + Layer B findings.

    LLM is preferred when available; falls back to rule-based composition on
    any failure (no client, soft errors, malformed JSON, hard provider errors).
    Fallback preserves the mock-mode-first invariant from R1: tests can run
    without a real LLM.
    """
    if llm_client is None or not getattr(llm_client, "available", False):
        return _compose_layer_c_rule_based(answer, layer_a, layer_b)

    try:
        return _compose_layer_c_with_llm(answer, layer_a, layer_b, llm_client)
    except Exception:  # noqa: BLE001 — any LLM failure → fallback
        return _compose_layer_c_rule_based(answer, layer_a, layer_b)


# ---------------------------------------------------------------------------
# LLM-driven composition
# ---------------------------------------------------------------------------


def _compose_layer_c_with_llm(
    answer: SQLAgentAnswer,
    layer_a: LayerAReport,
    layer_b: LayerBReport,
    llm_client: LLMClient,
) -> LayerCReport:
    system = (_PROMPTS_DIR / "qa_layer_c_compose.md").read_text(encoding="utf-8")
    user = _build_user_prompt(answer, layer_a, layer_b)
    response = llm_client.chat_json(system, user, stage_tag=f"qa_layer_c:{answer.question_id}")
    payload = _parse_json(response.content)
    tp = payload["trust_profile"]
    return LayerCReport(
        trust_profile=TrustProfile(
            dimensions=TrustDimensions(**tp["dimensions"]),
            overall=tp["overall"],
            reviewer_summary=tp["reviewer_summary"],
        ),
        unresolved_questions=list(payload.get("unresolved_questions") or []),
    )


def _build_user_prompt(
    answer: SQLAgentAnswer,
    layer_a: LayerAReport,
    layer_b: LayerBReport,
) -> str:
    a_block = "\n".join(
        f"  - {c.name}: {c.result}"
        + (f" — {c.detail}" if c.detail else "")
        for c in layer_a.checks
    ) or "  (no checks)"

    if layer_b.hypothesis is not None:
        h = layer_b.hypothesis
        b_hypothesis = (
            f"  hypothesis:\n"
            f"    proposal: {h.proposal}\n"
            f"    confidence: {h.confidence}\n"
            f"    evidence: {h.evidence[:5]}\n"
            f"    what_this_does_not_explain: {h.what_this_does_not_explain}"
        )
    else:
        b_hypothesis = (
            f"  hypothesis: null\n"
            f"  hypothesis_absence_note: {layer_b.hypothesis_absence_note or '(none)'}"
        )

    interpretation_block = (
        f"  count: {len(answer.interpretation_choices)}\n"
        + (
            f"  first_proposal: {answer.interpretation_choices[0].choice}"
            if answer.interpretation_choices
            else "  first_proposal: (none)"
        )
    )

    return (
        f"## question\n"
        f"  question_id: {answer.question_id}\n"
        f"  metric_name: {answer.metric_name}\n"
        f"  period: {answer.period}\n\n"
        f"## interpretation_choices\n{interpretation_block}\n\n"
        f"## layer_a\n{a_block}\n\n"
        f"## layer_b\n"
        f"  verdict: {layer_b.verdict}\n"
        f"  n_findings: {len(layer_b.cross_source_findings)}\n"
        f"{b_hypothesis}\n\n"
        f"Compose the trust profile JSON now."
    )


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        import re

        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"Layer C JSON invalid: {exc}") from exc


# ---------------------------------------------------------------------------
# Rule-based fallback (deterministic)
# ---------------------------------------------------------------------------


def _compose_layer_c_rule_based(
    answer: SQLAgentAnswer,
    layer_a: LayerAReport,
    layer_b: LayerBReport,
) -> LayerCReport:
    """Deterministic fallback. Same verdict rubric the prompt encodes."""
    a_fail_names = [c.name for c in layer_a.checks if c.result == "FAIL"]
    any_a_fail = bool(a_fail_names)
    b_disagree = layer_b.verdict == "DISAGREEMENT"
    b_n_a = layer_b.verdict == "NOT_APPLICABLE"

    correctness: Verdict = "RED" if any_a_fail else "GREEN"
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

    parts: list[str] = []
    if any_a_fail:
        parts.append(f"Layer A flagged: {', '.join(a_fail_names)}.")
    else:
        parts.append("Layer A clean.")
    if b_disagree:
        n = len(layer_b.cross_source_findings)
        parts.append(f"Layer B: {n} cross-source findings show DISAGREEMENT.")
        if layer_b.hypothesis is not None:
            parts.append(
                f"Hypothesis ({layer_b.hypothesis.confidence}): {layer_b.hypothesis.proposal}"
            )
        elif layer_b.hypothesis_absence_note:
            parts.append(f"No hypothesis: {layer_b.hypothesis_absence_note}")
    elif b_n_a:
        note = layer_b.hypothesis_absence_note or "Layer B not applicable."
        parts.append(f"Layer B: {note}")
    else:
        parts.append("Layer B: sources agree within threshold.")
    if answer.interpretation_choices:
        parts.append(f"{len(answer.interpretation_choices)} interpretation choice(s) declared.")

    summary = " ".join(parts)

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
            reviewer_summary=summary,
        ),
        unresolved_questions=unresolved,
    )
