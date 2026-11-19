# 📌 Project Handoff & Current Status

**Last Updated:** 2026-11-19  
**Project:** Enterprise AI Risk-Based Testing (RBT) Platform (PDH v2.0)  
**GitHub Repository:** https://github.com/jyotiranjanojha/pdh_ai_risk_based_testing.git (`main` @ `de2f593`, plus this `HANDOFF.md`)

---

## 1. Current State (MVP Ready for Review)
- **Mode:** Live Snowflake Mode (`SNOWFLAKE_MOCK_MODE=false` in local `backend/.env`, gitignored)
- **Snowflake Target:** Account `XD14286-IFCDWm4`, DB/Schema `IF_SUPPLY_CHAIN.PDH_SUPPLY_PLANNING_SRC_BASE`, Warehouse `IF_WH_SCHAIN_CONSUMPTION`
- **Seeded Tenants (6 Data Hubs):**
  - `IP Supply Planning Data Hub`, `IP Demand Planning Data Hub`, `IP Material Planning Data Hub`
  - `IF Supply Planning Data Hub`, `IF Demand Planning Data Hub`, `IF Material Planning Data Hub`
- **Cortex AI Models:** `claude-sonnet-4-5` (default), `llama3.3-70b`, `llama3.1-70b`, `mistral-large2`

---

## 2. Completed & Verified
- **Full-Stack MVP:** FastAPI backend (`backend/`) + Next.js 14 App Router UI (`frontend/`) with full-width **Light** default theme (Dark toggle in top bar).
- **One-Click Snowflake SSO:** Top-bar **`Snowflake SSO`** button triggers `externalbrowser` auth without blocking the backend event loop.
- **UI Pages Built:**
  - `/` — Executive Dashboard
  - `/onboard` — AI Dataset Onboarding Wizard (dynamic **Target Schema** dropdown per team DB + Cortex rule generation)
  - `/rules` — Risk Matrix & SQL Studio (CodeMirror SQL editor, rule versioning, live **Dry Run**)
  - `/runs` — Gate Logs & Triage (batch execution, sample failing keys, CSV export, AI diagnosis)
  - `/marketplace` — Rule Marketplace (cross-hub rule cloning with Cortex column remapping)
  - `/help` — Step-by-Step User Guide with real screenshots (`frontend/public/screenshots/`)
- **Live-Mode Fixes Applied:**
  - Extracted `SNOWFLAKE.CORTEX.COMPLETE` responses from both `structured_output[0].raw_message` and `choices[0].messages`.
  - Fixed live `dry-run` / query execution (`cur.execute` + `cur.description` guard + `RLock` thread safety on shared SSO connection).
  - Normalized `sample_failing_keys` JSON serialization (`default=str`) for Snowflake `Decimal`/`DATE`/`TIMESTAMP`.
  - Wired `PATCH /api/v1/teams/{team_id}` (`sf_client.update_team`) for Live Snowflake mode.
- **Docs & Repo:** Created interactive `README.md`, `architecture.md`, and `.gitignore`; pushed to GitHub.
- **Latest Verification:**
  - `backend\.venv\Scripts\python.exe -m pytest backend\tests -q` → `28 passed`
  - `cd frontend; npx tsc --noEmit` & `npm run build` → exited `0`

---

## 3. How to Resume Quickly

### Start Servers (Local Windows)
1. **Backend (port `8000`):**
   ```powershell
   cd backend
   .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
2. **Frontend (port `3000`):**
   ```powershell
   cd frontend
   npm run start -- -p 3000
   ```
3. Open **http://localhost:3000** and click **`Snowflake SSO`** in the top bar.

### Run Automated Checks
```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
cd frontend; npx tsc --noEmit; npm run build
```

---

## 4. Suggested Next Items (Post-MVP)
- Collect user UAT feedback from Data Hub teams.
- Add a dynamic **Target Table / View** dropdown/autocomplete under the selected Target Schema on `/onboard`.
- Disable `ALLOW_DEMO_AUTH_HEADERS` and wire corporate OIDC/JWT validation for shared/production deployment.
- Integrate Airflow / Control-M DAGs using team `X-API-Key` against `/api/v1/execution/run-gate`.
- Commit and push `HANDOFF.md` when ready (`git add HANDOFF.md && git commit -m "Add project handoff doc" && git push`).
