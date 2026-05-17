"""Run and cost telemetry for the Trust Analytics admin view."""

from __future__ import annotations

import contextlib
import contextvars
import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trust_analytics.models import UsageRecord

RUN_LOG_PATH = Path("logs/runs.jsonl")
PRICING_SOURCE = "OpenAI API pricing, checked 2026-05-17"
PRICING_SOURCE_URL = "https://openai.com/api/pricing/"

# Per 1M text tokens. Dollar values are estimates for admin observability,
# not billing-grade accounting.
MODEL_PRICES_PER_1M = {
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-5.4": {"input": 2.50, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5.5": {"input": 5.00, "output": 30.00},
}


@dataclass(frozen=True)
class TelemetryContext:
    run_id: str | None = None
    action: str | None = None
    analysis_id: str | None = None
    question_text: str | None = None


DEFAULT_CONTEXT = TelemetryContext()
_context: contextvars.ContextVar[TelemetryContext | None] = contextvars.ContextVar(
    "trust_analytics_telemetry",
    default=None,
)


def current_context() -> TelemetryContext:
    return _context.get() or DEFAULT_CONTEXT


@contextlib.contextmanager
def telemetry_context(
    *,
    run_id: str | None = None,
    action: str | None = None,
    analysis_id: str | None = None,
    question_text: str | None = None,
) -> Iterator[None]:
    parent = current_context()
    token = _context.set(
        TelemetryContext(
            run_id=run_id or parent.run_id,
            action=action or parent.action,
            analysis_id=analysis_id or parent.analysis_id,
            question_text=question_text or parent.question_text,
        )
    )
    try:
        yield
    finally:
        _context.reset(token)


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_from_ms(value: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value / 1000))


def estimate_cost_usd(model: str | None, usage: UsageRecord | None) -> float | None:
    if usage is None or not model:
        return None
    prices = MODEL_PRICES_PER_1M.get(model)
    if prices is None:
        return None
    prompt = usage.prompt_tokens or 0
    completion = usage.completion_tokens or 0
    return round(
        (prompt / 1_000_000 * prices["input"])
        + (completion / 1_000_000 * prices["output"]),
        8,
    )


def record_event(
    *,
    event_type: str,
    status: str,
    action: str | None = None,
    run_id: str | None = None,
    analysis_id: str | None = None,
    question_text: str | None = None,
    stage_tag: str | None = None,
    model: str | None = None,
    usage: UsageRecord | None = None,
    duration_ms: int | None = None,
    error: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    log_path: Path = RUN_LOG_PATH,
) -> dict[str, Any]:
    ctx = current_context()
    resolved_model = model or (usage.model if usage else None)
    record: dict[str, Any] = {
        "event_id": uuid.uuid4().hex,
        "ts": iso_from_ms(now_ms()),
        "event_type": event_type,
        "status": status,
        "run_id": run_id or ctx.run_id or new_run_id("event"),
        "action": action or ctx.action,
        "analysis_id": analysis_id or ctx.analysis_id,
        "question_text": question_text or ctx.question_text,
        "stage_tag": stage_tag,
        "model": resolved_model,
        "duration_ms": duration_ms,
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
        "total_tokens": usage.total_tokens if usage else None,
        "estimated_cost_usd": estimate_cost_usd(resolved_model, usage),
        "cost_is_estimated": True,
        "error": error,
    }
    if extra:
        record.update(extra)
    append_event(log_path, record)
    return record


def append_event(log_path: Path, record: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def read_events(log_path: Path | None = None) -> list[dict[str, Any]]:
    log_path = log_path or RUN_LOG_PATH
    if not log_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def summarize_costs(log_path: Path | None = None) -> dict[str, Any]:
    events = read_events(log_path)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(str(event.get("run_id") or event["event_id"]), []).append(event)

    runs = [_summarize_run(run_id, items) for run_id, items in grouped.items()]
    runs.sort(key=lambda item: item["lastSeenAt"], reverse=True)
    totals = {
        "runs": len(runs),
        "events": len(events),
        "llmCalls": sum(run["llmCalls"] for run in runs),
        "totalTokens": sum(run["totalTokens"] for run in runs),
        "estimatedCostUsd": round(sum(run["estimatedCostUsd"] for run in runs), 8),
        "durationMs": sum(run["durationMs"] for run in runs),
    }
    return {
        "totals": totals,
        "runs": runs,
        "pricing": {
            "source": PRICING_SOURCE,
            "sourceUrl": PRICING_SOURCE_URL,
            "estimated": True,
            "models": MODEL_PRICES_PER_1M,
        },
    }


def summarize_run_detail(run_id: str, log_path: Path | None = None) -> dict[str, Any]:
    events = [
        event for event in read_events(log_path)
        if str(event.get("run_id") or event["event_id"]) == run_id
    ]
    if not events:
        raise KeyError(run_id)
    return {"summary": _summarize_run(run_id, events), "events": events}


def _summarize_run(run_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    first = events[0]
    last = events[-1]
    statuses = [event.get("status") for event in events if event.get("status")]
    status = _final_status(statuses)
    tokens = sum(int(event.get("total_tokens") or 0) for event in events)
    cost = sum(float(event.get("estimated_cost_usd") or 0) for event in events)
    duration = sum(int(event.get("duration_ms") or 0) for event in events)
    actions = sorted({event.get("action") for event in events if event.get("action")})
    stage_events = [event for event in events if event.get("event_type") == "llm_stage"]
    error = next((event.get("error") for event in reversed(events) if event.get("error")), None)
    return {
        "runId": run_id,
        "firstSeenAt": first.get("ts"),
        "lastSeenAt": last.get("ts"),
        "status": status,
        "actions": actions,
        "analysisId": _last_value(events, "analysis_id"),
        "questionText": _last_value(events, "question_text"),
        "durationMs": duration,
        "llmCalls": len(stage_events),
        "totalTokens": tokens,
        "estimatedCostUsd": round(cost, 8),
        "costIsEstimated": True,
        "error": error,
    }


def _last_value(events: list[dict[str, Any]], key: str) -> Any:
    for event in reversed(events):
        if event.get(key):
            return event[key]
    return None


def _final_status(statuses: list[str]) -> str:
    for status in reversed(statuses):
        if status in {"failed", "system_error", "audit_required", "needs_clarification"}:
            return status
        if status == "success":
            return "success"
    return statuses[-1] if statuses else "unknown"
