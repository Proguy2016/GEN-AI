"""Subprocess bridge isolating browser automation from the core FastAPI server."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.schemas import InspectionRecord, ObservedElement
from app.security import build_sanitized_runner_env, validate_target_url


WORKER_SCRIPT = PROJECT_ROOT / "runner" / "inspect_worker.py"


class RunnerExecutionError(RuntimeError):
    """Raised when the isolated browser runner fails or times out."""


def run_isolated_inspection(
    url: str,
    scope_selector: str,
    visible_only: bool,
    settings: Settings,
) -> InspectionRecord:
    """Spawn runner/inspect_worker.py in a restricted child process and parse its JSON output."""
    validated_url = validate_target_url(url, settings.allowed_hosts)
    inspection_id = f"insp_{uuid.uuid4().hex[:12]}"
    run_dir: Path = settings.runs_dir / inspection_id
    run_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = run_dir / "screenshot.png"

    worker_payload = {
        "url": validated_url,
        "scope_selector": scope_selector.strip(),
        "visible_only": visible_only,
        "screenshot_path": str(screenshot_path),
        "allowed_hosts": list(settings.allowed_hosts),
        "slowmo_ms": settings.browser_slowmo_ms,
        "hold_ms": settings.browser_hold_ms,
    }

    try:
        completed = subprocess.run(
            [sys.executable, str(WORKER_SCRIPT)],
            input=json.dumps(worker_payload),
            cwd=str(run_dir),
            env=build_sanitized_runner_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            shell=False,
            timeout=settings.runner_timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerExecutionError(
            f"Isolated browser runner timed out after {settings.runner_timeout_sec}s."
        ) from exc

    if completed.returncode != 0:
        err_detail = (completed.stderr or completed.stdout or "Unknown runner error").strip()
        raise RunnerExecutionError(f"Browser runner failed: {err_detail}")

    try:
        raw_result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerExecutionError(
            f"Browser runner returned invalid JSON: {completed.stdout[:300]}"
        ) from exc

    if not raw_result.get("ok"):
        raise RunnerExecutionError(raw_result.get("error", "Inspection failed."))

    elements = [
        ObservedElement.model_validate(item) for item in raw_result.get("elements", [])
    ]

    return InspectionRecord(
        inspection_id=inspection_id,
        source_url=validated_url,
        final_url=raw_result.get("final_url", validated_url),
        page_title=raw_result.get("page_title", ""),
        scope_selector=scope_selector.strip(),
        visible_only=visible_only,
        created_at=datetime.now(timezone.utc).isoformat(),
        browser_duration_ms=int(raw_result.get("browser_duration_ms", 0)),
        screenshot_width=int(raw_result.get("screenshot_width", 0)),
        screenshot_height=int(raw_result.get("screenshot_height", 0)),
        screenshot_relpath=f"storage/runs/{inspection_id}/screenshot.png",
        total_matched_elements=int(raw_result.get("total_matched_elements", len(elements))),
        elements=elements,
    )
