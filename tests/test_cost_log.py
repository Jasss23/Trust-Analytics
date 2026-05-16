"""Cost-log JSONL writer — schema and append behavior."""

from __future__ import annotations

import json
from pathlib import Path

from trust_analytics.llm import append_cost_log
from trust_analytics.models import UsageRecord


def test_append_creates_log_and_appends(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "cost.jsonl"
    append_cost_log(
        log,
        stage_tag="sql_agent:q1",
        model="gpt-4o-mini",
        usage=UsageRecord(prompt_tokens=100, completion_tokens=50, total_tokens=150, cost=0.001),
    )
    append_cost_log(
        log,
        stage_tag="sql_agent:q2",
        model="gpt-4o-mini",
        usage=UsageRecord(prompt_tokens=120, completion_tokens=70, total_tokens=190, cost=0.002),
    )

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["stage_tag"] == "sql_agent:q1"
    assert first["model"] == "gpt-4o-mini"
    assert first["prompt_tokens"] == 100
    assert first["cost_usd"] == 0.001
    assert "ts" in first
    second = json.loads(lines[1])
    assert second["stage_tag"] == "sql_agent:q2"
    assert second["cost_usd"] == 0.002


def test_append_handles_missing_cost(tmp_path: Path) -> None:
    """OpenAI native does not return a cost field; cost_usd is None."""
    log = tmp_path / "cost.jsonl"
    append_cost_log(
        log,
        stage_tag="sql_agent:q1",
        model="gpt-4o-mini",
        usage=UsageRecord(prompt_tokens=10, completion_tokens=5, total_tokens=15, cost=None),
    )
    record = json.loads(log.read_text().strip())
    assert record["cost_usd"] is None
