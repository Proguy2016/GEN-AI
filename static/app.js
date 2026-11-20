/* Phase 1 Human-in-the-Loop Web Testing Assistant — Vanilla JS UI */

const state = {
  inspection: null,
  selectedIds: new Set(),
  hoveredId: null,
  tagFilter: "all",
  searchQuery: "",
};

const els = {
  banner: document.getElementById("status-banner"),
  badgeModel: document.getElementById("badge-model"),
  badgeKey: document.getElementById("badge-key"),
  historySelect: document.getElementById("history-select"),
  btnExportInspection: document.getElementById("btn-export-inspection"),
  inspectForm: document.getElementById("inspect-form"),
  urlInput: document.getElementById("url-input"),
  scopeInput: document.getElementById("scope-input"),
  visibleOnlyInput: document.getElementById("visible-only-input"),
  btnInspect: document.getElementById("btn-inspect"),
  inspectionMeta: document.getElementById("inspection-meta"),
  metaUrl: document.getElementById("meta-url"),
  metaTitle: document.getElementById("meta-title"),
  metaScope: document.getElementById("meta-scope"),
  metaCount: document.getElementById("meta-count"),
  metaBrowserMs: document.getElementById("meta-browser-ms"),
  findingsSection: document.getElementById("findings-section"),
  analysisSection: document.getElementById("analysis-section"),
  selectedCounter: document.getElementById("selected-counter"),
  elementSearch: document.getElementById("element-search"),
  tagFilters: document.getElementById("tag-filters"),
  btnSelectVisible: document.getElementById("btn-select-visible"),
  btnClearSelection: document.getElementById("btn-clear-selection"),
  elementsTbody: document.getElementById("elements-tbody"),
  toggleBoxes: document.getElementById("toggle-boxes"),
  pageScreenshot: document.getElementById("page-screenshot"),
  highlightLayer: document.getElementById("highlight-layer"),
  analyzeForm: document.getElementById("analyze-form"),
  goalInput: document.getElementById("goal-input"),
  notesInput: document.getElementById("notes-input"),
  forceFreshInput: document.getElementById("force-fresh-input"),
  btnAnalyze: document.getElementById("btn-analyze"),
  btnExportTelemetry: document.getElementById("btn-export-telemetry"),
  telemetryPanel: document.getElementById("telemetry-panel"),
  statBrowserMs: document.getElementById("stat-browser-ms"),
  statModelMs: document.getElementById("stat-model-ms"),
  statCallMode: document.getElementById("stat-call-mode"),
  statModelVer: document.getElementById("stat-model-ver"),
  statConfigModel: document.getElementById("stat-config-model"),
  statTokens: document.getElementById("stat-tokens"),
  statTokenSource: document.getElementById("stat-token-source"),
  statAvgMs: document.getElementById("stat-avg-ms"),
  statAvgCounts: document.getElementById("stat-avg-counts"),
  groundednessBanner: document.getElementById("groundedness-banner"),
  outSummary: document.getElementById("out-summary"),
  outObservations: document.getElementById("out-observations"),
  outActions: document.getElementById("out-actions"),
  outAssumptions: document.getElementById("out-assumptions"),
  outQuestions: document.getElementById("out-questions"),
  logCount: document.getElementById("log-count"),
  telemetryLogTbody: document.getElementById("telemetry-log-tbody"),
};

function showBanner(message, type = "info") {
  els.banner.textContent = message;
  els.banner.className = `banner ${type}`;
}

function clearBanner() {
  els.banner.className = "banner hidden";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function apiFetch(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }
  return data;
}

async function loadPublicConfig() {
  try {
    const data = await apiFetch("/api/config");
    const cfg = data.config;
    els.badgeModel.textContent = `Model: ${cfg.configured_model}`;
    if (cfg.api_key_configured) {
      els.badgeKey.textContent = "Gemini Key: Configured";
      els.badgeKey.className = "badge badge-success";
    } else {
      els.badgeKey.textContent = "Gemini Key: Set GEMINI_API_KEY in .env";
      els.badgeKey.className = "badge badge-warn";
    }
  } catch (err) {
    showBanner(`Failed to load runtime config: ${err.message}`, "error");
  }
}

async function refreshHistory() {
  try {
    const items = await apiFetch("/api/inspections");
    const currentId = state.inspection ? state.inspection.inspection_id : "";
    els.historySelect.innerHTML = '<option value="">Load saved inspection...</option>';
    for (const item of items) {
      const opt = document.createElement("option");
      opt.value = item.inspection_id;
      const scopeTxt = item.scope_selector ? ` [${item.scope_selector}]` : "";
      opt.textContent = `${item.page_title || item.source_url}${scopeTxt} (${item.element_count} els · ${item.browser_duration_ms}ms)`;
      if (item.inspection_id === currentId) opt.selected = true;
      els.historySelect.appendChild(opt);
    }
  } catch (err) {
    console.error("Failed to refresh inspection history:", err);
  }
}

async function refreshTelemetryLog() {
  try {
    const query = state.inspection
      ? `?source_url=${encodeURIComponent(state.inspection.source_url)}`
      : "";
    const data = await apiFetch(`/api/telemetry${query}`);
    const calls = data.calls || [];
    els.logCount.textContent = String(calls.length);
    els.telemetryLogTbody.innerHTML = calls
      .map((entry) => {
        const t = entry.telemetry;
        const modeBadge = t.is_reused
          ? '<span class="badge badge-warn">Reused</span>'
          : '<span class="badge badge-success">Fresh</span>';
        return `
          <tr>
            <td>${escapeHtml(t.created_at.replace("T", " ").slice(0, 19))}</td>
            <td><code>${escapeHtml(t.model_version)}</code></td>
            <td>${modeBadge}</td>
            <td>${t.model_response_ms} ms</td>
            <td>${t.browser_duration_ms} ms</td>
            <td>${t.prompt_tokens} / ${t.output_tokens} / <strong>${t.total_tokens}</strong></td>
            <td>${escapeHtml(t.token_source)}</td>
            <td>${escapeHtml(entry.testing_goal)}</td>
          </tr>
        `;
      })
      .join("");
  } catch (err) {
    console.error("Failed to refresh telemetry log:", err);
  }
}

function getFilteredElements() {
  if (!state.inspection) return [];
  const q = state.searchQuery.trim().toLowerCase();
  return state.inspection.elements.filter((el) => {
    if (state.tagFilter !== "all" && el.tag !== state.tagFilter) {
      return false;
    }
    if (!q) return true;
    const haystack = [
      el.element_id,
      el.tag,
      el.role,
      el.input_type,
      el.visible_text,
      el.accessible_label,
      el.placeholder,
      el.element_name,
      el.html_id,
      el.data_testid,
      el.recommended_locator ? el.recommended_locator.expression : "",
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  });
}

function updateSelectedCounter() {
  const n = state.selectedIds.size;
  els.selectedCounter.textContent = `${n} selected`;
}

function renderElementsTable() {
  const rows = getFilteredElements();
  if (!rows.length) {
    els.elementsTbody.innerHTML =
      '<tr><td colspan="5" style="text-align:center;color:#64748b;padding:18px;">No matching interactive elements.</td></tr>';
    renderHighlightBoxes();
    return;
  }

  els.elementsTbody.innerHTML = rows
    .map((el) => {
      const checked = state.selectedIds.has(el.element_id);
      const attrs = [];
      if (el.accessible_label) attrs.push(`label: "${el.accessible_label}"`);
      if (el.visible_text) attrs.push(`text: "${el.visible_text}"`);
      if (el.placeholder) attrs.push(`placeholder: "${el.placeholder}"`);
      if (el.element_name) attrs.push(`name="${el.element_name}"`);
      if (el.html_id) attrs.push(`id="${el.html_id}"`);
      if (el.data_testid) attrs.push(`testid="${el.data_testid}"`);
      if (el.href) attrs.push(`href="${el.href}"`);
      if (el.required) attrs.push("required");
      if (el.disabled) attrs.push("disabled");

      const rec = el.recommended_locator;
      let locBadge = '<span class="badge badge-error">No locator</span>';
      if (rec) {
        if (rec.is_unique) {
          locBadge = '<span class="badge badge-success">Unique (1)</span>';
        } else if (rec.match_count > 1) {
          locBadge = `<span class="badge badge-warn">${rec.match_count} matches</span>`;
        } else {
          locBadge = '<span class="badge badge-error">0 matches</span>';
        }
      }

      const candidatesHtml = (el.candidate_locators || [])
        .map((c) => {
          const status = c.is_unique ? "Unique (1)" : `${c.match_count} matches`;
          return `<li><code>${escapeHtml(c.expression)}</code> — <em>${escapeHtml(c.strategy)}</em> (${status})</li>`;
        })
        .join("");

      const tagSummary = el.input_type ? `${el.tag}[${el.input_type}]` : el.tag;
      const roleSummary = el.role ? `<div class="attr-line">role=${escapeHtml(el.role)}</div>` : "";

      return `
        <tr data-eid="${escapeHtml(el.element_id)}" class="${checked ? "row-selected" : ""}">
          <td class="col-check">
            <input type="checkbox" class="el-checkbox" data-eid="${escapeHtml(el.element_id)}" ${checked ? "checked" : ""} />
          </td>
          <td class="col-id">${escapeHtml(el.element_id)}</td>
          <td class="col-tag"><strong>${escapeHtml(tagSummary)}</strong>${roleSummary}</td>
          <td class="col-attrs">
            ${escapeHtml(el.accessible_label || el.visible_text || el.placeholder || "(no label)")}
            <div class="attr-line">${escapeHtml(attrs.join(" · "))}</div>
          </td>
          <td class="col-loc">
            ${rec ? `<code class="locator-code">${escapeHtml(rec.expression)}</code><br/>` : ""}
            ${locBadge}
            ${
              candidatesHtml
                ? `<details class="loc-details"><summary>All ${el.candidate_locators.length} candidates</summary><ul class="loc-candidates">${candidatesHtml}</ul></details>`
                : ""
            }
          </td>
        </tr>
      `;
    })
    .join("");

  updateSelectedCounter();
  renderHighlightBoxes();
}

function renderHighlightBoxes() {
  els.highlightLayer.innerHTML = "";
  if (!state.inspection || !els.toggleBoxes.checked) return;

  const img = els.pageScreenshot;
  const naturalW = img.naturalWidth || state.inspection.screenshot_width;
  const naturalH = img.naturalHeight || state.inspection.screenshot_height;
  if (!naturalW || !naturalH || !img.clientWidth || !img.clientHeight) return;

  const scaleX = img.clientWidth / naturalW;
  const scaleY = img.clientHeight / naturalH;

  for (const el of state.inspection.elements) {
    if (!el.bbox) continue;
    const isSelected = state.selectedIds.has(el.element_id);
    const isHovered = state.hoveredId === el.element_id;
    if (!isSelected && !isHovered) continue;

    const box = document.createElement("div");
    box.className = `hl-box${isHovered ? " hovered" : ""}`;
    box.style.left = `${el.bbox.x * scaleX}px`;
    box.style.top = `${el.bbox.y * scaleY}px`;
    box.style.width = `${Math.max(el.bbox.width * scaleX, 8)}px`;
    box.style.height = `${Math.max(el.bbox.height * scaleY, 8)}px`;
    box.title = `${el.element_id}: ${el.accessible_label || el.visible_text || el.tag}`;

    const tag = document.createElement("span");
    tag.className = "hl-tag";
    tag.textContent = el.element_id;
    box.appendChild(tag);

    box.addEventListener("click", () => toggleElementSelection(el.element_id));
    els.highlightLayer.appendChild(box);
  }
}

function toggleElementSelection(elementId, explicitState = null) {
  const next = explicitState !== null ? explicitState : !state.selectedIds.has(elementId);
  if (next) {
    state.selectedIds.add(elementId);
  } else {
    state.selectedIds.delete(elementId);
  }
  renderElementsTable();
}

function loadInspectionIntoUI(record) {
  state.inspection = record;
  state.selectedIds = new Set(record.elements.slice(0, 8).map((el) => el.element_id));
  state.hoveredId = null;
  state.tagFilter = "all";
  state.searchQuery = "";
  els.elementSearch.value = "";
  els.tagFilters.querySelectorAll(".pill").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tag === "all");
  });

  els.urlInput.value = record.source_url;
  els.scopeInput.value = record.scope_selector || "";
  els.visibleOnlyInput.checked = Boolean(record.visible_only);

  els.metaUrl.href = record.final_url || record.source_url;
  els.metaUrl.textContent = record.final_url || record.source_url;
  els.metaTitle.textContent = record.page_title || "(untitled)";
  els.metaScope.textContent = record.scope_selector || "(entire page)";
  els.metaCount.textContent =
    record.total_matched_elements > record.elements.length
      ? `${record.elements.length} shown of ${record.total_matched_elements} matched`
      : `${record.elements.length} interactive elements`;
  els.metaBrowserMs.textContent = `${record.browser_duration_ms} ms`;

  els.inspectionMeta.classList.remove("hidden");
  els.findingsSection.classList.remove("hidden");
  els.analysisSection.classList.remove("hidden");
  els.telemetryPanel.classList.add("hidden");
  els.btnExportInspection.disabled = false;

  els.pageScreenshot.src = `/api/runs/${encodeURIComponent(record.inspection_id)}/screenshot?t=${Date.now()}`;
  renderElementsTable();
  refreshTelemetryLog();
}

function renderAnalysisResult(resp) {
  const { analysis, telemetry, averages } = resp;
  const unverifiedSet = new Set(telemetry.unverified_element_ids || []);

  els.statBrowserMs.textContent = `${telemetry.browser_duration_ms} ms`;
  els.statModelMs.textContent = telemetry.is_reused
    ? "0 ms (cached)"
    : `${telemetry.model_response_ms} ms`;
  els.statCallMode.textContent = telemetry.is_reused
    ? "Reused from SQLite cache"
    : "Fresh Gemini API call";
  els.statModelVer.textContent = telemetry.model_version;
  els.statConfigModel.textContent = `Configured: ${telemetry.configured_model}`;
  els.statTokens.textContent = `${telemetry.prompt_tokens} in + ${telemetry.output_tokens} out = ${telemetry.total_tokens}`;
  els.statTokenSource.textContent = `Token count source: ${telemetry.token_source}`;

  const sessionAvgTxt =
    averages.session_avg_model_ms !== null ? `${averages.session_avg_model_ms} ms` : "—";
  const pageAvgTxt =
    averages.page_avg_model_ms !== null ? `${averages.page_avg_model_ms} ms` : "—";
  els.statAvgMs.textContent = `Session: ${sessionAvgTxt} · Page: ${pageAvgTxt}`;
  els.statAvgCounts.textContent = `${averages.session_fresh_calls} fresh / ${averages.session_total_calls} total calls`;

  if (unverifiedSet.size > 0) {
    els.groundednessBanner.className = "banner warn";
    els.groundednessBanner.textContent = `Groundedness Warning: Gemini cited element ID(s) not in your selection: ${Array.from(unverifiedSet).join(", ")}. Review carefully before acting.`;
  } else {
    els.groundednessBanner.className = "banner success";
    els.groundednessBanner.textContent =
      "Groundedness Check Passed: Every cited element_id matches an observed interactive element in the stored inspection.";
  }

  els.outSummary.textContent = analysis.summary || "(no summary returned)";

  els.outObservations.innerHTML = (analysis.observations || [])
    .map((obs) => {
      const bad = unverifiedSet.has(obs.element_id);
      const chip = `<span class="eid-chip${bad ? " unverified" : ""}">${escapeHtml(obs.element_id)}</span>`;
      const loc = obs.locator_hint ? ` <code>${escapeHtml(obs.locator_hint)}</code>` : "";
      return `<li>${chip}${escapeHtml(obs.detail)}${loc}</li>`;
    })
    .join("") || "<li>No element observations returned.</li>";

  els.outActions.innerHTML = (analysis.suggested_next_actions || [])
    .map((act) => {
      const chips = (act.element_ids || [])
        .map((id) => `<span class="eid-chip${unverifiedSet.has(id) ? " unverified" : ""}">${escapeHtml(id)}</span>`)
        .join("");
      return `<li>${chips}<strong>${escapeHtml(act.title)}:</strong> ${escapeHtml(act.rationale)}</li>`;
    })
    .join("") || "<li>No suggested actions returned.</li>";

  els.outAssumptions.innerHTML = (analysis.assumptions_and_unknowns || [])
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("") || "<li>None listed.</li>";

  els.outQuestions.innerHTML = (analysis.clarifying_questions || [])
    .map((q) => `<li>${escapeHtml(q)}</li>`)
    .join("") || "<li>No clarifying questions raised.</li>";

  els.telemetryPanel.classList.remove("hidden");
  refreshTelemetryLog();
}

// Event listeners
els.inspectForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearBanner();
  els.btnInspect.disabled = true;
  els.btnInspect.textContent = "Inspecting in Chromium...";
  showBanner("Launching isolated visible Chromium runner to inspect the webpage...", "info");

  try {
    const record = await apiFetch("/api/inspections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: els.urlInput.value.trim(),
        scope_selector: els.scopeInput.value.trim(),
        visible_only: els.visibleOnlyInput.checked,
      }),
    });
    loadInspectionIntoUI(record);
    await refreshHistory();
    showBanner(
      `Inspection saved (${record.inspection_id}): found ${record.elements.length} interactive elements in ${record.browser_duration_ms} ms.`,
      "success"
    );
  } catch (err) {
    showBanner(`Inspection failed: ${err.message}`, "error");
  } finally {
    els.btnInspect.disabled = false;
    els.btnInspect.textContent = "Inspect Page";
  }
});

els.historySelect.addEventListener("change", async () => {
  const id = els.historySelect.value;
  if (!id) return;
  clearBanner();
  try {
    const record = await apiFetch(`/api/inspections/${encodeURIComponent(id)}`);
    loadInspectionIntoUI(record);
    showBanner(`Loaded saved inspection ${record.inspection_id}.`, "info");
  } catch (err) {
    showBanner(`Failed to load inspection: ${err.message}`, "error");
  }
});

els.btnExportInspection.addEventListener("click", () => {
  if (!state.inspection) return;
  window.location.href = `/api/inspections/${encodeURIComponent(state.inspection.inspection_id)}/export`;
});

els.btnExportTelemetry.addEventListener("click", () => {
  const query = state.inspection
    ? `?source_url=${encodeURIComponent(state.inspection.source_url)}`
    : "";
  window.location.href = `/api/telemetry/export${query}`;
});

els.elementSearch.addEventListener("input", () => {
  state.searchQuery = els.elementSearch.value;
  renderElementsTable();
});

els.tagFilters.addEventListener("click", (e) => {
  const btn = e.target.closest(".pill");
  if (!btn) return;
  state.tagFilter = btn.dataset.tag;
  els.tagFilters.querySelectorAll(".pill").forEach((b) => b.classList.toggle("active", b === btn));
  renderElementsTable();
});

els.btnSelectVisible.addEventListener("click", () => {
  for (const el of getFilteredElements()) {
    state.selectedIds.add(el.element_id);
  }
  renderElementsTable();
});

els.btnClearSelection.addEventListener("click", () => {
  state.selectedIds.clear();
  renderElementsTable();
});

els.elementsTbody.addEventListener("change", (e) => {
  const cb = e.target.closest(".el-checkbox");
  if (!cb) return;
  toggleElementSelection(cb.dataset.eid, cb.checked);
});

els.elementsTbody.addEventListener("click", (e) => {
  if (e.target.closest("input, summary, details, a, code")) return;
  const row = e.target.closest("tr[data-eid]");
  if (!row) return;
  toggleElementSelection(row.dataset.eid);
});

els.elementsTbody.addEventListener("mouseover", (e) => {
  const row = e.target.closest("tr[data-eid]");
  const nextId = row ? row.dataset.eid : null;
  if (nextId !== state.hoveredId) {
    state.hoveredId = nextId;
    renderHighlightBoxes();
  }
});

els.elementsTbody.addEventListener("mouseleave", () => {
  if (state.hoveredId !== null) {
    state.hoveredId = null;
    renderHighlightBoxes();
  }
});

els.toggleBoxes.addEventListener("change", renderHighlightBoxes);
els.pageScreenshot.addEventListener("load", renderHighlightBoxes);
window.addEventListener("resize", renderHighlightBoxes);

els.analyzeForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearBanner();

  if (!state.inspection) {
    showBanner("Please inspect a webpage first.", "warn");
    return;
  }
  if (state.selectedIds.size === 0) {
    showBanner("Select at least one observed element in Step 2 before calling Gemini.", "warn");
    return;
  }

  els.btnAnalyze.disabled = true;
  els.btnAnalyze.textContent = "Calling Gemini...";
  showBanner("Sending selected observed elements (as untrusted data) and testing goal to Gemini...", "info");

  try {
    const resp = await apiFetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        inspection_id: state.inspection.inspection_id,
        selected_element_ids: Array.from(state.selectedIds),
        testing_goal: els.goalInput.value.trim(),
        notes: els.notesInput.value.trim(),
        force_fresh: els.forceFreshInput.checked,
      }),
    });
    renderAnalysisResult(resp);
    showBanner(
      resp.telemetry.is_reused
        ? "Returned cached grounded analysis (Reused). Check 'Force fresh model call' to query Gemini again."
        : `Fresh Gemini analysis completed in ${resp.telemetry.model_response_ms} ms (${resp.telemetry.total_tokens} ${resp.telemetry.token_source} tokens).`,
      "success"
    );
  } catch (err) {
    showBanner(`Gemini analysis failed: ${err.message}`, "error");
  } finally {
    els.btnAnalyze.disabled = false;
    els.btnAnalyze.textContent = "Analyze Selected Elements";
  }
});

// Boot
loadPublicConfig();
refreshHistory();
refreshTelemetryLog();
