"""Standalone isolated Playwright inspector executed in a restricted child process.

Security boundary:
- Does NOT import anything from app/.
- Runs with a stripped environment (no GEMINI_API_KEY or host secrets).
- Restricts main-frame navigations to approved http/https origins.
- Writes only the run screenshot inside its dedicated sandbox working directory.
- Communicates solely via JSON over stdin/stdout.
"""

from __future__ import annotations

import json
import struct
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, sync_playwright


MAX_ELEMENTS = 100

# Deterministic DOM extraction script run inside the inspected page.
DOM_EXTRACT_JS = r"""
({ scopeSelector, visibleOnly, maxElements }) => {
  const root = scopeSelector ? document.querySelector(scopeSelector) : document;
  if (!root) {
    return { error: `Scope selector "${scopeSelector}" matched no element on the page.` };
  }

  const interactiveSelector = [
    'button',
    'a[href]',
    'input:not([type="hidden"])',
    'textarea',
    'select',
    '[role="button"]',
    '[role="link"]',
    '[role="textbox"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="combobox"]',
    '[role="tab"]',
    '[role="menuitem"]',
    '[contenteditable="true"]'
  ].join(', ');

  const allNodes = Array.from(root.querySelectorAll(interactiveSelector));

  function getImplicitRole(el) {
    const explicit = (el.getAttribute('role') || '').trim().toLowerCase();
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (['button', 'submit', 'reset'].includes(type)) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'search') return 'searchbox';
      return 'textbox';
    }
    return '';
  }

  function getAccessibleLabel(el) {
    const ariaLabel = (el.getAttribute('aria-label') || '').trim();
    if (ariaLabel) return ariaLabel;

    const labelledBy = (el.getAttribute('aria-labelledby') || '').trim();
    if (labelledBy) {
      const parts = labelledBy.split(/\s+/)
        .map(id => document.getElementById(id))
        .filter(Boolean)
        .map(n => (n.innerText || n.textContent || '').trim())
        .filter(Boolean);
      if (parts.length) return parts.join(' ');
    }

    if (el.id) {
      try {
        const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lbl) {
          const text = (lbl.innerText || lbl.textContent || '').trim();
          if (text) return text;
        }
      } catch (_) {}
    }

    const parentLabel = el.closest('label');
    if (parentLabel) {
      const clone = parentLabel.cloneNode(true);
      clone.querySelectorAll('input, textarea, select, button').forEach(n => n.remove());
      const text = (clone.innerText || clone.textContent || '').trim();
      if (text) return text;
    }

    const title = (el.getAttribute('title') || '').trim();
    if (title) return title;

    return '';
  }

  function buildCssPath(el) {
    if (el.id) {
      try {
        return '#' + CSS.escape(el.id);
      } catch (_) {}
    }
    const parts = [];
    let current = el;
    let depth = 0;
    while (current && current.nodeType === 1 && depth < 4) {
      let part = current.tagName.toLowerCase();
      if (current.id) {
        try {
          part = '#' + CSS.escape(current.id);
          parts.unshift(part);
          break;
        } catch (_) {}
      }
      const parent = current.parentElement;
      if (parent) {
        const sameTagSiblings = Array.from(parent.children).filter(
          c => c.tagName === current.tagName
        );
        if (sameTagSiblings.length > 1) {
          const index = sameTagSiblings.indexOf(current) + 1;
          part += `:nth-of-type(${index})`;
        }
      }
      parts.unshift(part);
      current = parent;
      depth += 1;
    }
    return parts.join(' > ');
  }

  function isElementVisible(el, rect) {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) {
      return false;
    }
    return rect.width > 0 && rect.height > 0;
  }

  const results = [];
  for (const el of allNodes) {
    const rect = el.getBoundingClientRect();
    const visible = isElementVisible(el, rect);
    if (visibleOnly && !visible) continue;

    const tag = el.tagName.toLowerCase();
    const inputType = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : '';
    let visibleText = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (tag === 'input' && ['button', 'submit', 'reset'].includes(inputType) && !visibleText) {
      visibleText = (el.value || '').trim();
    }
    if (visibleText.length > 140) {
      visibleText = visibleText.slice(0, 137) + '...';
    }

    const dataTestId = (
      el.getAttribute('data-testid') ||
      el.getAttribute('data-test') ||
      el.getAttribute('data-cy') ||
      ''
    ).trim();
    const dataTestAttr = el.hasAttribute('data-testid')
      ? 'data-testid'
      : el.hasAttribute('data-test')
        ? 'data-test'
        : el.hasAttribute('data-cy')
          ? 'data-cy'
          : '';

    results.push({
      tag,
      role: getImplicitRole(el),
      input_type: inputType,
      visible_text: visibleText,
      accessible_label: getAccessibleLabel(el),
      placeholder: (el.getAttribute('placeholder') || '').trim(),
      element_name: (el.getAttribute('name') || '').trim(),
      html_id: (el.id || '').trim(),
      data_testid: dataTestId,
      data_test_attr: dataTestAttr,
      href: (el.getAttribute('href') || '').trim(),
      required: Boolean(el.required || el.getAttribute('aria-required') === 'true'),
      disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      visible,
      css_path: buildCssPath(el),
      bbox: visible
        ? {
            x: Math.round((rect.left + window.scrollX) * 10) / 10,
            y: Math.round((rect.top + window.scrollY) * 10) / 10,
            width: Math.round(rect.width * 10) / 10,
            height: Math.round(rect.height * 10) / 10
          }
        : null
    });
  }

  return {
    total_matched_elements: results.length,
    elements: results.slice(0, maxElements)
  };
}
"""


def _read_png_dimensions(path: Path) -> tuple[int, int]:
    """Read width/height directly from the PNG IHDR header without extra dependencies."""
    if not path.is_file():
        return (0, 0)
    with path.open("rb") as fh:
        header = fh.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)


def _host_matches_target(candidate_host: str, base_host: str, extra_hosts: list[str]) -> bool:
    cand = candidate_host.lower()
    base = base_host.lower()
    if extra_hosts:
        return cand in [h.lower() for h in extra_hosts]
    # Allow exact host or standard www. redirect of the requested target host
    return cand == base or cand == f"www.{base}" or base == f"www.{cand}"


def _build_and_verify_locators(page: Page, raw_el: dict) -> list[dict]:
    """Build ranked Playwright candidate locators and verify uniqueness live via .count()."""
    candidates: list[dict] = []
    seen_exprs: set[str] = set()

    def test_candidate(strategy: str, expr: str, locator_obj) -> None:
        if expr in seen_exprs:
            return
        seen_exprs.add(expr)
        try:
            count = int(locator_obj.count())
        except Exception:
            count = 0
        candidates.append(
            {
                "strategy": strategy,
                "expression": expr,
                "match_count": count,
                "is_unique": count == 1,
            }
        )

    tag = raw_el["tag"]
    role = raw_el["role"]
    text = raw_el["visible_text"]
    label = raw_el["accessible_label"]
    placeholder = raw_el["placeholder"]
    name_attr = raw_el["element_name"]
    html_id = raw_el["html_id"]
    testid = raw_el["data_testid"]
    test_attr = raw_el.get("data_test_attr", "")
    css_path = raw_el.get("css_path", "")

    # 1. Test ID attribute (highest resilience)
    if testid:
        if test_attr == "data-testid":
            test_candidate(
                "get_by_test_id",
                f"page.get_by_test_id({json.dumps(testid)})",
                page.get_by_test_id(testid),
            )
        else:
            sel = f'[{test_attr}={json.dumps(testid)}]'
            test_candidate(
                "data_test_attr",
                f"page.locator({json.dumps(sel)})",
                page.locator(sel),
            )

    # 2. Accessible role + name (Playwright's recommended semantic locator)
    role_name = label or text
    if role and role_name and len(role_name) <= 80:
        test_candidate(
            "get_by_role",
            f"page.get_by_role({json.dumps(role)}, name={json.dumps(role_name)}, exact=True)",
            page.get_by_role(role, name=role_name, exact=True),
        )

    # 3. Form control label
    if label and tag in ("input", "textarea", "select"):
        test_candidate(
            "get_by_label",
            f"page.get_by_label({json.dumps(label)}, exact=True)",
            page.get_by_label(label, exact=True),
        )

    # 4. Placeholder
    if placeholder:
        test_candidate(
            "get_by_placeholder",
            f"page.get_by_placeholder({json.dumps(placeholder)}, exact=True)",
            page.get_by_placeholder(placeholder, exact=True),
        )

    # 5. Visible text (for concise buttons/links)
    if text and len(text) <= 60 and tag in ("button", "a"):
        test_candidate(
            "get_by_text",
            f"page.get_by_text({json.dumps(text)}, exact=True)",
            page.get_by_text(text, exact=True),
        )

    # 6. HTML id attribute
    if html_id:
        css_id = f"[id={json.dumps(html_id)}]" if any(c in html_id for c in ".: []#") else f"#{html_id}"
        test_candidate(
            "css_id",
            f"page.locator({json.dumps(css_id)})",
            page.locator(css_id),
        )

    # 7. HTML name attribute
    if name_attr:
        css_name = f'{tag}[name={json.dumps(name_attr)}]'
        test_candidate(
            "css_name",
            f"page.locator({json.dumps(css_name)})",
            page.locator(css_name),
        )

    # 8. Deterministic CSS path fallback
    if css_path:
        test_candidate(
            "css_path",
            f"page.locator({json.dumps(css_path)})",
            page.locator(css_path),
        )

    return candidates


def main() -> int:
    started = time.perf_counter()
    try:
        config = json.loads(sys.stdin.read())
        url: str = config["url"]
        scope_selector: str = config.get("scope_selector", "")
        visible_only: bool = bool(config.get("visible_only", True))
        screenshot_path = Path(config["screenshot_path"])
        allowed_hosts: list[str] = config.get("allowed_hosts") or []
        slowmo_ms: int = int(config.get("slowmo_ms", 150))
        hold_ms: int = int(config.get("hold_ms", 1500))

        parsed_target = urlparse(url)
        target_host = (parsed_target.hostname or "").lower()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, slow_mo=slowmo_ms)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                device_scale_factor=1,
            )

            def enforce_origin_guard(route: Route) -> None:
                req = route.request
                req_parsed = urlparse(req.url)
                scheme = req_parsed.scheme.lower()
                if scheme not in ("http", "https", "data", "blob"):
                    route.abort("blockedbyclient")
                    return
                if req.is_navigation_request() and req.frame == page.main_frame:
                    if not req_parsed.hostname or not _host_matches_target(
                        req_parsed.hostname, target_host, allowed_hosts
                    ):
                        route.abort("blockedbyclient")
                        return
                route.continue_()

            page = context.new_page()
            context.route("**/*", enforce_origin_guard)

            page.goto(url, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(400)

            extracted = page.evaluate(
                DOM_EXTRACT_JS,
                {
                    "scopeSelector": scope_selector,
                    "visibleOnly": visible_only,
                    "maxElements": MAX_ELEMENTS,
                },
            )
            if "error" in extracted:
                browser.close()
                print(json.dumps({"ok": False, "error": extracted["error"]}))
                return 0

            elements_out = []
            for idx, raw_el in enumerate(extracted["elements"], start=1):
                candidates = _build_and_verify_locators(page, raw_el)
                recommended = next(
                    (c for c in candidates if c["is_unique"]),
                    next((c for c in candidates if c["match_count"] >= 1), None),
                )
                elements_out.append(
                    {
                        "element_id": f"el_{idx:03d}",
                        "tag": raw_el["tag"],
                        "role": raw_el["role"],
                        "input_type": raw_el["input_type"],
                        "visible_text": raw_el["visible_text"],
                        "accessible_label": raw_el["accessible_label"],
                        "placeholder": raw_el["placeholder"],
                        "element_name": raw_el["element_name"],
                        "html_id": raw_el["html_id"],
                        "data_testid": raw_el["data_testid"],
                        "href": raw_el["href"],
                        "required": raw_el["required"],
                        "disabled": raw_el["disabled"],
                        "visible": raw_el["visible"],
                        "bbox": raw_el["bbox"],
                        "recommended_locator": recommended,
                        "candidate_locators": candidates,
                    }
                )

            page.screenshot(
                path=str(screenshot_path), full_page=True, animations="disabled"
            )
            shot_w, shot_h = _read_png_dimensions(screenshot_path)
            final_url = page.url
            page_title = page.title()

            if hold_ms > 0:
                page.wait_for_timeout(hold_ms)

            browser.close()

        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        print(
            json.dumps(
                {
                    "ok": True,
                    "final_url": final_url,
                    "page_title": page_title,
                    "browser_duration_ms": elapsed_ms,
                    "screenshot_width": shot_w,
                    "screenshot_height": shot_h,
                    "total_matched_elements": extracted["total_matched_elements"],
                    "elements": elements_out,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
