"""FastAPI app for the Trust Analytics Portal."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from trust_analytics.portal import (
    HERO_ID,
    cached_analysis,
    demo_questions,
    list_cached_analyses,
    make_csv,
    make_pptx,
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


@app.post("/api/analysis/run")
def run_analysis(request: RunRequest) -> dict[str, Any]:
    return run_or_cache(
        question_id=request.question_id or HERO_ID,
        question_text=request.question_text,
    )


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


WEB_DIR = Path(__file__).parents[2] / "web"
if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


@app.get("/{path:path}")
def frontend(path: str) -> FileResponse:
    index = WEB_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not built.")
    return FileResponse(index)
