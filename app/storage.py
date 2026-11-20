"""SQLite persistence for inspection snapshots, selected elements, and LLM telemetry."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from app.schemas import (
    GroundedAnalysis,
    InspectionRecord,
    InspectionSummary,
    LatencyAverages,
    LLMCallTelemetry,
    ObservedElement,
)


class Storage:
    """Thin, transparent sqlite3 store (no ORM magic) designed for Phase 1 & Phase 2."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inspections (
                    inspection_id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    final_url TEXT NOT NULL,
                    page_title TEXT NOT NULL,
                    scope_selector TEXT NOT NULL,
                    visible_only INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    browser_duration_ms INTEGER NOT NULL,
                    screenshot_width INTEGER NOT NULL,
                    screenshot_height INTEGER NOT NULL,
                    screenshot_relpath TEXT NOT NULL,
                    total_matched_elements INTEGER NOT NULL,
                    elements_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS llm_calls (
                    call_id TEXT PRIMARY KEY,
                    inspection_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    selected_ids_json TEXT NOT NULL,
                    testing_goal TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    configured_model TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    token_source TEXT NOT NULL,
                    model_response_ms INTEGER NOT NULL,
                    browser_duration_ms INTEGER NOT NULL,
                    is_reused INTEGER NOT NULL,
                    analysis_json TEXT NOT NULL,
                    unverified_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (inspection_id) REFERENCES inspections (inspection_id)
                );

                CREATE INDEX IF NOT EXISTS idx_llm_calls_hash
                    ON llm_calls (request_hash, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_llm_calls_url
                    ON llm_calls (source_url, created_at DESC);
                """
            )

    @staticmethod
    def compute_request_hash(
        inspection_id: str,
        selected_element_ids: list[str],
        testing_goal: str,
        notes: str,
        configured_model: str,
    ) -> str:
        """Deterministic key used to detect identical analysis requests for reuse."""
        canonical = json.dumps(
            {
                "inspection_id": inspection_id,
                "selected_element_ids": sorted(set(selected_element_ids)),
                "testing_goal": testing_goal.strip(),
                "notes": notes.strip(),
                "configured_model": configured_model,
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def save_inspection(self, record: InspectionRecord) -> None:
        elements_json = json.dumps(
            [el.model_dump() for el in record.elements], ensure_ascii=False
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO inspections (
                    inspection_id, source_url, final_url, page_title,
                    scope_selector, visible_only, created_at, browser_duration_ms,
                    screenshot_width, screenshot_height, screenshot_relpath,
                    total_matched_elements, elements_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.inspection_id,
                    record.source_url,
                    record.final_url,
                    record.page_title,
                    record.scope_selector,
                    1 if record.visible_only else 0,
                    record.created_at,
                    record.browser_duration_ms,
                    record.screenshot_width,
                    record.screenshot_height,
                    record.screenshot_relpath,
                    record.total_matched_elements,
                    elements_json,
                ),
            )

    def get_inspection(self, inspection_id: str) -> InspectionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inspections WHERE inspection_id = ?", (inspection_id,)
            ).fetchone()
        if row is None:
            return None

        raw_elements = json.loads(row["elements_json"])
        elements = [ObservedElement.model_validate(item) for item in raw_elements]
        return InspectionRecord(
            inspection_id=row["inspection_id"],
            source_url=row["source_url"],
            final_url=row["final_url"],
            page_title=row["page_title"],
            scope_selector=row["scope_selector"],
            visible_only=bool(row["visible_only"]),
            created_at=row["created_at"],
            browser_duration_ms=row["browser_duration_ms"],
            screenshot_width=row["screenshot_width"],
            screenshot_height=row["screenshot_height"],
            screenshot_relpath=row["screenshot_relpath"],
            total_matched_elements=row["total_matched_elements"],
            elements=elements,
        )

    def list_inspections(self, limit: int = 30) -> list[InspectionSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT inspection_id, source_url, final_url, page_title,
                       scope_selector, visible_only, browser_duration_ms,
                       created_at, elements_json
                FROM inspections
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        summaries: list[InspectionSummary] = []
        for row in rows:
            el_count = len(json.loads(row["elements_json"]))
            summaries.append(
                InspectionSummary(
                    inspection_id=row["inspection_id"],
                    source_url=row["source_url"],
                    final_url=row["final_url"],
                    page_title=row["page_title"],
                    scope_selector=row["scope_selector"],
                    visible_only=bool(row["visible_only"]),
                    element_count=el_count,
                    browser_duration_ms=row["browser_duration_ms"],
                    created_at=row["created_at"],
                )
            )
        return summaries

    def find_cached_analysis(
        self, request_hash: str
    ) -> tuple[GroundedAnalysis, LLMCallTelemetry] | None:
        """Find the latest fresh LLM call matching this exact inspection + selection + goal."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM llm_calls
                WHERE request_hash = ? AND is_reused = 0
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (request_hash,),
            ).fetchone()
        if row is None:
            return None

        analysis = GroundedAnalysis.model_validate(json.loads(row["analysis_json"]))
        telemetry = self._row_to_telemetry(row)
        return analysis, telemetry

    def save_llm_call(
        self,
        telemetry: LLMCallTelemetry,
        request_hash: str,
        selected_element_ids: list[str],
        testing_goal: str,
        notes: str,
        analysis: GroundedAnalysis,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_calls (
                    call_id, inspection_id, source_url, request_hash,
                    selected_ids_json, testing_goal, notes, configured_model,
                    model_version, prompt_tokens, output_tokens, total_tokens,
                    token_source, model_response_ms, browser_duration_ms,
                    is_reused, analysis_json, unverified_ids_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telemetry.call_id,
                    telemetry.inspection_id,
                    telemetry.source_url,
                    request_hash,
                    json.dumps(selected_element_ids),
                    testing_goal,
                    notes,
                    telemetry.configured_model,
                    telemetry.model_version,
                    telemetry.prompt_tokens,
                    telemetry.output_tokens,
                    telemetry.total_tokens,
                    telemetry.token_source,
                    telemetry.model_response_ms,
                    telemetry.browser_duration_ms,
                    1 if telemetry.is_reused else 0,
                    json.dumps(analysis.model_dump(), ensure_ascii=False),
                    json.dumps(telemetry.unverified_element_ids),
                    telemetry.created_at,
                ),
            )

    def list_llm_calls(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM llm_calls
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        results = []
        for row in rows:
            results.append(
                {
                    "telemetry": self._row_to_telemetry(row).model_dump(),
                    "selected_element_ids": json.loads(row["selected_ids_json"]),
                    "testing_goal": row["testing_goal"],
                    "notes": row["notes"],
                    "analysis": json.loads(row["analysis_json"]),
                }
            )
        return results

    def get_latency_averages(self, source_url: str | None = None) -> LatencyAverages:
        """Compute average model response time across fresh calls (session & per-page)."""
        with self._connect() as conn:
            s_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_calls,
                    SUM(CASE WHEN is_reused = 0 THEN 1 ELSE 0 END) AS fresh_calls,
                    AVG(CASE WHEN is_reused = 0 THEN model_response_ms END) AS avg_ms
                FROM llm_calls
                """
            ).fetchone()

            if source_url:
                p_row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total_calls,
                        SUM(CASE WHEN is_reused = 0 THEN 1 ELSE 0 END) AS fresh_calls,
                        AVG(CASE WHEN is_reused = 0 THEN model_response_ms END) AS avg_ms
                    FROM llm_calls
                    WHERE source_url = ?
                    """,
                    (source_url,),
                ).fetchone()
            else:
                p_row = None

        return LatencyAverages(
            session_fresh_calls=int(s_row["fresh_calls"] or 0),
            session_total_calls=int(s_row["total_calls"] or 0),
            session_avg_model_ms=(
                round(float(s_row["avg_ms"]), 1)
                if s_row["avg_ms"] is not None
                else None
            ),
            page_fresh_calls=int(p_row["fresh_calls"] or 0) if p_row else 0,
            page_total_calls=int(p_row["total_calls"] or 0) if p_row else 0,
            page_avg_model_ms=(
                round(float(p_row["avg_ms"]), 1)
                if p_row and p_row["avg_ms"] is not None
                else None
            ),
        )

    @staticmethod
    def _row_to_telemetry(row: sqlite3.Row) -> LLMCallTelemetry:
        return LLMCallTelemetry(
            call_id=row["call_id"],
            inspection_id=row["inspection_id"],
            source_url=row["source_url"],
            created_at=row["created_at"],
            configured_model=row["configured_model"],
            model_version=row["model_version"],
            prompt_tokens=row["prompt_tokens"],
            output_tokens=row["output_tokens"],
            total_tokens=row["total_tokens"],
            token_source=row["token_source"],
            model_response_ms=row["model_response_ms"],
            browser_duration_ms=row["browser_duration_ms"],
            is_reused=bool(row["is_reused"]),
            unverified_element_ids=json.loads(row["unverified_ids_json"]),
        )
