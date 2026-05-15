"""Capture one `pluang-agent ask` invocation end-to-end into outputs/sample/.

The `ask` CLI (R7) routes any natural-language question through the same
planner-validated pipeline as the five demo questions. This script drives
that flow under a scripted-LLM stub (mirroring the pattern in
`scripts/capture_reinvestigation.py`) so evaluators can see the artifact
shape without running pytest or an interactive review.

The captured question is intentionally a *re-phrasing* of Q1's intent
("how did GTV stack up across asset classes for October 2025?") so the
registry lookup hits a known metric entry. The point is to demonstrate
that the SQL Agent, Quality Agent, and trust-profile machinery all work
when the input is free-form NL rather than one of the five hand-curated
question objects.

Run:
    .venv/bin/python scripts/capture_ask_example.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from pluang_agent.agents.quality_agent import QualityAgent  # noqa: E402
from pluang_agent.agents.sql_agent import SQLAgent  # noqa: E402
from pluang_agent.llm import LLMResponse  # noqa: E402
from pluang_agent.metadata import DbtMetadata  # noqa: E402
from pluang_agent.metrics import load_metrics_registry  # noqa: E402
from pluang_agent.models import ReviewMode, UsageRecord  # noqa: E402
from pluang_agent.questions import synthesize_business_question  # noqa: E402
from pluang_agent.workflow import run_pipeline, write_pipeline_outputs  # noqa: E402


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


def _build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE fct_trading_daily ("
        "transaction_date TEXT, asset_class TEXT, gtv_idr REAL, transaction_count INTEGER)"
    )
    rows = []
    for day in range(1, 32):
        date = f"2025-10-{day:02d}"
        rows.extend([
            (date, "crypto", 600_000_000.0, 400),
            (date, "gold", 250_000_000.0, 160),
            (date, "gss", 200_000_000.0, 120),
            (date, "fx", 130_000_000.0, 80),
            (date, "options", 60_000_000.0, 40),
        ])
    conn.executemany(
        "INSERT INTO fct_trading_daily VALUES (?, ?, ?, ?)", rows
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
                    "description": "Canonical completed-transaction daily mart.",
                    "columns": [
                        {"name": "transaction_date"},
                        {"name": "asset_class"},
                        {"name": "gtv_idr"},
                        {"name": "transaction_count"},
                    ],
                },
            ]
        },
    )


def _trace_payload() -> str:
    return json.dumps(
        {
            "required_grain": {"dimensions": ["transaction_date", "asset_class"]},
            "scope_predicates": ["October 2025"],
            "candidate_sources": [
                {
                    "table": "fct_trading_daily",
                    "grain": {"dimensions": ["transaction_date", "asset_class"]},
                    "grain_match": "exact",
                    "scope_feasibility": {
                        "October 2025": (
                            "feasible_via=transaction_date >= '2025-10-01' AND "
                            "transaction_date < '2025-11-01'"
                        )
                    },
                    "selected": True,
                    "rejection_reason": None,
                },
            ],
            "chosen_source": "fct_trading_daily",
            "chosen_filters": [
                "transaction_date >= '2025-10-01'",
                "transaction_date < '2025-11-01'",
            ],
            "chosen_aggregator": "SUM",
            "aggregator_rationale": (
                "SUM because gtv_idr is per-(transaction_date, asset_class) row, summable across days."
            ),
            "rendered_why_chosen": (
                "Picked fct_trading_daily for ad-hoc GTV breakdown — single canonical source in the registry."
            ),
        }
    )


def _sql_payload() -> str:
    return json.dumps(
        {
            "question_id": "adhoc_ask_demo",
            "question": "How did GTV stack up across asset classes for October 2025?",
            "metric_name": "gtv_idr_by_asset_class",
            "metric_value": None,
            "period": "October 2025",
            "source": {
                "primary_table": "fct_trading_daily",
                "why_chosen": "planner-derived",
                "alternatives_available": [],
            },
            "sql": (
                "SELECT asset_class, SUM(gtv_idr) AS gtv_idr "
                "FROM fct_trading_daily "
                "WHERE transaction_date >= '2025-10-01' "
                "AND transaction_date < '2025-11-01' "
                "GROUP BY asset_class "
                "ORDER BY gtv_idr DESC"
            ),
            "filters": [
                "transaction_date in October 2025",
                "asset_class breakdown",
            ],
            "assumptions": [
                "fct_trading_daily already filters to completed transactions per the canonical contract.",
            ],
            "logic": "Aggregate gtv_idr by asset_class for the October 2025 window.",
            "result_rows": [],
            "interpretation_choices": [],
            "dq_notes": [],
            "warnings": [],
        }
    )


def _qa_layer_b_payload() -> str:
    return json.dumps(
        {
            "proposal": (
                "No alternative source is registered for the ad-hoc demo metric beyond "
                "fct_trading_daily; cross-source reconciliation is not applicable."
            ),
            "evidence": [
                "Metric registry lists fct_trading_daily as the sole canonical source for this ad-hoc demo run.",
            ],
            "confidence": "LOW",
            "what_this_does_not_explain": (
                "Whether Ops dashboards still show a status-filter delta versus this canonical answer."
            ),
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
            "reviewer_summary": (
                "Ad-hoc breakdown over fct_trading_daily passes Layer A and has no "
                "registered alternative source to reconcile against."
            ),
            "unresolved_questions": [],
        }
    )


def main() -> int:
    out_dir = REPO / "outputs" / "sample" / "ask_example"
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ask.sqlite"
        _build_db(db_path)

        stub = _ScriptedLLM()
        stub.queue("planner_trace:", _trace_payload())
        stub.queue("sql_agent:", _sql_payload())

        question = synthesize_business_question(
            "How did GTV stack up across asset classes for October 2025?",
            metric_override="gtv_idr_by_asset_class",
            period_override="October 2025",
            id_override="adhoc_ask_demo",
        )

        sql_agent = SQLAgent(
            db_path=db_path,
            metadata=_metadata(),
            llm_client=stub,  # type: ignore[arg-type]
            metrics_registry=load_metrics_registry(),
        )
        quality_agent = QualityAgent(
            db_path=db_path,
            metrics_registry=load_metrics_registry(),
            llm_client=stub,  # type: ignore[arg-type]
        )

        result = run_pipeline(
            [question], sql_agent, quality_agent, ReviewMode.DEMO_APPROVE
        )
        write_pipeline_outputs(result, out_dir)

        item = result.items[0]
        decision = item.review_decision
        print(f"Wrote {out_dir}")
        print(f"Terminal: {decision.terminal_state.value if decision else '(none)'}")
        print(f"Trust: {item.quality_report.layer_c.trust_profile.overall}")
        print(f"Rows: {item.answer.result_rows}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
