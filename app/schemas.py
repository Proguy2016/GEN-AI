"""Pydantic data models shared across API routes, storage, runner, and Gemini client."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class CandidateLocator(BaseModel):
    """A Playwright locator candidate verified live against the inspected DOM."""

    strategy: str
    expression: str
    match_count: int = Field(ge=0)
    is_unique: bool


class BoundingBox(BaseModel):
    """Document-relative bounding box (in CSS pixels) matching the captured screenshot."""

    x: float
    y: float
    width: float
    height: float


class ObservedElement(BaseModel):
    """A single interactive DOM element extracted deterministically by Playwright."""

    element_id: str
    tag: str
    role: str = ""
    input_type: str = ""
    visible_text: str = ""
    accessible_label: str = ""
    placeholder: str = ""
    element_name: str = ""
    html_id: str = ""
    data_testid: str = ""
    href: str = ""
    required: bool = False
    disabled: bool = False
    visible: bool = True
    bbox: BoundingBox | None = None
    recommended_locator: CandidateLocator | None = None
    candidate_locators: list[CandidateLocator] = Field(default_factory=list)


class InspectionRequest(BaseModel):
    """User request to inspect a webpage inside the isolated browser runner."""

    url: str = Field(..., min_length=1, description="Target http:// or https:// URL")
    scope_selector: str = Field(
        default="",
        description="Optional CSS selector to narrow inspection scope (e.g. 'form', '#login')",
    )
    visible_only: bool = Field(
        default=True,
        description="If true, include only visible interactive elements",
    )


class InspectionSummary(BaseModel):
    """Lightweight metadata for listing saved inspections."""

    inspection_id: str
    source_url: str
    final_url: str
    page_title: str
    scope_selector: str
    visible_only: bool
    element_count: int
    browser_duration_ms: int
    created_at: str


class InspectionRecord(BaseModel):
    """Full structured inspection snapshot linked to its source page."""

    inspection_id: str
    source_url: str
    final_url: str
    page_title: str
    scope_selector: str = ""
    visible_only: bool = True
    created_at: str
    browser_duration_ms: int
    screenshot_width: int = 0
    screenshot_height: int = 0
    screenshot_relpath: str = ""
    total_matched_elements: int = 0
    elements: list[ObservedElement] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    """Human-in-the-loop request to analyze selected observed elements with Gemini."""

    inspection_id: str = Field(..., min_length=1)
    selected_element_ids: list[str] = Field(
        ..., min_length=1, description="Observed element IDs chosen by the tester"
    )
    testing_goal: str = Field(
        ..., min_length=1, description="Tester's stated testing goal or focus"
    )
    notes: str = Field(
        default="",
        description="Optional extra requirements or expected behavior clarified by the tester",
    )
    force_fresh: bool = Field(
        default=False,
        description="If true, bypass cached identical analysis and call Gemini fresh",
    )


class GroundedObservation(BaseModel):
    """One factual observation explicitly linked to an observed element_id."""

    element_id: str
    detail: str
    locator_hint: str = ""


class SuggestedAction(BaseModel):
    """One suggested next inspection or test step grounded in observed elements."""

    title: str
    rationale: str
    element_ids: list[str] = Field(default_factory=list)


class GroundedAnalysis(BaseModel):
    """Structured JSON returned by Gemini separating facts from assumptions."""

    summary: str
    observations: list[GroundedObservation] = Field(default_factory=list)
    assumptions_and_unknowns: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    suggested_next_actions: list[SuggestedAction] = Field(default_factory=list)


class LLMCallTelemetry(BaseModel):
    """Feasibility & visibility telemetry recorded per Gemini call."""

    call_id: str
    inspection_id: str
    source_url: str
    created_at: str
    configured_model: str
    model_version: str
    prompt_tokens: int
    output_tokens: int
    total_tokens: int
    token_source: Literal["provider", "estimated"]
    model_response_ms: int
    browser_duration_ms: int
    is_reused: bool
    unverified_element_ids: list[str] = Field(default_factory=list)


class LatencyAverages(BaseModel):
    """Session-wide and per-page average model response times (fresh calls only)."""

    session_fresh_calls: int
    session_total_calls: int
    session_avg_model_ms: float | None = None
    page_fresh_calls: int
    page_total_calls: int
    page_avg_model_ms: float | None = None


class AnalyzeResponse(BaseModel):
    """Combined API response returned to the web UI after Gemini analysis."""

    analysis: GroundedAnalysis
    telemetry: LLMCallTelemetry
    averages: LatencyAverages
