"""Security, URL policy, subprocess env sanitization, and groundedness validation."""

from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

from app.schemas import GroundedAnalysis, InspectionRecord, ObservedElement


INSPECTION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{4,64}$")

# Explicit OS/Playwright environment variables permitted inside the isolated runner.
# Notice GEMINI_API_KEY and all application secrets are intentionally excluded.
RUNNER_ALLOWED_ENV_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "PROGRAMDATA",
    "WINDIR",
    "COMSPEC",
    "LOCALAPPDATA",
    "APPDATA",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "TEMP",
    "TMP",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES",
    "PLAYWRIGHT_BROWSERS_PATH",
    "HOME",
    "DISPLAY",
    "XAUTHORITY",
)


class SecurityValidationError(ValueError):
    """Raised when a URL, identifier, or request violates security guardrails."""


def validate_target_url(raw_url: str, allowed_hosts: tuple[str, ...] = ()) -> str:
    """Validate that the target URL uses http/https and satisfies any host allowlist."""
    url = raw_url.strip()
    if not url:
        raise SecurityValidationError("Target URL must not be empty.")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise SecurityValidationError(
            f"Unsupported URL scheme '{parsed.scheme or 'none'}'. Only http:// and https:// are allowed."
        )
    if not parsed.hostname:
        raise SecurityValidationError("Target URL must include a valid hostname.")

    hostname = parsed.hostname.lower()
    if allowed_hosts and hostname not in allowed_hosts:
        raise SecurityValidationError(
            f"Host '{hostname}' is not in ALLOWED_HOSTS ({', '.join(allowed_hosts)})."
        )

    return url


def validate_inspection_id(inspection_id: str) -> str:
    """Prevent path traversal when accessing per-run artifacts on disk."""
    if not INSPECTION_ID_RE.match(inspection_id):
        raise SecurityValidationError("Invalid inspection identifier format.")
    return inspection_id


def build_sanitized_runner_env(
    source_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal environment for the isolated runner with zero API secrets."""
    env_source = source_env if source_env is not None else dict(os.environ)
    sanitized: dict[str, str] = {}

    # Case-insensitive lookup for Windows compatibility
    lower_map = {k.upper(): (k, v) for k, v in env_source.items()}
    for allowed_key in RUNNER_ALLOWED_ENV_KEYS:
        if allowed_key in lower_map:
            orig_key, val = lower_map[allowed_key]
            sanitized[orig_key] = val

    sanitized["PYTHONUTF8"] = "1"
    sanitized["PYTHONIOENCODING"] = "utf-8"
    return sanitized


def _clip(text: str, limit: int = 180) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def wrap_untrusted_page_data(
    inspection: InspectionRecord, selected_elements: list[ObservedElement]
) -> str:
    """Serialize observed DOM facts inside explicit untrusted-data delimiters."""
    compact_elements = []
    for el in selected_elements:
        compact_elements.append(
            {
                "element_id": el.element_id,
                "tag": el.tag,
                "role": el.role,
                "input_type": el.input_type,
                "visible_text": _clip(el.visible_text, 120),
                "accessible_label": _clip(el.accessible_label, 120),
                "placeholder": _clip(el.placeholder, 100),
                "element_name": _clip(el.element_name, 80),
                "html_id": _clip(el.html_id, 80),
                "data_testid": _clip(el.data_testid, 80),
                "href": _clip(el.href, 140),
                "required": el.required,
                "disabled": el.disabled,
                "recommended_locator": (
                    el.recommended_locator.model_dump()
                    if el.recommended_locator
                    else None
                ),
            }
        )

    payload = {
        "source_url": inspection.source_url,
        "final_url": inspection.final_url,
        "page_title": _clip(inspection.page_title, 150),
        "scope_selector": inspection.scope_selector or "(entire page)",
        "selected_elements": compact_elements,
    }
    # Escape angle brackets inside the JSON so page text can never contain a literal
    # "</observed_page_data>" and close the untrusted-data block early. JSON parsers
    # read < / > back as < and >, so the data itself is unchanged.
    body = (
        json.dumps(payload, indent=2, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return "<observed_page_data>\n" + body + "\n</observed_page_data>"


def verify_grounded_element_ids(
    analysis: GroundedAnalysis, valid_element_ids: set[str]
) -> list[str]:
    """Return any element_ids cited by the LLM that were NOT actually observed."""
    cited: set[str] = set()
    for obs in analysis.observations:
        if obs.element_id:
            cited.add(obs.element_id.strip())
    for action in analysis.suggested_next_actions:
        for eid in action.element_ids:
            if eid:
                cited.add(eid.strip())

    unverified = sorted(eid for eid in cited if eid not in valid_element_ids)
    return unverified
