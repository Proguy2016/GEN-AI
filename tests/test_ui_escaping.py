"""Regression test: webpage- and model-derived text must never become live HTML in the UI.

Loads the real static/index.html + static/app.js in headless Chromium with the API mocked,
renders an inspection and a model analysis whose every text field carries an injection
payload, and checks that nothing executed and the payloads appear only as plain text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

STATIC = Path(__file__).resolve().parent.parent / "static"
ORIGIN = "http://app.test"

# Each payload tries a different way out: a new element, an event handler, a script,
# and breaking out of a double-quoted attribute.
P = '<img src=x onerror="window.__xss=1"><b id="injected">x</b>'
Q = '"><script>window.__xss=2</script><i id="injected2">'


def _element(eid: str) -> dict:
    return {
        "element_id": eid + Q,
        "tag": "button" + P,
        "role": "button" + Q,
        "input_type": "text" + P,
        "visible_text": "Login " + P,
        "accessible_label": "Label " + Q,
        "placeholder": "Ph " + P,
        "element_name": "nm" + Q,
        "html_id": "id" + P,
        "data_testid": "tid" + Q,
        "href": "https://example.com/" + P,
        "required": True,
        "disabled": False,
        "visible": True,
        "bbox": {"x": 10, "y": 10, "width": 50, "height": 20},
        "recommended_locator": {
            "strategy": "get_by_role" + P,
            "expression": "page.get_by_role('button', name='" + Q + "')",
            "match_count": 1,
            "is_unique": True,
        },
        "candidate_locators": [
            {"strategy": "css_id" + Q, "expression": "page.locator('" + P + "')", "match_count": 2, "is_unique": False}
        ],
    }


RECORD = {
    "inspection_id": "insp_hostile01",
    "source_url": "https://example.com/login",
    "final_url": "https://example.com/login",
    "page_title": "Title " + P,
    "scope_selector": "form",
    "visible_only": True,
    "browser_duration_ms": 1234,
    "viewport_width": 1280,
    "viewport_height": 800,
    "total_matched_elements": 1,
    "elements": [_element("el_001")],
}

ANALYSIS = {
    "analysis": {
        "summary": "Summary " + P,
        "observations": [{"element_id": "el_001" + Q, "detail": "Detail " + P, "locator_hint": "hint " + Q}],
        "suggested_next_actions": [{"title": "Do " + P, "rationale": "Because " + Q, "element_ids": ["el_001" + P]}],
        "assumptions_and_unknowns": ["Assume " + P],
        "clarifying_questions": ["Ask " + Q],
    },
    "telemetry": {
        "browser_duration_ms": 1234, "model_response_ms": 500, "is_reused": False,
        "model_version": "m" + P, "configured_model": "m", "prompt_tokens": 1, "output_tokens": 2,
        "total_tokens": 3, "token_source": "provider", "unverified_element_ids": ["el_001" + P],
    },
    "averages": {"session_avg_model_ms": 500, "page_avg_model_ms": 500,
                 "session_fresh_calls": 1, "session_total_calls": 1},
}

TELEMETRY_LOG = {
    "calls": [{
        "telemetry": {"created_at": "2026-11-20T10:00:00" + P, "model_version": "m" + Q, "is_reused": False,
                      "model_response_ms": 500, "browser_duration_ms": 1234, "prompt_tokens": 1,
                      "output_tokens": 2, "total_tokens": 3, "token_source": "provider" + P},
        "testing_goal": "Goal " + P,
    }],
    "averages": ANALYSIS["averages"],
}


def _route(route) -> None:
    path = route.request.url[len(ORIGIN):].split("?")[0]
    if path in ("/", "/index.html"):
        route.fulfill(path=str(STATIC / "index.html"), content_type="text/html")
    elif path.startswith("/static/"):
        route.fulfill(path=str(STATIC / path[len("/static/"):]))
    elif path.startswith("/api/telemetry"):
        route.fulfill(json=TELEMETRY_LOG)
    elif path == "/api/inspections":
        route.fulfill(json=[])
    elif path == "/api/config":
        route.fulfill(json={"configured_model": "m", "api_key_configured": True})
    else:
        route.fulfill(status=404, body="")


def test_ui_renders_hostile_page_and_model_text_as_plain_text() -> None:
    try:
        pw = sync_api.sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:  # browser not installed on this machine
        pytest.skip(f"Chromium not available: {exc}")
    try:
        page = browser.new_page()
        page.route(f"{ORIGIN}/**", _route)
        page.goto(f"{ORIGIN}/")
        page.wait_for_load_state("networkidle")

        page.evaluate(f"loadInspectionIntoUI({json.dumps(RECORD)})")
        page.evaluate(f"renderAnalysisResult({json.dumps(ANALYSIS)})")
        page.wait_for_load_state("networkidle")

        # Nothing executed and no injected element exists anywhere in the document.
        assert page.evaluate("window.__xss") is None
        assert page.locator("#injected, #injected2").count() == 0
        assert page.locator("main img[src='x'], main script").count() == 0

        # The payloads are shown to the tester as literal text.
        attr_line = page.locator("#elements-tbody .attr-line").last.inner_text()
        assert '<img src=x onerror="window.__xss=1">' in attr_line
        assert "<script>" in page.locator("#out-questions").inner_text()
        assert "<img" in page.locator("#out-observations").inner_text()

        # Attribute values were not broken out of: data-eid holds the raw id string.
        assert page.locator("#elements-tbody tr").first.get_attribute("data-eid") == RECORD["elements"][0]["element_id"]
    finally:
        browser.close()
        pw.stop()
