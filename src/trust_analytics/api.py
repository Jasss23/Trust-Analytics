"""FastAPI app for the Trust Analytics Portal."""

from __future__ import annotations

import io
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
    return run_or_cache(
        question_id=request.question_id or HERO_ID,
        question_text=request.question_text,
    )


@app.post("/api/runs")
def create_run(request: RunSessionRequest) -> dict[str, Any]:
    shape = shape_question(question_text=request.question_text, **_shape_kwargs(request.fields))
    run_id = uuid.uuid4().hex
    session = {
        "id": run_id,
        "status": "needs_clarification" if shape["clarificationNeeds"] else "running",
        "questionText": request.question_text,
        "fields": request.fields,
        "shape": shape,
        "clarificationNeeds": shape["clarificationNeeds"],
        "stages": _initial_stages(),
        "resultId": None,
        "result": None,
        "error": None,
    }
    if shape["clarificationNeeds"]:
        session["stages"][0]["state"] = "blocked"
        with RUN_LOCK:
            RUN_SESSIONS[run_id] = session
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
        if session["status"] != "completed" or session["result"] is None:
            raise HTTPException(status_code=409, detail="Run result is not ready.")
        return session["result"]


@app.post("/api/question/shape")
def shape_business_question(request: ShapeQuestionRequest) -> dict[str, Any]:
    return shape_question(
        question_text=request.question_text,
        business_objective=request.business_objective,
        period=request.period,
        segment=request.segment,
        dimension=request.dimension,
        audience=request.audience,
        desired_output=request.desired_output,
    )


@app.get("/api/analysis/{analysis_id}")
def get_analysis(analysis_id: str) -> dict[str, Any]:
    return cached_analysis(analysis_id)


@app.get("/api/analysis/{analysis_id}/cached")
def get_cached_analysis(analysis_id: str) -> dict[str, Any]:
    return cached_analysis(analysis_id)


@app.get("/api/analysis/{analysis_id}/export.csv")
def export_csv(analysis_id: str) -> Response:
    try:
        analysis = cached_analysis(analysis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        make_csv(analysis),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{analysis_id}.csv"',
        },
    )


@app.get("/api/analysis/{analysis_id}/deck.pptx")
def export_deck(analysis_id: str) -> StreamingResponse:
    try:
        analysis = cached_analysis(analysis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StreamingResponse(
        io.BytesIO(make_pptx(analysis)),
        media_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{analysis_id}-decision-brief.pptx"',
        },
    )


@app.post("/api/analysis/{analysis_id}/email-draft")
def email_draft(analysis_id: str) -> dict[str, str]:
    try:
        analysis = cached_analysis(analysis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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


def _initial_stages() -> list[dict[str, str]]:
    return [
        {"key": key, "label": label, "state": "pending"}
        for key, label in RUN_STAGES
    ]


def _set_stage(run_id: str, index: int, state: str) -> None:
    with RUN_LOCK:
        session = RUN_SESSIONS[run_id]
        session["stages"][index]["state"] = state


def _run_session(run_id: str, request: RunSessionRequest) -> None:
    try:
        for index in range(1, len(RUN_STAGES) - 1):
            _set_stage(run_id, index, "current")
            if index > 1:
                _set_stage(run_id, index - 1, "done")
        analysis = run_and_persist_analysis(
            question_id=request.question_id,
            question_text=request.question_text,
            fields=request.fields,
        )
        _set_stage(run_id, len(RUN_STAGES) - 2, "done")
        _set_stage(run_id, len(RUN_STAGES) - 1, "done")
        with RUN_LOCK:
            session = RUN_SESSIONS[run_id]
            session["status"] = "completed"
            session["resultId"] = analysis["id"]
            session["result"] = analysis
    except Exception as exc:  # noqa: BLE001 - run sessions surface structured failures.
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
            for stage in session["stages"]:
                if stage["state"] == "current":
                    stage["state"] = "failed"


WEB_DIR = Path(__file__).parents[2] / "web"
if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.get("/{path:path}")
def frontend(path: str) -> FileResponse:
    index = WEB_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not built.")
    return FileResponse(index)
