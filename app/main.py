"""FastAPI entry point for the Phase 1 Human-in-the-Loop Web Testing Assistant."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_ROOT, load_settings
from app.llm_client import GeminiRestClient, LLMClientError
from app.runner_bridge import RunnerExecutionError, run_isolated_inspection
from app.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    InspectionRecord,
    InspectionRequest,
    InspectionSummary,
    LLMCallTelemetry,
)
from app.security import (
    SecurityValidationError,
    validate_inspection_id,
    verify_grounded_element_ids,
    wrap_untrusted_page_data,
)
from app.storage import Storage


STATIC_DIR = PROJECT_ROOT / "static"

settings = load_settings()
storage = Storage(settings.db_path)

app = FastAPI(
    title="Human-in-the-Loop Web Testing Assistant (Phase 1)",
    description="Guided webpage inspection, isolated Playwright runner, and grounded Gemini analysis.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def serve_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_public_config() -> dict:
    current = load_settings()
    return {
        "config": current.public_config(),
        "averages": storage.get_latency_averages().model_dump(),
    }


@app.post("/api/inspections", response_model=InspectionRecord)
def create_inspection(req: InspectionRequest) -> InspectionRecord:
    current = load_settings()
    try:
        record = run_isolated_inspection(
            url=req.url,
            scope_selector=req.scope_selector,
            visible_only=req.visible_only,
            settings=current,
        )
    except SecurityValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RunnerExecutionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    storage.save_inspection(record)
    return record


@app.get("/api/inspections", response_model=list[InspectionSummary])
def list_inspections() -> list[InspectionSummary]:
    return storage.list_inspections()


@app.get("/api/inspections/{inspection_id}", response_model=InspectionRecord)
def get_inspection(inspection_id: str) -> InspectionRecord:
    try:
        safe_id = validate_inspection_id(inspection_id)
    except SecurityValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record = storage.get_inspection(safe_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Inspection not found.")
    return record


@app.get("/api/inspections/{inspection_id}/export")
def export_inspection_json(inspection_id: str) -> Response:
    record = get_inspection(inspection_id)
    payload = json.dumps(record.model_dump(), indent=2, ensure_ascii=False)
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{record.inspection_id}_inspection.json"'
        },
    )


@app.get("/api/runs/{inspection_id}/screenshot")
def get_run_screenshot(inspection_id: str) -> FileResponse:
    try:
        safe_id = validate_inspection_id(inspection_id)
    except SecurityValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    screenshot_path: Path = settings.runs_dir / safe_id / "screenshot.png"
    if not screenshot_path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found.")
    return FileResponse(screenshot_path, media_type="image/png")


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze_elements(req: AnalyzeRequest) -> AnalyzeResponse:
    current = load_settings()
    try:
        safe_id = validate_inspection_id(req.inspection_id)
    except SecurityValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    inspection = storage.get_inspection(safe_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found.")

    element_map = {el.element_id: el for el in inspection.elements}
    selected_elements = [
        element_map[eid] for eid in req.selected_element_ids if eid in element_map
    ]
    if not selected_elements:
        raise HTTPException(
            status_code=400,
            detail="None of the selected_element_ids exist in this inspection snapshot.",
        )

    request_hash = storage.compute_request_hash(
        inspection_id=inspection.inspection_id,
        selected_element_ids=[el.element_id for el in selected_elements],
        testing_goal=req.testing_goal,
        notes=req.notes,
        configured_model=current.llm_model,
    )

    # Distinguish reused results from fresh calls (Brief Page 4)
    if not req.force_fresh:
        cached = storage.find_cached_analysis(request_hash)
        if cached is not None:
            cached_analysis, prev_telemetry = cached
            reused_telemetry = LLMCallTelemetry(
                call_id=f"call_{uuid.uuid4().hex[:12]}",
                inspection_id=inspection.inspection_id,
                source_url=inspection.source_url,
                created_at=datetime.now(timezone.utc).isoformat(),
                configured_model=prev_telemetry.configured_model,
                model_version=prev_telemetry.model_version,
                prompt_tokens=prev_telemetry.prompt_tokens,
                output_tokens=prev_telemetry.output_tokens,
                total_tokens=prev_telemetry.total_tokens,
                token_source=prev_telemetry.token_source,
                model_response_ms=0,
                browser_duration_ms=inspection.browser_duration_ms,
                is_reused=True,
                unverified_element_ids=prev_telemetry.unverified_element_ids,
            )
            storage.save_llm_call(
                telemetry=reused_telemetry,
                request_hash=request_hash,
                selected_element_ids=[el.element_id for el in selected_elements],
                testing_goal=req.testing_goal,
                notes=req.notes,
                analysis=cached_analysis,
            )
            averages = storage.get_latency_averages(inspection.source_url)
            return AnalyzeResponse(
                analysis=cached_analysis,
                telemetry=reused_telemetry,
                averages=averages,
            )

    untrusted_payload = wrap_untrusted_page_data(inspection, selected_elements)
    client = GeminiRestClient(api_key=current.gemini_api_key, model=current.llm_model)
    try:
        result = client.analyze_selection(
            untrusted_page_data=untrusted_payload,
            testing_goal=req.testing_goal,
            notes=req.notes,
        )
    except LLMClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    valid_ids = {el.element_id for el in selected_elements}
    unverified_ids = verify_grounded_element_ids(result.analysis, valid_ids)

    telemetry = LLMCallTelemetry(
        call_id=f"call_{uuid.uuid4().hex[:12]}",
        inspection_id=inspection.inspection_id,
        source_url=inspection.source_url,
        created_at=datetime.now(timezone.utc).isoformat(),
        configured_model=result.configured_model,
        model_version=result.model_version,
        prompt_tokens=result.prompt_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
        token_source=result.token_source,
        model_response_ms=result.model_response_ms,
        browser_duration_ms=inspection.browser_duration_ms,
        is_reused=False,
        unverified_element_ids=unverified_ids,
    )
    storage.save_llm_call(
        telemetry=telemetry,
        request_hash=request_hash,
        selected_element_ids=[el.element_id for el in selected_elements],
        testing_goal=req.testing_goal,
        notes=req.notes,
        analysis=result.analysis,
    )
    averages = storage.get_latency_averages(inspection.source_url)
    return AnalyzeResponse(
        analysis=result.analysis, telemetry=telemetry, averages=averages
    )


@app.get("/api/telemetry")
def get_telemetry(source_url: str | None = None) -> dict:
    current = load_settings()
    return {
        "setup": current.public_config(),
        "averages": storage.get_latency_averages(source_url).model_dump(),
        "calls": storage.list_llm_calls(),
    }


@app.get("/api/telemetry/export")
def export_telemetry_json(source_url: str | None = None) -> Response:
    evidence = get_telemetry(source_url=source_url)
    payload = json.dumps(evidence, indent=2, ensure_ascii=False)
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="model_usage_timing_evidence.json"'
        },
    )
