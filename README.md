# Human-in-the-Loop Web Testing Assistant (Phase 1)

A lightweight, human-supervised web testing assistant built with **Python 3.13**, **FastAPI**, **Playwright (visible Chromium in an isolated subprocess)**, **SQLite**, and the **Google Gemini free-tier REST API**.

Phase 1 delivers the system architecture ([docs/PHASE1_DESIGN.md](docs/PHASE1_DESIGN.md)) and a working guided inspection prototype that inspects live webpages, validates candidate Playwright locators, lets a human tester refine scope and enter a testing goal, and runs grounded Gemini analysis with separated browser vs. model telemetry.

---

## Project Structure

```text
├── .env.example              # Safe config template (copy to .env; never commit .env)
├── .gitignore                # Excludes .env, SQLite DB, per-run screenshots, caches
├── requirements.txt          # Minimal Python dependencies
├── docs/
│   └── PHASE1_DESIGN.md      # Architecture, data flow, approval gates & Phase 2 plan
├── app/
│   ├── config.py             # Loads .env settings (never exposes API keys to UI/runner)
│   ├── schemas.py            # Pydantic models for inspections, locators, analysis & telemetry
│   ├── security.py           # URL allowlist, subprocess env sanitization, data wrapper, ID check
│   ├── storage.py            # Built-in sqlite3 storage, caching & latency averages
│   ├── runner_bridge.py      # Spawns isolated browser subprocess (shell=False, stripped env)
│   ├── llm_client.py         # Direct httpx client for Google Gemini free-tier REST API
│   └── main.py               # FastAPI routes + static web UI
├── runner/
│   └── inspect_worker.py     # Standalone visible Chromium DOM inspector & locator verifier
├── static/
│   ├── index.html            # 3-panel Phase 1 web interface
│   ├── styles.css            # Clean responsive styling
│   └── app.js                # Element filtering, screenshot highlight boxes, Gemini UI & export
└── tests/
    └── test_phase1_core.py   # Deterministic unit tests (security, storage, mocked Gemini)
```

---

## Setup Instructions

### 1. Install Dependencies
```powershell
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Your Free-Tier Model (`.env`)
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
Edit `.env` and fill in your **free-tier** Google AI Studio API key and active model ID:
```ini
GEMINI_API_KEY=your_actual_free_tier_key
LLM_MODEL=gemini-3.5-flash-lite
ALLOWED_HOSTS=webdriveruniversity.com,www.webdriveruniversity.com
RUNNER_TIMEOUT_SEC=60
BROWSER_SLOWMO_MS=0
BROWSER_HOLD_MS=1200
```
> **Security Note:** `.env` is listed in `.gitignore`. Keep `.env` and API secrets out of any submitted archive or repository.

---

## Running the Assistant

Start the FastAPI web server from the project root in **one terminal**:
```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Open **`http://127.0.0.1:8000`** in your browser.

---

## Phase 1 Demonstration Walkthrough

1. **Inspect a Webpage (3 pts):**
   - Enter a target URL (for example, `https://webdriveruniversity.com/Login-Portal/index.html`) and click **Inspect Page**.
   - A **visible Chromium window** opens in a separate restricted subprocess, extracts interactive elements, checks `locator.count()` live for uniqueness, captures `screenshot.png`, and saves the structured findings to SQLite (`storage/assistant.db`).
2. **Refine Scope & Select Elements (2 pts):**
   - Optionally enter a CSS scope selector (e.g., `form`) and re-inspect, or filter the table by tag/keyword.
   - Hover or check elements in the table to see their corresponding **highlight boxes** on the captured page screenshot.
   - Enter a **Testing Goal** (e.g., *"Verify invalid credentials display an error alert"*).
3. **Grounded Model Call & Feasibility Evidence (1 pt):**
   - Click **Analyze Selected Elements** to send only the selected observed elements (wrapped inside `<observed_page_data>` as untrusted data) to Gemini.
   - Review the grounded summary, element-ID-cited observations, assumptions/missing business rules, clarifying questions, and suggested next actions.
   - Observe **Browser Inspection Time** displayed separately from **Model Response Time**, exact provider `modelVersion` & `usageMetadata` token counts, Fresh vs. Reused cache behavior, and session/page average model latency.
4. **Export Evidence for Submission:**
   - Click **Export Inspection JSON** to save a sample structured inspection output.
   - Click **Export Usage/Timing Evidence** to save model configuration, token counts, and timing logs (contains zero API secrets).

---

## Running Automated Verification Tests

```powershell
pytest tests/test_phase1_core.py -v
```
All tests run offline with zero API calls, verifying URL/origin validation, subprocess secret stripping, prompt-injection data wrapping, element-ID groundedness auditing, SQLite persistence/averages, and Gemini telemetry parsing.
