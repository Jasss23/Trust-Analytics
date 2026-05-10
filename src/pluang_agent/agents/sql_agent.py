"""SQL Agent — LLM-driven, prompts loaded from files (V6 compliance).

Prompt engineering inspired by WrenAI's describe_schema() pattern:
full column descriptions (not just names) are injected as context so the
LLM can reason about source selection and date filters from business meaning,
not column name inference.

Error handling:
- Hard errors (auth/quota/transient): escalate immediately via SystemError.
- Soft errors (bad output/schema/SQL): one-shot repair attempt, then escalate.
  No silent fallback to deterministic oracle — V5.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from pluang_agent.llm import (
    HARD_ERROR_TYPES,
    LLMError,
    LLMOutputError,
    OpenRouterClient,
)
from pluang_agent.metadata import DbtMetadata, describe_schema_context
from pluang_agent.metrics import MetricEntry, MetricsRegistry, SourceSpec
from pluang_agent.models import (
    BusinessQuestion,
    SQLAgentAnswer,
    SystemError,
)
from pluang_agent.sql_runner import SQLSafetyError, execute_read_only

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_system_prompt() -> str:
    return (_PROMPTS_DIR / "sql_agent_system.md").read_text(encoding="utf-8")


def _load_user_template() -> str:
    return (_PROMPTS_DIR / "sql_agent_user.md").read_text(encoding="utf-8")


class SQLAgent:
    def __init__(
        self,
        db_path: Path,
        metadata: DbtMetadata,
        llm_client: OpenRouterClient,
        metrics_registry: MetricsRegistry | None = None,
    ):
        self.db_path = db_path
        self.metadata = metadata
        self.llm_client = llm_client
        self.metrics_registry = metrics_registry or MetricsRegistry(entries={})

    def answer(
        self,
        question: BusinessQuestion,
        reviewer_note: str | None = None,
    ) -> SQLAgentAnswer:
        try:
            return self._answer_with_llm(question, reviewer_note)
        except HARD_ERROR_TYPES as exc:
            return _escalate(question, exc)
        except LLMError as exc:
            return _escalate(question, exc)

    def _answer_with_llm(
        self,
        question: BusinessQuestion,
        reviewer_note: str | None,
    ) -> SQLAgentAnswer:
        system = _load_system_prompt()
        user = self._build_user_prompt(question, reviewer_note)

        # First attempt
        try:
            response = self.llm_client.chat_json(system, user, stage_tag=f"sql_agent:{question.id}")
            payload = _loads_json_object(response.content)
            payload = _adapt_payload(payload)
            answer = SQLAgentAnswer.model_validate(payload)
            answer.usage = response.usage
        except (LLMOutputError, ValidationError, json.JSONDecodeError, SQLSafetyError) as exc:
            # One-shot repair: feed the error + bad output back
            answer = self._repair_attempt(question, reviewer_note, exc, system, user)
            if answer.system_error is not None:
                return answer  # repair also failed — escalate

        # Execute the SQL
        try:
            rows = execute_read_only(self.db_path, answer.sql)
        except SQLSafetyError as exc:
            return _escalate_soft(question, exc)
        answer.result_rows = rows
        if answer.metric_value is None:
            answer.metric_value = rows
        return answer

    def _repair_attempt(
        self,
        question: BusinessQuestion,
        reviewer_note: str | None,
        original_exc: Exception,
        system: str,
        original_user: str,
    ) -> SQLAgentAnswer:
        """One-shot repair: append the error to the user prompt and retry once."""
        error_summary = f"{type(original_exc).__name__}: {str(original_exc)[:400]}"
        repair_user = (
            original_user
            + f"\n\n---\nPrevious attempt failed validation. Error: {error_summary}\n"
            "Fix the JSON structure and return a valid response. "
            "Ensure filters is a list of strings, interpretation_choices is a list of objects, "
            "and warnings/dq_notes are lists.\n---"
        )
        try:
            response = self.llm_client.chat_json(
                system, repair_user, stage_tag=f"sql_agent_repair:{question.id}"
            )
            payload = _loads_json_object(response.content)
            payload = _adapt_payload(payload)
            answer = SQLAgentAnswer.model_validate(payload)
            answer.usage = response.usage
            answer.warnings.append(
                f"First attempt failed ({type(original_exc).__name__}); repaired on second attempt."
            )
            return answer
        except (LLMOutputError, ValidationError, json.JSONDecodeError, SQLSafetyError) as exc2:
            return _escalate_soft(question, exc2)

    def _build_user_prompt(self, question: BusinessQuestion, reviewer_note: str | None) -> str:
        schema_ctx = describe_schema_context(self.metadata)
        registry_entry = _render_registry_entry(
            self.metrics_registry.get(question.id)
        )
        reviewer_block = (
            f"\nReviewer note for reinvestigation: {reviewer_note}" if reviewer_note else ""
        )
        template = _load_user_template()
        template_body = template.split("---\n", 1)[-1] if "---\n" in template else template
        return template_body.replace("{schema_context}", schema_ctx).replace(
            "{registry_entry}", registry_entry
        ).replace("{question_text}", question.text).replace(
            "{question_id}", question.id
        ).replace("{question_metric}", question.metric).replace(
            "{question_period}", question.period
        ).replace("{reviewer_note}", reviewer_block)


def _render_registry_entry(entry: MetricEntry | None) -> str:
    """Render a MetricEntry as a YAML-style block for the user prompt.

    Returns a no-entry note when the question is not registered (so the LLM
    can still fall back to schema-only reasoning).
    """
    if entry is None:
        return "(no metric registry entry — fall back to schema context only)"

    def fmt_source(label: str, src: SourceSpec) -> list[str]:
        return [
            f"  {label}:",
            f"    table: {src.table}",
            f"    column: {src.column}",
            f"    period_column: {src.period_column}",
            f"    extra_filters: {list(src.extra_filters)}",
            f"    breakdown: {src.breakdown}",
            f"    aggregator: {src.aggregator}",
            *([f"    notes: {src.notes}"] if src.notes else []),
        ]

    lines: list[str] = [
        f"id: {entry.id}",
        f"metric_name: {entry.metric_name}",
        f"cross_source: {entry.cross_source}",
        f"period_start: {entry.period_start}",
        f"period_end: {entry.period_end}",
        "primary:",
    ]
    lines.extend(fmt_source("(primary)", entry.primary)[1:])
    if entry.alternatives:
        lines.append("alternatives:")
        for i, alt in enumerate(entry.alternatives):
            lines.extend(fmt_source(f"alt_{i}", alt))
    if entry.notes_for_layer_b:
        lines.append(f"notes_for_layer_b: {entry.notes_for_layer_b}")
    return "\n".join(lines)


def _adapt_payload(payload: dict) -> dict:
    """Coerce common LLM-output drift into the contract.

    Despite the system prompt specifying list[str] for filters and list[obj]
    for interpretation_choices, models (especially less capable ones) will
    return dicts or plain strings. We adapt here rather than failing so the
    one-shot repair path is only needed for structural problems.
    """
    if "source" not in payload:
        tables = payload.pop("source_tables", None)
        if isinstance(tables, list) and tables:
            payload["source"] = {
                "primary_table": tables[0],
                "why_chosen": "Selected by LLM (no rationale provided).",
                "alternatives_available": tables[1:],
            }
    if "interpretation_choices" not in payload:
        notes = payload.pop("ambiguity_notes", None)
        if isinstance(notes, list) and notes:
            payload["interpretation_choices"] = [
                {"choice": note, "alternatives": [], "rationale": ""} for note in notes
            ]
        elif isinstance(notes, str) and notes.strip():
            payload["interpretation_choices"] = [
                {"choice": notes.strip(), "alternatives": [], "rationale": ""}
            ]
    for list_field in ("warnings", "dq_notes", "assumptions"):
        if list_field in payload and not isinstance(payload[list_field], list):
            raw = payload[list_field]
            payload[list_field] = [str(raw)] if str(raw).strip() else []
    if "filters" in payload and isinstance(payload["filters"], dict):
        # Flatten dict filters to list of "key: value" strings
        payload["filters"] = [f"{k}: {v}" for k, v in payload["filters"].items()]
    return payload


def _escalate(question: BusinessQuestion, exc: LLMError) -> SQLAgentAnswer:
    return SQLAgentAnswer(
        question_id=question.id,
        question=question.text,
        metric_name=question.metric,
        metric_value=None,
        period=question.period,
        source=None,
        sql="",
        filters=[],
        assumptions=[],
        logic="No answer produced — LLM call failed; pipeline escalated.",
        result_rows=[],
        interpretation_choices=[],
        dq_notes=[],
        warnings=[f"{type(exc).__name__}: {exc}"],
        system_error=SystemError(
            error_class=exc.error_class,  # type: ignore[arg-type]
            message=str(exc),
            suggested_action=exc.suggested_action,
            raw=type(exc).__name__,
        ),
    )


def _escalate_soft(question: BusinessQuestion, exc: Exception) -> SQLAgentAnswer:
    return SQLAgentAnswer(
        question_id=question.id,
        question=question.text,
        metric_name=question.metric,
        metric_value=None,
        period=question.period,
        source=None,
        sql="",
        filters=[],
        assumptions=[],
        logic="No answer produced — LLM output failed validation after repair attempt.",
        result_rows=[],
        interpretation_choices=[],
        dq_notes=[],
        warnings=[f"{type(exc).__name__}: {str(exc)[:300]}"],
        system_error=SystemError(
            error_class="output",
            message=str(exc)[:500],
            suggested_action=(
                "LLM returned content that did not validate after a one-shot repair. "
                "Inspect the prompt in prompts/sql_agent_system.md and the raw response; "
                "consider adding a stricter few-shot example or switching to a more capable model."
            ),
            raw=type(exc).__name__,
        ),
    )


def _loads_json_object(text: str) -> dict:
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise LLMOutputError(f"No JSON object found in model output: {text[:300]}") from None
        return json.loads(match.group(0))
