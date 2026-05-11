"""SQL Agent — one LLM call per invocation (R5).

R5 changes vs the R3.5 shape:
- Single attempt: the agent no longer runs an internal repair loop. Retry
  policy lives in the state machine (workflow / sql_attempt.run_one_attempt).
- SQL execution moves out: this module produces a SQL string and never
  touches the database. Execution + pre-flight are run by sql_attempt.
- `correction_context` parameter: when the workflow retries an attempt, it
  passes a CorrectionContext built from the previous failure (exec error
  with schema hint, pre-flight failure, or human reject). The agent renders
  a `## Correction` block into the user prompt; the model is expected to
  produce a *different* SQL that addresses the specific failure.

Prompt engineering principle: domain knowledge lives in YAML (metrics.yml +
instructions.yml). The system prompt carries process rules only, plus an
empty JSON output skeleton — no Pluang-specific tables or rules baked into
the prompt.
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
    CorrectionContext,
    QuestionPlan,
    SQLAgentAnswer,
    SystemError,
)
from pluang_agent.sql_runner import SQLSafetyError

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
        question_plan: QuestionPlan | None = None,
        reviewer_note: str | None = None,
        correction_context: CorrectionContext | None = None,
    ) -> SQLAgentAnswer:
        """One LLM call → parsed SQLAgentAnswer. Does NOT execute SQL.

        On hard LLM error (auth/quota/transient): returns SQLAgentAnswer with
        system_error set to that class. On soft error (parse/validation/SQL
        safety on the SQL string): returns SQLAgentAnswer with system_error
        class='output'. The workflow inspects `error_class` to decide retry
        vs. immediate escalation.
        """
        try:
            return self._attempt_once(question, question_plan, reviewer_note, correction_context)
        except HARD_ERROR_TYPES as exc:
            return _escalate_hard(question, exc)
        except (LLMOutputError, ValidationError, json.JSONDecodeError, SQLSafetyError) as exc:
            return _make_soft_failure_answer(question, exc)
        except LLMError as exc:
            return _escalate_hard(question, exc)

    def _attempt_once(
        self,
        question: BusinessQuestion,
        question_plan: QuestionPlan | None,
        reviewer_note: str | None,
        correction_context: CorrectionContext | None,
    ) -> SQLAgentAnswer:
        system = _load_system_prompt()
        user = self._build_user_prompt(question, question_plan, reviewer_note, correction_context)
        stage = (
            f"sql_agent:{question.id}"
            if correction_context is None
            else f"sql_agent_retry:{question.id}"
        )
        response = self.llm_client.chat_json(system, user, stage_tag=stage)
        payload = _loads_json_object(response.content)
        payload = _adapt_payload(payload)
        answer = SQLAgentAnswer.model_validate(payload)
        answer.usage = response.usage
        # Validate the SQL string is well-formed read-only SQL before handing
        # it off; this catches SAFETY issues early (the exec path will catch
        # runtime issues like missing columns).
        from pluang_agent.sql_runner import validate_read_only_sql

        validate_read_only_sql(answer.sql)
        return answer

    def _build_user_prompt(
        self,
        question: BusinessQuestion,
        question_plan: QuestionPlan | None,
        reviewer_note: str | None,
        correction_context: CorrectionContext | None,
    ) -> str:
        schema_ctx = describe_schema_context(self.metadata)
        registry_entry = _render_registry_entry(
            self.metrics_registry.get(question.id)
        )
        reviewer_block = (
            f"\nReviewer note for reinvestigation: {reviewer_note}" if reviewer_note else ""
        )
        correction_block = _render_correction_block(correction_context)
        template = _load_user_template()
        template_body = template.split("---\n", 1)[-1] if "---\n" in template else template
        return (
            template_body
            .replace("{schema_context}", schema_ctx)
            .replace("{registry_entry}", registry_entry)
            .replace("{question_plan}", _render_question_plan(question_plan))
            .replace("{question_text}", question.text)
            .replace("{question_id}", question.id)
            .replace("{question_metric}", question.metric)
            .replace("{question_period}", question.period)
            .replace("{reviewer_note}", reviewer_block)
            .replace("{correction_block}", correction_block)
        )


def _render_question_plan(plan: QuestionPlan | None) -> str:
    if plan is None:
        return "(no validated question plan — use registry and schema context directly)"
    return plan.model_dump_json(indent=2)


def _render_correction_block(correction: CorrectionContext | None) -> str:
    """Render the optional ## Correction block for a retry attempt.

    Empty string when no correction context — the prompt template still
    works on the first attempt with `{correction_block}` substituting to ''.
    """
    if correction is None:
        return ""
    lines = [
        "",
        "## Correction",
        "Your previous attempt failed. Do NOT repeat the previous SQL — address the specific failure below.",
        "",
        f"Failure kind: {correction.failure_kind}",
        f"Failure detail: {correction.failure_detail}",
        "",
        "Previous SQL:",
        "```sql",
        correction.prev_sql or "(empty)",
        "```",
    ]
    if correction.schema_hint:
        lines.extend([
            "",
            "Live column info for the tables your previous SQL referenced "
            "(authoritative — use these names exactly):",
            "```",
            correction.schema_hint,
            "```",
        ])
    if correction.reviewer_note:
        lines.extend([
            "",
            f"Reviewer note: {correction.reviewer_note}",
        ])
    return "\n".join(lines)


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
    for interpretation_choices, models will return dicts or plain strings.
    Adapt here rather than failing so the retry loop is only needed for
    structural problems (and not field-shape micro-violations).
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


def _escalate_hard(question: BusinessQuestion, exc: LLMError) -> SQLAgentAnswer:
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


def _make_soft_failure_answer(
    question: BusinessQuestion, exc: Exception
) -> SQLAgentAnswer:
    """Soft failure (parse / schema validation / SQL safety).

    Unlike hard errors, soft failures are retry-eligible at the state-machine
    level. The agent surfaces the failure on the answer (class='output') so
    the workflow can decide whether to retry with a correction context or
    give up. No internal repair — that lives in the state machine now.
    """
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
        logic="No answer produced — LLM output failed schema/safety validation.",
        result_rows=[],
        interpretation_choices=[],
        dq_notes=[],
        warnings=[f"{type(exc).__name__}: {str(exc)[:300]}"],
        system_error=SystemError(
            error_class="output",
            message=str(exc)[:500],
            suggested_action=(
                "LLM output did not validate. The state machine will retry "
                "with a correction context if the unified retry budget allows; "
                "exhaustion routes to AUDIT_REQUIRED."
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
