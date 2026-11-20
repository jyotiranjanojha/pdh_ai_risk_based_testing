# 📌 Project Handoff & Current Status

**Last Updated:** 2026-11-20  
**Project:** Enterprise AI Risk-Based Testing (RBT) Platform (**PDH v2.1**)  
**GitHub Repository:** https://github.com/jyotiranjanojha/pdh_ai_risk_based_testing.git (`main` @ `de2f593` + local v2.1 changes ready to commit)

---

## 1. Current State (v2.1 Ready for UAT)
- **Mode:** Live Snowflake Mode (`SNOWFLAKE_MOCK_MODE=false` in local `backend/.env`, gitignored)
- **Snowflake Target:** Account `XD14286-IFCDWm4`, DB/Schema `IF_SUPPLY_CHAIN.PDH_SUPPLY_PLANNING_SRC_BASE`, Warehouse `IF_WH_SCHAIN_CONSUMPTION`
- **Snowflake DDL:** Both `backend/app/db/migrations/01_init_ddl.sql` and `backend/app/db/migrations/02_rbt_v21_upgrade.sql` have been executed in Snowflake.
- **Seeded Tenants (6 Data Hubs):**
  - `IP Supply Planning Data Hub`, `IP Demand Planning Data Hub`, `IP Material Planning Data Hub`
  - `IF Supply Planning Data Hub`, `IF Demand Planning Data Hub`, `IF Material Planning Data Hub`
- **Cortex AI Models:** `claude-sonnet-4-5` (default), `llama3.3-70b`, `llama3.1-70b`, `mistral-large2`

---

## 2. Completed & Verified Today (2026-11-20)

### A. PDH v2.1 Upgrade (All 4 Scopes, Dual-Mode Parity)
1. **Scope 1 — Quantitative Risk Scoring (`Likelihood × Impact`) & Release Confidence Scorecard:**
   - Added `LIKELIHOOD_SCORE` (1–5), `IMPACT_SCORE` (1–5), `RISK_SCORE` (1–25) with auto-priority mapping (`16–25 → P1_BLOCKER`, `8–15 → P2_WARNING`, `1–7 → P3_INFO`, preserving manual overrides).
   - Updated `CORTEX_RULE_LIST_SCHEMA` and added `COVERAGE_PCT` + `RELEASE_CONFIDENCE_SCORE` (0–100%) to `RBT_EXECUTION_RUNS`, `/`, `/runs`, and interactive `L×I` controls + Risk Heatmap on `/rules`.
2. **Scope 2 — Step-0 Schema Contract Gate & Cross-Hop Pipeline Reconciliation:**
   - Snapshots `INFORMATION_SCHEMA.COLUMNS` into `BASELINE_SCHEMA_JSON` on dataset onboarding; added `POST /api/v1/domains/{domain_id}/rebaseline` (`APPROVER+`).
   - `POST /api/v1/execution/run-gate` runs Step-0 Schema Drift Check *before* SQL rules and blocks dropped columns or precision/scale narrowing (`NUMBER(18,4)` → `NUMBER(18,0)`) as `P1_BLOCKER` (`SCHEMA_CONTRACT_DRIFT`).
   - Added `UPSTREAM_SOURCE_TABLE` input on `/onboard` for `PIPELINE_RECONCILIATION` rules and Step-0 status + **Re-Baseline** action on `/runs`.
3. **Scope 3 — Transformation SQL & PR-Diff Aware AI Discovery:**
   - Added `TRANSFORMATION_SQL` on `RBT_DOMAIN_REGISTRY` and `pr_sql_diff` on `POST /api/v1/domains/{domain_id}/ai-discover`.
   - `sqlglot` lineage extraction (`extract_sql_lineage_summary`) parses tables, joins, `WHERE` filters, aggregations, and `CASE` branches into Cortex AI prompts; added collapsible SQL/PR-diff inputs on `/onboard` and **Re-Discover Risks** modal on `/rules`.
4. **Scope 4 — Multi-Environment (`DEV` | `UAT` | `PROD`) Overrides & `REGRESSION_DIFF`:**
   - Added `ENVIRONMENT` and `TARGET_SCHEMA_OVERRIDE` (sanitized via `sqlglot`) to `run-gate` and `dry-run`, routing `__TARGET_DATASET__` to the override schema while keeping `__BASELINE_DATASET__` bound to canonical `PROD`.
   - Added Environment selector + Target Schema Override fields on `/rules` (Dry-Run) and `/runs` (Trigger Gate modal).

### B. Enhanced ADMIN Module & Strict RBAC (`ADMIN > APPROVER > AUTHOR`)
- **Backend (`teams.py`, `snowflake_client.py`):**
  - `add_team_member` now upserts by `(TEAM_ID, LOWER(USER_EMAIL))` without creating duplicates.
  - Added `DELETE /api/v1/teams/{team_id}/members/{member_id}` (`ADMIN` only).
  - Confirmed `ADMIN` inherits `APPROVER` + `AUTHOR` privileges; `APPROVER` inherits `AUTHOR` privileges and is blocked (`403`) from creating workspaces or mapping user roles.
- **Frontend (`AppShell.tsx`, `TeamSettingsModal.tsx`):**
  - Hid **`+ New Team`** button and **New Team Workspace** / **User & Role Mapping** tabs for `AUTHOR` and `APPROVER`.
  - Added **User & Role Mapping** tab (`ADMIN` only) to map user emails to `AUTHOR` / `APPROVER` / `ADMIN`, update roles inline, or remove mappings.

### C. Bug Fixes, Branding & Documentation
- Fixed missing `HTTPException` import in `backend/app/db/snowflake_client.py` (`404` on missing/inaccessible table during onboarding).
- Fixed 1/2/3-part `DB.SCHEMA` normalization in `_fetch_table_schema_snapshot` for live `detect_schema_drift`.
- Added `triggerErr` error banner in the Trigger Pre-Flight Gate modal (`frontend/src/app/runs/page.tsx`).
- Updated top-bar logo to **Intel** logo (`frontend/public/intel-logo.svg`, `AppShell.tsx`).
- Rewrote `/help` (`frontend/src/app/help/page.tsx`) as a self-service PDH v2.1 guide with screenshots, roles, glossary, and FAQ.
- Updated `README.md` and `architecture.md` for v2.1, RBAC, and `/help`.

### D. Latest Verification
- `backend\.venv\Scripts\python.exe -m pytest backend\tests -v` → **`38 passed`** (`28` v2.0 + `10` new v2.1 & RBAC tests in `backend/tests/test_v21_upgrade.py`).
- `cd frontend; npx tsc --noEmit --incremental false` → exited **`0`**.
- `cd frontend; npm run build` → exited **`0`** (Note: on Windows + OneDrive, if `next build` hits `EINVAL: readlink` on `.next/`, delete `.next` first with `Remove-Item -Recurse -Force .next`).

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
backend\.venv\Scripts\python.exe -m pytest backend\tests -v
cd frontend; npx tsc --noEmit --incremental false; npm run build
```

---

## 4. Suggested Next Items
- Commit and push all v2.1 + ADMIN/RBAC + docs changes to GitHub (`git status`, `git add`, `git commit`, `git push`).
- Collect live UAT feedback from Data Hub teams (`IP` & `IF` Supply, Demand, Material Planning).
- Add a dynamic **Target Table / View** dropdown/autocomplete under the selected Target Schema on `/onboard`.
- Disable `ALLOW_DEMO_AUTH_HEADERS` and wire corporate OIDC/JWT validation for shared/production deployment.
- Integrate Airflow / Control-M / CI-CD pipelines using team `X-API-Key` against `/api/v1/execution/run-gate`.
