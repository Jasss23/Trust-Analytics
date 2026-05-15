"""Capture a successful reinvestigation log to outputs/sample/.

Reproduces the R8 happy-path: reviewer rejects with a plan-revising note
("row count, not sum"), the planner LLM-revises the plan, the SQL Agent
retries with the revised plan, pre-flight passes, terminal=reinvestigated.

The test `tests/test_workflow_reinvestigation.py::test_reinvestigation_revises_plan_for_count_not_sum`
covers the same mechanism; this script writes the resulting review log to
disk so evaluators can see the artifact without running pytest.

Run:
    PYTHONPATH=src python scripts/capture_reinvestigation.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from pluang_agent import workflow as wf  # noqa: E402
from pluang_agent.agents.quality_agent import QualityAgent  # noqa: E402
from pluang_agent.agents.sql_agent import SQLAgent  # noqa: E402
from pluang_agent.llm import LLMResponse  # noqa: E402
from pluang_agent.metadata import DbtMetadata  # noqa: E402
from pluang_agent.metrics import MetricsRegistry  # noqa: E402
from pluang_agent.models import (  # noqa: E402
    BusinessQuestion,
    ReviewCategory,
    ReviewDecision,
    ReviewMode,
    TerminalState,
    UsageRecord,
)
from pluang_agent.workflow import _review_log, run_pipeline  # noqa: E402

# ---------------------------------------------------------------------------
# Scripted LLM stub — mirrors tests/test_workflow_reinvestigation.py
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    def __init__(self) -> None:
        self.available = True
        self.calls: list[str] = []
        self.responses: dict[str, list[str]] = {}

    def queue(self, stage_prefix: str, payload: str) -> None:
        self.responses.setdefault(stage_prefix, []).append(payload)

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        self.calls.append(stage_tag)
        for prefix, payloads in self.responses.items():
            if stage_tag.startswith(prefix) and payloads:
                return LLMResponse(content=payloads.pop(0), usage=UsageRecord())
        if stage_tag.startswith("qa_layer_b"):
            return LLMResponse(content=_qa_layer_b_payload(), usage=UsageRecord())
        if stage_tag.startswith("qa_layer_c"):
            return LLMResponse(content=_qa_layer_c_payload(), usage=UsageRecord())
        return LLMResponse(content="{}", usage=UsageRecord())


def _trace_payload(aggregator: str) -> str:
    return json.dumps(
        {
            "required_grain": {"dimensions": ["transaction_date"]},
            "scope_predicates": ["October 2025"],
            "candidate_sources": [
                {
                    "table": "fct_trading_daily",
                    "grain": {"dimensions": ["transaction_date"]},
                    "grain_match": "exact",
                    "scope_feasibility": {"October 2025": "feasible_via transaction_date"},
                    "selected": True,
                    "rejection_reason": None,
                },
            ],
            "chosen_source": "fct_trading_daily",
            "chosen_filters": ["transaction_date >= '2025-10-01'", "transaction_date < '2025-11-01'"],
            "chosen_aggregator": aggregator,
            "aggregator_rationale": f"{aggregator} matches per-day grain rows.",
            "rendered_why_chosen": f"Picked fct_trading_daily with aggregator={aggregator}.",
        }
    )


def _sql_payload(sql: str, alias: str, value: object) -> str:
    return json.dumps(
        {
            "question_id": "adhoc_row_count",
            "question": "how many transactions in October 2025",
            "metric_name": alias,
            "metric_value": [{alias: value}],
            "period": "October 2025",
            "source": {
                "primary_table": "fct_trading_daily",
                "why_chosen": "planner-derived",
                "alternatives_available": [],
            },
            "sql": sql,
            "filters": ["transaction_date >= '2025-10-01'", "transaction_date < '2025-11-01'"],
            "assumptions": [],
            "logic": f"Aggregate via {alias} on the canonical daily table.",
            "result_rows": [{alias: value}],
            "interpretation_choices": [],
            "dq_notes": [],
            "warnings": [],
        }
    )


def _revised_plan_payload(
    *,
    metric_intent: str,
    primary_column: str,
    aggregator: str,
    required_output_columns: list[str],
) -> str:
    return json.dumps(
        {
            "question_id": "adhoc_row_count",
            "metric_intent": metric_intent,
            "period": {"start": "2025-10-01", "end": "2025-11-01"},
            "answer_shape": "scalar",
            "primary_source": {
                "table": "fct_trading_daily",
                "column": primary_column,
                "period_column": "transaction_date",
                "aggregator": aggregator,
                "extra_filters": [],
                "reason": "revised per reviewer note",
            },
            "comparison_sources": [],
            "breakdown": None,
            "required_output_columns": required_output_columns,
            "required_definitions": [],
            "ambiguity_policy": "single_definition",
            "source_policy": "schema_grounded",
            "validation_rules": [],
        }
    )


def _qa_layer_b_payload() -> str:
    return json.dumps(
        {
            "proposal": "Single canonical source; no cross-source comparison applicable.",
            "evidence": ["only fct_trading_daily registered for this metric"],
            "confidence": "LOW",
            "what_this_does_not_explain": "n/a — single source.",
        }
    )


def _qa_layer_c_payload() -> str:
    return json.dumps(
        {
            "dimensions": {
                "correctness": "GREEN",
                "source_reliability": "GREEN",
                "ambiguity": "GREEN",
            },
            "overall": "GREEN",
            "reviewer_summary": "Clean — revised plan reaches a defensible answer.",
            "unresolved_questions": [],
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE fct_trading_daily (transaction_date TEXT, asset_class TEXT, gtv_idr REAL)"
    )
    conn.executemany(
        "INSERT INTO fct_trading_daily VALUES (?, ?, ?)",
        [
            ("2025-10-15", "crypto", 1000.0),
            ("2025-10-20", "crypto", 2000.0),
            ("2025-10-25", "gold", 500.0),
        ],
    )
    conn.commit()
    conn.close()


def _metadata() -> DbtMetadata:
    return DbtMetadata(
        sources={},
        models={
            "models": [
                {
                    "name": "fct_trading_daily",
                    "columns": [
                        {"name": "transaction_date"},
                        {"name": "asset_class"},
                        {"name": "gtv_idr"},
                    ],
                },
            ]
        },
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    out = REPO / "outputs" / "sample" / "review_reinvestigation_success.log"
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "wf.sqlite"
        _build_db(db_path)

        stub = _ScriptedLLM()
        stub.queue("planner_trace:", _trace_payload(aggregator="SUM"))
        stub.queue(
            "sql_agent:",
            _sql_payload(
                "SELECT SUM(gtv_idr) AS gtv_idr FROM fct_trading_daily "
                "WHERE transaction_date >= '2025-10-01' AND transaction_date < '2025-11-01'",
                "gtv_idr",
                3500.0,
            ),
        )
        stub.queue(
            "planner_revise:",
            _revised_plan_payload(
                metric_intent="row_count",
                primary_column="transaction_date",
                aggregator="COUNT_DISTINCT",
                required_output_columns=["row_count"],
            ),
        )
        stub.queue("planner_trace:", _trace_payload(aggregator="COUNT_DISTINCT"))
        stub.queue(
            "sql_agent_retry:",
            _sql_payload(
                "SELECT COUNT(*) AS row_count FROM fct_trading_daily "
                "WHERE transaction_date >= '2025-10-01' AND transaction_date < '2025-11-01'",
                "row_count",
                3,
            ),
        )

        def _scripted_review(items, review_mode):  # noqa: ARG001
            decisions = []
            for item in items:
                decisions.append(
                    ReviewDecision(
                        question_id=item.question.id,
                        decision="reject",
                        category=ReviewCategory.ANSWER_WRONG,
                        note=(
                            "I want the row count of transactions, not the sum of gtv_idr. "
                            "Please reinvestigate with COUNT_DISTINCT on the transaction grain."
                        ),
                    )
                )
            return decisions

        original = wf.collect_review_decisions
        wf.collect_review_decisions = _scripted_review  # type: ignore[assignment]
        try:
            sql_agent = SQLAgent(
                db_path=db_path,
                metadata=_metadata(),
                llm_client=stub,  # type: ignore[arg-type]
                metrics_registry=MetricsRegistry(entries={}),
            )
            quality_agent = QualityAgent(
                db_path=db_path,
                metrics_registry=MetricsRegistry(entries={}),
                llm_client=stub,  # type: ignore[arg-type]
            )
            question = BusinessQuestion(
                id="adhoc_row_count",
                text="How many transactions were there in October 2025?",
                metric="gtv_idr",
                period="October 2025",
            )
            result = run_pipeline(
                [question], sql_agent, quality_agent, ReviewMode.INTERACTIVE
            )
        finally:
            wf.collect_review_decisions = original  # type: ignore[assignment]

        item = result.items[0]
        assert item.review_decision is not None, "expected a review decision"
        assert item.review_decision.terminal_state == TerminalState.REINVESTIGATED, (
            f"expected REINVESTIGATED, got {item.review_decision.terminal_state}"
        )

        log = _review_log(result)
        header = (
            "# R8 reinvestigation happy-path — captured by scripts/capture_reinvestigation.py\n"
            "# Scenario: reviewer rejects an answer with note 'row count, not sum'.\n"
            "# The planner LLM-revises the plan (SUM(gtv_idr) → COUNT_DISTINCT(transaction_date)),\n"
            "# the SQL Agent retries with the revised plan, pre-flight passes,\n"
            "# terminal state = REINVESTIGATED (NOT audit_required).\n"
            "# This is the artifact form of tests/test_workflow_reinvestigation.py::\n"
            "#   test_reinvestigation_revises_plan_for_count_not_sum.\n"
            "\n"
        )
        out.write_text(header + log, encoding="utf-8")
        print(f"Wrote {out}")
        print(f"Terminal state: {item.review_decision.terminal_state.value}")
        print(
            f"Reinvestigated metric_value: {item.reinvestigated_answer.metric_value}"
            if item.reinvestigated_answer
            else "no reinvestigated answer"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
