"""FastAPI app for the Trust Analytics Portal."""

from __future__ import annotations

import io
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from trust_analytics.config import load_settings
from trust_analytics.llm import make_client
from trust_analytics.portal import (
    HERO_ID,
    cached_analysis,
    demo_questions,
    list_cached_analyses,
    list_library_items,
    make_csv,
    make_pptx,
    run_and_persist_analysis,
    run_or_cache,
    shape_question,
)
from trust_analytics.telemetry import (
    record_event,
    summarize_costs,
    summarize_run_detail,
    telemetry_context,
)


class RunRequest(BaseModel):
    question_id: str | None = None
    question_text: str | None = None


class ShapeQuestionRequest(BaseModel):
    question_text: str
    business_objective: str | None = None
    period: str | None = None
    segment: str | None = None
    dimension: str | None = None
    audience: str | None = None
    desired_output: str | None = None


class RunSessionRequest(BaseModel):
    question_id: str | None = None
    question_text: str
    fields: dict[str, str] = Field(default_factory=dict)


RUN_STAGES = [
    ("shape", "Question shaping"),
    ("plan", "Planner/source derivation"),
    ("sql", "SQL execution"),
    ("preflight", "Pre-flight checks"),
    ("qa", "QA reconciliation"),
    ("project", "Pack projection"),
]

RUN_SESSIONS: dict[str, dict[str, Any]] = {}
RUN_LOCK = threading.Lock()


app = FastAPI(title="Trust Analytics Portal", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/runtime/llm")
def llm_status() -> dict[str, Any]:
    settings = load_settings()
    client = make_client(settings)
    available = bool(getattr(client, "available", False))
    message = "Live LLM is configured."
    if not available and settings.legacy_openrouter_configured:
        message = (
            "Legacy OpenRouter config was detected but ignored. Set OPENAI_API_KEY "
            "to use native OpenAI validation."
        )
    elif not available:
        message = "OPENAI_API_KEY is not set, so live validation cannot call the LLM."
    elif settings.ignored_openai_base_url:
        message = "Live LLM is configured. Non-native OPENAI_BASE_URL was ignored."
    return {
        "configured": bool(settings.openai_api_key) or type(client).__name__ == "FixtureLLMClient",
        "available": available,
        "client": type(client).__name__,
        "model": settings.openai_model,
        "mock": type(client).__name__ == "FixtureLLMClient",
        "provider": "openai-native",
        "legacyOpenRouterDetected": settings.legacy_openrouter_configured,
        "ignoredCustomBaseUrl": bool(settings.ignored_openai_base_url),
        "message": message,
    }


@app.get("/api/demo-questions")
def questions() -> list[dict[str, str]]:
    return demo_questions()


@app.get("/api/analyses")
def analyses() -> list[dict[str, Any]]:
    return list_cached_analyses()


@app.get("/api/library")
def library() -> list[dict[str, Any]]:
    return list_library_items()


@app.post("/api/analysis/run")
def run_analysis(request: RunRequest) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    start = time.perf_counter()
    with telemetry_context(
        run_id=run_id,
        action="build_pack",
        analysis_id=request.question_id or HERO_ID,
        question_text=request.question_text,
    ):
        try:
            analysis = run_or_cache(
                question_id=request.question_id or HERO_ID,
                question_text=request.question_text,
            )
            record_event(
                event_type="app_call",
                status=_status_for_analysis(analysis),
                action="build_pack",
                run_id=run_id,
                analysis_id=analysis["id"],
                question_text=request.question_text or analysis.get("question"),
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            return analysis
        except Exception as exc:
            record_event(
                event_type="app_call",
                status="failed",
                action="build_pack",
                run_id=run_id,
                analysis_id=request.question_id or HERO_ID,
                question_text=request.question_text,
                duration_ms=int((time.perf_counter() - start) * 1000),
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            raise


@app.post("/api/runs")
def create_run(request: RunSessionRequest) -> dict[str, Any]:
    start = time.perf_counter()
    shape = shape_question(question_text=request.question_text, **_shape_kwargs(request.fields))
    run_id = uuid.uuid4().hex
    effective_fields = {
        key: value
        for key, value in (shape.get("fields") or {}).items()
        if value is not None
    }
    session = {
        "id": run_id,
        "status": "needs_clarification" if shape["clarificationNeeds"] else "running",
        "questionText": request.question_text,
        "fields": effective_fields,
        "shape": shape,
        "clarificationNeeds": shape["clarificationNeeds"],
        "nextActions": _next_actions_for_shape(shape),
        "stages": _initial_stages(),
        "resultId": None,
        "result": None,
        "error": None,
    }
    if shape["clarificationNeeds"]:
        session["stages"][0]["state"] = "blocked"
        with RUN_LOCK:
            RUN_SESSIONS[run_id] = session
        record_event(
            event_type="app_call",
            status="needs_clarification",
            action="validate_run",
            run_id=run_id,
            analysis_id=shape.get("recommendedAnalysisId"),
            question_text=request.question_text,
            duration_ms=int((time.perf_counter() - start) * 1000),
            extra={"clarification_count": len(shape["clarificationNeeds"])},
        )
        return session

    session["stages"][0]["state"] = "done"
    session["stages"][1]["state"] = "current"
    with RUN_LOCK:
        RUN_SESSIONS[run_id] = session
    thread = threading.Thread(
        target=_run_session,
        args=(run_id, request),
        daemon=True,
    )
    thread.start()
    return session


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    with RUN_LOCK:
        session = RUN_SESSIONS.get(run_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return session


@app.get("/api/runs/{run_id}/result")
def get_run_result(run_id: str) -> dict[str, Any]:
    with RUN_LOCK:
        session = RUN_SESSIONS.get(run_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        if session["status"] not in {"completed", "audit_required"} or session["result"] is None:
            raise HTTPException(status_code=409, detail="Run result is not ready.")
        return session["result"]


@app.post("/api/question/shape")
def shape_business_question(request: ShapeQuestionRequest) -> dict[str, Any]:
    start = time.perf_counter()
    shape = shape_question(
            question_text=request.question_text,
            business_objective=request.business_objective,
            period=request.period,
            segment=request.segment,
            dimension=request.dimension,
            audience=request.audience,
            desired_output=request.desired_output,
        )
    record_event(
        event_type="app_call",
        status="success" if shape["quality"]["ready"] else "needs_clarification",
        action="ask_shape",
        analysis_id=shape.get("recommendedAnalysisId"),
        question_text=request.question_text,
        duration_ms=int((time.perf_counter() - start) * 1000),
        extra={"missing_fields": [item["key"] for item in shape.get("missingFields", [])]},
    )
    return shape


@app.get("/api/admin/costs")
def admin_costs() -> dict[str, Any]:
    return summarize_costs()


@app.get("/api/admin/costs/{run_id}")
def admin_cost_detail(run_id: str) -> dict[str, Any]:
    try:
        return summarize_run_detail(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run cost record not found.") from exc


@app.get("/api/analysis/{analysis_id}")
def get_analysis(analysis_id: str) -> dict[str, Any]:
    return cached_analysis(analysis_id)


@app.get("/api/analysis/{analysis_id}/cached")
def get_cached_analysis(analysis_id: str) -> dict[str, Any]:
    return cached_analysis(analysis_id)


@app.get("/api/analysis/{analysis_id}/export.csv")
def export_csv(analysis_id: str) -> Response:
    start = time.perf_counter()
    try:
        analysis = cached_analysis(analysis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_event(
        event_type="app_call",
        status="success",
        action="export_csv",
        analysis_id=analysis_id,
        question_text=analysis.get("question"),
        duration_ms=int((time.perf_counter() - start) * 1000),
    )
    return Response(
        make_csv(analysis),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{analysis_id}.csv"',
        },
    )


@app.get("/api/analysis/{analysis_id}/deck.pptx")
def export_deck(analysis_id: str) -> StreamingResponse:
    start = time.perf_counter()
    try:
        analysis = cached_analysis(analysis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    deck = make_pptx(analysis)
    record_event(
        event_type="app_call",
        status="success",
        action="export_ppt",
        analysis_id=analysis_id,
        question_text=analysis.get("question"),
        duration_ms=int((time.perf_counter() - start) * 1000),
    )
    return StreamingResponse(
        io.BytesIO(deck),
        media_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{analysis_id}-decision-brief.pptx"',
        },
    )


@app.post("/api/analysis/{analysis_id}/email-draft")
def email_draft(analysis_id: str) -> dict[str, str]:
    start = time.perf_counter()
    try:
        analysis = cached_analysis(analysis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_event(
        event_type="app_call",
        status="success",
        action="email_draft",
        analysis_id=analysis_id,
        question_text=analysis.get("question"),
        duration_ms=int((time.perf_counter() - start) * 1000),
    )
    return analysis["emailDraft"]


def _shape_kwargs(fields: dict[str, str]) -> dict[str, str | None]:
    return {
        "business_objective": fields.get("businessObjective") or fields.get("business_objective"),
        "period": fields.get("period"),
        "segment": fields.get("segment"),
        "dimension": fields.get("dimension"),
        "audience": fields.get("audience"),
        "desired_output": fields.get("desiredOutput") or fields.get("desired_output"),
    }


def _initial_stages() -> list[dict[str, Any]]:
    return [
        {"key": key, "label": label, "state": "pending", "durationMs": None}
        for key, label in RUN_STAGES
    ]


STAGE_TICK_SECONDS = 0.25


def _set_stage(run_id: str, index: int, state: str, *, duration_ms: int | None = None) -> None:
    with RUN_LOCK:
        session = RUN_SESSIONS[run_id]
        session["stages"][index]["state"] = state
        if duration_ms is not None:
            session["stages"][index]["durationMs"] = duration_ms


def _advance_stage(run_id: str, index: int, started_at: float) -> float:
    """Mark stage at index done with its measured duration, return new started_at."""
    now = time.perf_counter()
    _set_stage(run_id, index, "done", duration_ms=int((now - started_at) * 1000))
    return now


def _begin_stage(run_id: str, index: int) -> float:
    _set_stage(run_id, index, "current")
    return time.perf_counter()


def _run_session(run_id: str, request: RunSessionRequest) -> None:
    start = time.perf_counter()
    stage_start = start
    try:
        with RUN_LOCK:
            session_fields = dict(RUN_SESSIONS[run_id].get("fields") or {})
        with telemetry_context(
            run_id=run_id,
            action="validate_run",
            analysis_id=request.question_id,
            question_text=request.question_text,
        ):
            # Stage 0 (shape) is already marked done by create_run. Walk the
            # intermediate stages discretely so the React poller (~650ms)
            # catches each in the 'current' state, then drive the heaviest
            # work inside the SQL stage.
            for index in (1, 2):
                stage_start = _begin_stage(run_id, index)
                time.sleep(STAGE_TICK_SECONDS)
                stage_start = _advance_stage(run_id, index, stage_start)

            stage_start = _begin_stage(run_id, 3)  # sql execution
            analysis = run_and_persist_analysis(
                question_id=request.question_id,
                question_text=request.question_text,
                fields=session_fields,
            )
            stage_start = _advance_stage(run_id, 3, stage_start)

            for index in (4, 5):
                stage_start = _begin_stage(run_id, index)
                time.sleep(STAGE_TICK_SECONDS)
                stage_start = _advance_stage(run_id, index, stage_start)

            record_event(
                event_type="app_call",
                status=_status_for_analysis(analysis),
                action="validate_run",
                run_id=run_id,
                analysis_id=analysis["id"],
                question_text=request.question_text,
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        with RUN_LOCK:
            session = RUN_SESSIONS[run_id]
            session["status"] = _run_status_for_analysis(analysis)
            session["resultId"] = analysis["id"]
            session["result"] = analysis
            session["nextActions"] = _next_actions_for_analysis(analysis)
    except Exception as exc:  # noqa: BLE001 - run sessions surface structured failures.
        record_event(
            event_type="app_call",
            status="failed",
            action="validate_run",
            run_id=run_id,
            analysis_id=request.question_id,
            question_text=request.question_text,
            duration_ms=int((time.perf_counter() - start) * 1000),
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        with RUN_LOCK:
            session = RUN_SESSIONS[run_id]
            session["status"] = "failed"
            session["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "suggestedAction": (
                    "Check the live agent configuration or revise the question, then validate again."
                ),
            }
            session["nextActions"] = [
                {"key": "revise", "label": "Revise question", "route": "/"},
                {
                    "key": "evidence",
                    "label": "Open nearest evidence room",
                    "route": f"/review/{HERO_ID}",
                },
            ]
            for stage in session["stages"]:
                if stage["state"] == "current":
                    stage["state"] = "failed"


def _status_for_analysis(analysis: dict[str, Any]) -> str:
    label = (analysis.get("status") or {}).get("label")
    if label == "Audit required":
        return "audit_required"
    return "success"


def _run_status_for_analysis(analysis: dict[str, Any]) -> str:
    label = (analysis.get("status") or {}).get("label")
    if label == "Audit required":
        return "audit_required"
    return "completed"


def _next_actions_for_shape(shape: dict[str, Any]) -> list[dict[str, str]]:
    if shape.get("clarificationNeeds"):
        return [
            {
                "key": "clarify",
                "label": "Add missing critical context",
                "route": "/",
            }
        ]
    return [
        {"key": "validate", "label": "Validate analysis", "route": "/"},
        {
            "key": "evidence",
            "label": "Open evidence room",
            "route": f"/review/{shape.get('recommendedAnalysisId') or HERO_ID}",
        },
    ]


def _next_actions_for_analysis(analysis: dict[str, Any]) -> list[dict[str, str]]:
    if (analysis.get("status") or {}).get("label") == "Audit required":
        return [
            {
                "key": "audit",
                "label": "Open audit handoff",
                "route": f"/handoff/{analysis['id']}",
            },
            {
                "key": "evidence",
                "label": "Open evidence room",
                "route": f"/review/{analysis['id']}",
            },
        ]
    return [
        {
            "key": "pack",
            "label": "Open decision pack",
            "route": f"/analysis/{analysis['id']}",
        },
        {
            "key": "evidence",
            "label": "Open evidence room",
            "route": f"/review/{analysis['id']}",
        },
    ]


WEB_DIR = Path(__file__).parents[2] / "web"
if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.get("/{path:path}")
def frontend(path: str) -> FileResponse:
    index = WEB_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not built.")
    return FileResponse(index)
