"""CLI `pluang-agent ask` tests (R7).

Uses typer's CliRunner with a stub LLM client injected via monkey-patch on
`pluang_agent.llm.make_client`. We don't rely on the fixture-based mock
client because ad-hoc questions get synthesised ids that won't match any
fixture filename — so we provide a programmatic stub instead.

Each test exercises one CLI surface: happy path, --no-review, --metric/--period
overrides, planner-failure non-zero exit.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pluang_agent import cli as cli_module
from pluang_agent import llm as llm_module
from pluang_agent.llm import LLMResponse
from pluang_agent.models import UsageRecord

runner = CliRunner()


# ---------------------------------------------------------------------------
# Test fixtures: real SQLite DB + stub LLM client
# ---------------------------------------------------------------------------


@pytest.fixture()
def real_db(tmp_path: Path) -> Path:
    """A real SQLite DB the ask command can actually execute SQL against."""
    db_path = tmp_path / "ask.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE fct_trading_daily (transaction_date TEXT, asset_class TEXT, gtv_idr REAL, gtv_usd REAL, transaction_count INTEGER)"
    )
    conn.execute(
        "INSERT INTO fct_trading_daily VALUES "
        "('2025-10-15', 'crypto', 100.0, 0.01, 5), "
        "('2025-10-20', 'crypto', 200.0, 0.02, 10)"
    )
    conn.commit()
    conn.close()
    return db_path


def _trace_response(table: str = "fct_trading_daily") -> str:
    """Canned DerivationTrace JSON that passes the validator."""
    return json.dumps(
        {
            "required_grain": {"dimensions": ["transaction_date"]},
            "scope_predicates": ["test_period"],
            "candidate_sources": [
                {
                    "table": table,
                    "grain": {"dimensions": ["transaction_date"]},
                    "grain_match": "exact",
                    "scope_feasibility": {
                        "test_period": "feasible_via=transaction_date filter"
                    },
                    "selected": True,
                    "rejection_reason": None,
                },
            ],
            "chosen_source": table,
            "chosen_filters": ["transaction_date >= '2025-10-01'"],
            "chosen_aggregator": "SUM",
            "aggregator_rationale": "SUM because gtv_idr is per-day grain.",
            "rendered_why_chosen": f"Picked {table} for the test.",
        }
    )


def _sql_response() -> str:
    return json.dumps(
        {
            "question_id": "adhoc_x",
            "question": "?",
            "metric_name": "gtv_idr",
            "metric_value": None,
            "period": "October 2025",
            "source": {
                "primary_table": "fct_trading_daily",
                "why_chosen": "planner-derived",
                "alternatives_available": [],
            },
            "sql": "SELECT SUM(gtv_idr) AS gtv_idr FROM fct_trading_daily WHERE transaction_date >= '2025-10-01'",
            "filters": ["transaction_date in October 2025"],
            "assumptions": [],
            "logic": "Sum gtv_idr",
            "result_rows": [],
            "interpretation_choices": [],
            "dq_notes": [],
            "warnings": [],
        }
    )


def _layer_b_response() -> str:
    return json.dumps(
        {
            "proposal": "n/a",
            "evidence": ["only one source"],
            "confidence": "LOW",
            "what_this_does_not_explain": "n/a",
        }
    )


def _layer_c_response() -> str:
    return json.dumps(
        {
            "dimensions": {
                "correctness": "GREEN",
                "source_reliability": "GREEN",
                "ambiguity": "GREEN",
            },
            "overall": "GREEN",
            "reviewer_summary": "Clean.",
            "unresolved_questions": [],
        }
    )


class _ScriptedStub:
    """LLM client that returns responses by stage_tag prefix routing.

    Avoids brittleness: we don't have to script every call in order; instead
    each stage gets a fixed canned response. The trace call has to validate,
    so it gets the canned trace; sql_agent gets a canned SQL answer; etc.
    """

    def __init__(self, table: str = "fct_trading_daily"):
        self.table = table
        self.available = True
        self.calls: list[str] = []

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        self.calls.append(stage_tag)
        if stage_tag.startswith("planner_trace"):
            return LLMResponse(content=_trace_response(self.table), usage=UsageRecord())
        if stage_tag.startswith("sql_agent"):
            return LLMResponse(content=_sql_response(), usage=UsageRecord())
        if stage_tag.startswith("qa_layer_b"):
            return LLMResponse(content=_layer_b_response(), usage=UsageRecord())
        if stage_tag.startswith("qa_layer_c"):
            return LLMResponse(content=_layer_c_response(), usage=UsageRecord())
        # Default for any other stage: return a generic OK
        return LLMResponse(content="{}", usage=UsageRecord())


class _PlannerTraceFailureStub(_ScriptedStub):
    """Stub that always returns a malformed trace so the validator rejects."""

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        self.calls.append(stage_tag)
        if stage_tag.startswith("planner_trace"):
            # Trace with NO candidates → validator catches "no candidate selected"
            return LLMResponse(
                content=json.dumps(
                    {
                        "required_grain": {"dimensions": ["x"]},
                        "scope_predicates": [],
                        "candidate_sources": [],
                        "chosen_source": "ghost_table",
                        "chosen_filters": [],
                        "chosen_aggregator": "SUM",
                        "aggregator_rationale": "no grain mentioned",
                        "rendered_why_chosen": "x",
                    }
                ),
                usage=UsageRecord(),
            )
        return super().chat_json(system, user, stage_tag=stage_tag)


@pytest.fixture()
def patch_llm_and_metadata(monkeypatch: pytest.MonkeyPatch, real_db: Path):
    """Swap make_client + dbt metadata + data_dir resolution so the CLI runs
    against the synthetic SQLite DB without needing the real CSVs.

    Yields the stub LLM client so tests can inspect call history.
    """
    from pluang_agent.metadata import DbtMetadata

    stub = _ScriptedStub()

    def _fake_make_client(settings):
        return stub

    def _fake_load_metadata(_root):
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
                            {"name": "gtv_usd"},
                            {"name": "transaction_count"},
                        ],
                    },
                ]
            },
        )

    monkeypatch.setattr(llm_module, "make_client", _fake_make_client)
    monkeypatch.setattr(
        "pluang_agent.cli.load_settings",
        lambda: _fake_settings(real_db),
    )
    monkeypatch.setattr(
        "pluang_agent.metadata.load_dbt_metadata",
        _fake_load_metadata,
    )
    monkeypatch.setattr(
        "pluang_agent.metadata.case_root_from_data_dir",
        lambda _d: Path("/tmp/fake_case_root"),
    )

    return stub


def _fake_settings(db_path: Path):
    """Settings shim that points at the synthetic DB."""
    class _S:
        pass

    s = _S()
    s.db_path = db_path
    s.data_dir = db_path.parent
    s.openrouter_api_key = "fake-key"
    s.openrouter_base_url = "https://api.openai.com/v1"
    s.openrouter_model = "gpt-4o-mini"
    return s


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_ask_happy_path_writes_four_files(
    patch_llm_and_metadata, tmp_path: Path
) -> None:
    """`pluang-agent ask "..." --no-review` writes the expected files and
    exits 0 on success."""
    out = tmp_path / "ask_out"
    result = runner.invoke(
        cli_module.app,
        [
            "ask",
            "What was GTV in October 2025?",
            "--no-review",
            "--output-dir",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.stdout
    # Four expected output files
    assert (out / "sql_agent_answers.json").exists()
    assert (out / "quality_report.json").exists()
    assert (out / "question_plans.json").exists()
    assert any(p.name.startswith("review_") and p.suffix == ".log" for p in out.iterdir())


# ---------------------------------------------------------------------------
# Overrides honoured
# ---------------------------------------------------------------------------


def test_ask_metric_and_period_overrides(
    patch_llm_and_metadata, tmp_path: Path
) -> None:
    out = tmp_path / "ask_out"
    result = runner.invoke(
        cli_module.app,
        [
            "ask",
            "Anything",
            "--metric",
            "custom_metric",
            "--period",
            "Custom period string",
            "--no-review",
            "--output-dir",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.stdout
    # The override reaches BusinessQuestion → QuestionPlan. The SQL Agent's
    # answer.metric_name is the LLM-authored string (canned in our stub),
    # so we assert against the question plan, which is the planner-side
    # surface of the override.
    plans = json.loads((out / "question_plans.json").read_text())
    assert plans[0]["metric_intent"] == "custom_metric"


# ---------------------------------------------------------------------------
# Planner-validation-failure non-zero exit
# ---------------------------------------------------------------------------


def test_ask_planner_failure_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, real_db: Path, tmp_path: Path
) -> None:
    """When the trace validator rejects the LLM-proposed trace, the
    question routes to AUDIT_REQUIRED and the CLI exits non-zero with a
    clear failure message."""
    from pluang_agent.metadata import DbtMetadata

    stub = _PlannerTraceFailureStub()
    monkeypatch.setattr(llm_module, "make_client", lambda _s: stub)
    monkeypatch.setattr("pluang_agent.cli.load_settings", lambda: _fake_settings(real_db))
    monkeypatch.setattr(
        "pluang_agent.metadata.load_dbt_metadata",
        lambda _r: DbtMetadata(sources={}, models={"models": [{"name": "fct_trading_daily", "columns": []}]}),
    )
    monkeypatch.setattr(
        "pluang_agent.metadata.case_root_from_data_dir",
        lambda _d: Path("/tmp/fake_case_root"),
    )

    out = tmp_path / "ask_out"
    result = runner.invoke(
        cli_module.app,
        [
            "ask",
            "Some question",
            "--no-review",
            "--output-dir",
            str(out),
        ],
    )
    assert result.exit_code != 0
    # Stdout should explain the failure (system_error / audit_required surface).
    assert "audit" in result.stdout.lower() or "could not" in result.stdout.lower()


# ---------------------------------------------------------------------------
# --no-review skips the prompt
# ---------------------------------------------------------------------------


def test_ask_no_review_does_not_prompt(
    patch_llm_and_metadata, tmp_path: Path
) -> None:
    """--no-review must not hang waiting for input; CliRunner without
    input stream proves it (default input is empty)."""
    out = tmp_path / "ask_out"
    result = runner.invoke(
        cli_module.app,
        [
            "ask",
            "GTV in October 2025?",
            "--no-review",
            "--output-dir",
            str(out),
        ],
        input="",  # explicit empty input; --no-review must not consume it
    )
    assert result.exit_code == 0, result.stdout
    # Final summary printed
    assert "Ask result" in result.stdout
