# 🏗️ System Architecture — Enterprise AI RBT Platform (v2.1)

> **Zero-Data-Egress Control Plane** for AI-assisted Risk-Based Testing on **Snowflake** + **Snowflake Cortex AI**.  
> Back to [README.md](./README.md) · Live UI: [http://localhost:3000](http://localhost:3000) · API Docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🧭 Table of Contents
1. [High-Level Architecture](#-1-high-level-architecture)
2. [Technology Stack](#-2-technology-stack)
3. [External & Internal Integrations](#-3-external--internal-integrations)
4. [Working Model & Governance](#-4-working-model--governance)
5. [End-to-End Data Flow](#-5-end-to-end-data-flow)
6. [Metadata Data Model (ER Diagram)](#-6-metadata-data-model-er-diagram)

---

## 🌐 1. High-Level Architecture

The platform separates **UI/Control Plane** (Next.js + FastAPI) from **Data & AI Compute** (Snowflake Warehouse + Cortex). Raw business data never leaves Snowflake; only metadata, SQL definitions, aggregate counts, and up to 5 sample failing keys are returned to the control plane.

```mermaid
flowchart TD
    User["User Browser (SSO / UI)"] -->|"HTTP :3000"| NextJS["Next.js 14 Frontend (App Router + Tailwind)"]
    Orch["Airflow / Control-M / CI-CD"] -->|"HTTPS + X-API-Key"| FastAPI["FastAPI Control Plane (:8000)"]
    NextJS -->|"REST JSON + Auth Headers"| FastAPI

    subgraph ControlPlane["Backend Control Plane (Python 3.11)"]
        FastAPI --> Auth["RBAC & Auth Guard (SSO JWT / X-API-Key / Demo Headers)"]
        Auth --> Guard["SQL AST Guardrail (sqlglot Read-Only + Depth Limiter)"]
        Guard --> SFClient["Dual-Mode Snowflake Client (Live / SQLite Mock)"]
    end

    subgraph Snowflake["Snowflake Cloud (XD14286-IFCDWm4)"]
        SFClient -->|"externalbrowser SSO / Key-Pair"| Meta["Metadata Schema (IF_SUPPLY_CHAIN.PDH_SUPPLY_PLANNING_SRC_BASE)"]
        SFClient -->|"INFORMATION_SCHEMA + Stats"| Data["Target Supply Chain Tables & Views"]
        SFClient -->|"SNOWFLAKE.CORTEX.COMPLETE"| Cortex["Cortex AI Models (claude-sonnet-4-5, llama3.3-70b, ...)"]
        SFClient -->|"Query Execution (IF_WH_SCHAIN_CONSUMPTION)"| Transient["Transient Failure Tables (7-day retention)"]
    end

    FastAPI -.->|"Optional Failure Alerts"| Webhook["Slack / Teams Webhook"]
```

---

## 🧰 2. Technology Stack

| Layer | Technology | Purpose | Key Files |
| :--- | :--- | :--- | :--- |
| **Frontend UI** | **Next.js 14** (App Router), **React 18**, **TypeScript** | Multi-page SPA with client state & routing | [frontend/src/app/layout.tsx](./frontend/src/app/layout.tsx), [AppContext.tsx](./frontend/src/components/AppContext.tsx) |
| **Styling & Icons** | **Tailwind CSS**, **Lucide React** | Full-width responsive layout, Light/Dark mode | [AppShell.tsx](./frontend/src/components/AppShell.tsx) |
| **SQL Studio** | **CodeMirror 6** (`@uiw/react-codemirror`, `@codemirror/lang-sql`) | Interactive SQL rule editing & syntax highlighting | [SqlEditor.tsx](./frontend/src/components/SqlEditor.tsx) |
| **Backend API** | **FastAPI**, **Pydantic v2**, **Uvicorn** | REST APIs, OpenAPI Swagger docs, async gate execution | [backend/app/main.py](./backend/app/main.py), [models.py](./backend/app/schemas/models.py) |
| **SQL Security** | **sqlglot** AST Parser | Blocks DML/DDL, enforces single-statement `SELECT`, bounds recursion, extracts transformation/PR-diff lineage, sanitizes `target_schema_override` & binds `__TARGET_DATASET__` / `__BASELINE_DATASET__` | [sql_validator.py](./backend/app/security/sql_validator.py) |
| **Snowflake Connector** | **snowflake-connector-python** | Live SSO (`externalbrowser`), key-pair auth, Cortex & query execution | [snowflake_client.py](./backend/app/db/snowflake_client.py) |
| **Local Mock Engine** | **SQLite** (in-memory/local) | Zero-dependency local testing when `SNOWFLAKE_MOCK_MODE=true` | [snowflake_client.py](./backend/app/db/snowflake_client.py) |
| **Testing** | **pytest**, **httpx**, **TypeScript (`tsc`)** | 38 automated API/RBAC/AST/v2.1 tests + strict typechecking | [backend/tests](./backend/tests) |

---

## 🔗 3. External & Internal Integrations

<details open>
<summary><strong>❄️ Snowflake SSO & Compute Integration</strong></summary>

- **Authentication:** `externalbrowser` SSO triggered from the **`Snowflake SSO`** button in the UI (`GET /api/v1/auth/sso-status`, `POST /api/v1/auth/sso-start`) or PKCS#8 private-key auth for headless deployments.
- **Session Reuse:** The backend holds a thread-safe (`RLock`) SSO connection proxy (`_NoCloseConnProxy`) so users authenticate once per server session.
- **Metadata & Execution Isolation:** Team metadata resides in `IF_SUPPLY_CHAIN.PDH_SUPPLY_PLANNING_SRC_BASE`, while queries run on each team's configured warehouse (e.g., `IF_WH_SCHAIN_CONSUMPTION`).

</details>

<details open>
<summary><strong>🧠 Snowflake Cortex AI Integration</strong></summary>

- **Dynamic Model Selection:** `GET /api/v1/cortex/models` exposes verified models on the account (`claude-sonnet-4-5`, `llama3.3-70b`, `llama3.1-70b`, `mistral-large2`).
- **Structured Outputs:** `SNOWFLAKE.CORTEX.COMPLETE(model, messages, response_format)` generates schema-validated JSON rules (`CORTEX_RULE_LIST_SCHEMA`), auto-debug SQL fixes, marketplace column remapping (`CORTEX_REMAP_SCHEMA`), and plain-English failure triage summaries.

</details>

<details open>
<summary><strong>⚙️ Orchestrator & Webhook Integrations</strong></summary>

- **Airflow / Control-M / CI-CD:** Call `POST /api/v1/execution/run-gate` (`SYNC` or `ASYNC`) and `GET /api/v1/runs/{run_id}` using the team's `X-API-Key` header (stored as a SHA-256 hash in `RBT_TEAMS`).
- **Alert Webhooks:** Teams can configure an `alert_webhook_url` in Team Settings to receive failure notifications when a quality gate blocks downstream execution.

</details>

---

## 🛡️ 4. Working Model & Governance

### Role-Based Access Control (RBAC)
Enforced by [dependencies.py](./backend/app/auth/dependencies.py):

| Role | Level | Permissions |
| :--- | :---: | :--- |
| **`AUTHOR`** | 1 | View teams/schemas/rules/members, onboard datasets (with optional upstream source & transformation SQL), run AI/PR-diff discovery, edit & dry-run SQL rules (`DEV`/`UAT`/`PROD`), trigger gates |
| **`APPROVER`** | 2 | Inherits all `AUTHOR` privileges + approve/archive rules, share templates, **Re-Baseline Schema** (`POST /api/v1/domains/{id}/rebaseline`), execute **Break-Glass Override** on failed gates (cannot create workspaces or map user roles) |
| **`ADMIN`** | 3 | Full privilege: inherits all `APPROVER` + `AUTHOR` privileges + create/update team workspaces (`+ New Team`), map/update/remove user roles (`POST`/`DELETE /api/v1/teams/{id}/members`), rotate `X-API-Key` |

### Quantitative Risk Scoring & Rule Lifecycle
```
AI Generated / Manual Draft ──▶ [ DRAFT ] ──(APPROVER)──▶ [ APPROVED ] ──▶ [ ARCHIVED ]
                                    │                           ▲
                              (Edit SQL)                        │
                                    └──▶ New Version Saved ─────┘
```
- **Quantitative Risk Matrix (`Likelihood × Impact`):** Each rule carries `LIKELIHOOD_SCORE` (1–5) and `IMPACT_SCORE` (1–5), computing `RISK_SCORE` (1–25). Unless manually overridden, `RISK_SCORE` auto-maps to priority: `16–25 → P1_BLOCKER`, `8–15 → P2_WARNING`, `1–7 → P3_INFO`.
- **Versioning:** Every edit to a rule's SQL inserts an immutable snapshot into `RBT_TEST_RULE_HISTORY` and increments `VERSION_NUM`.
- **Execution Semantics:** Every rule SQL query is written so **`0 rows returned = PASS`** and **`>= 1 row returned = FAIL`**.
- **Step-0 Schema Contract Gate:** Before running SQL rules, `run-gate` compares live `INFORMATION_SCHEMA.COLUMNS` against `BASELINE_SCHEMA_JSON`. Dropped columns or precision/scale narrowing (e.g., `NUMBER(18,4)` → `NUMBER(18,0)`) trigger an automatic `P1_BLOCKER` (`SCHEMA_CONTRACT_DRIFT`).
- **Multi-Environment Routing & Regression Diffs:** `run-gate` and `dry-run` accept `environment` (`DEV` | `UAT` | `PROD`) and `target_schema_override` (sanitized via `sqlglot`), routing `__TARGET_DATASET__` to the candidate schema while keeping `__BASELINE_DATASET__` bound to canonical `PROD` for `REGRESSION_DIFF` checks.
- **Gate Decision & Release Confidence Scorecard:**
  - Any `APPROVED` **P1 (`P1_BLOCKER`)** or **Step-0 Schema Drift** failure → `BLOCKED_DOWNSTREAM` (`allow_downstream = false`, `HTTP 422` in `SYNC` mode).
  - Only **P2 (`P2_WARNING`)** failures → `PASS_WITH_WARNINGS` (`allow_downstream = true`).
  - Manual `APPROVER` override → `OVERRIDDEN_PASS`.
  - Every run computes `COVERAGE_PCT` (critical column test coverage %) and `RELEASE_CONFIDENCE_SCORE` (`0–100`).

---

## 🔄 5. End-to-End Data Flow

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Next.js UI
    participant API as FastAPI Backend
    participant Guard as sqlglot Guard
    participant SF as Snowflake + Cortex AI

    Note over User,SF: Phase 1 — SSO & AI Dataset Onboarding (v2.1 Grounded)
    User->>UI: Click "Snowflake SSO"
    UI->>API: POST /api/v1/auth/sso-start
    API->>SF: Open externalbrowser SSO & cache connection
    User->>UI: Open /onboard, pick Schema/Table (+ optional Upstream & Transform SQL/PR Diff)
    UI->>API: POST /api/v1/domains/{id}/ai-discover
    API->>SF: Query INFORMATION_SCHEMA.COLUMNS, PK/FK, COUNT/MIN/MAX & snapshot BASELINE_SCHEMA_JSON
    API->>Guard: Parse Transformation SQL / PR Diff lineage (tables, joins, filters, CASE)
    API->>SF: SNOWFLAKE.CORTEX.COMPLETE(model, grounding + lineage + schema)
    SF-->>API: Structured JSON (rules + likelihood_score & impact_score)
    API->>Guard: Validate AST (read-only SELECT, recursion depth)
    API->>SF: Compile-check SQL & save rules to RBT_TEST_RULES

    Note over User,SF: Phase 2 — Step-0 Schema Contract & Quality Gate Execution
    User->>UI: Trigger Gate Run (PROD/UAT/DEV + optional target_schema_override)
    UI->>API: POST /api/v1/execution/run-gate
    API->>SF: Step-0 Compare live schema vs BASELINE_SCHEMA_JSON
    API->>Guard: Wrap __TARGET_DATASET__ (override) & __BASELINE_DATASET__ (PROD) CTEs
    API->>SF: Execute APPROVED rules on team warehouse
    SF-->>API: Failed row counts + up to 5 sample keys
    API->>SF: Compute Coverage Pct and Release Confidence Score, persist RBT_EXECUTION_RUNS
    API-->>UI: Gate Decision (PASS / BLOCKED_DOWNSTREAM) + Scorecard + AI Triage Summary
```

---

## 🗄️ 6. Metadata Data Model (ER Diagram)

All 7 metadata tables are defined in [01_init_ddl.sql](./backend/app/db/migrations/01_init_ddl.sql) (with additive v2.1 columns in [02_rbt_v21_upgrade.sql](./backend/app/db/migrations/02_rbt_v21_upgrade.sql)) and hosted in `IF_SUPPLY_CHAIN.PDH_SUPPLY_PLANNING_SRC_BASE`:

```mermaid
erDiagram
    RBT_TEAMS ||--o{ RBT_TEAM_MEMBERS : "has members"
    RBT_TEAMS ||--o{ RBT_DOMAIN_REGISTRY : "owns datasets"
    RBT_TEAMS ||--o{ RBT_TEST_RULES : "owns rules"
    RBT_DOMAIN_REGISTRY ||--o{ RBT_TEST_RULES : "tested by"
    RBT_TEST_RULES ||--o{ RBT_TEST_RULE_HISTORY : "versions"
    RBT_DOMAIN_REGISTRY ||--o{ RBT_EXECUTION_RUNS : "executes"
    RBT_EXECUTION_RUNS ||--o{ RBT_EXECUTION_DETAILS : "contains"
    RBT_TEST_RULES ||--o{ RBT_EXECUTION_DETAILS : "evaluated in"

    RBT_TEAMS {
        string TEAM_ID PK
        string TEAM_NAME
        string SNOWFLAKE_DATABASE
        string SNOWFLAKE_WAREHOUSE
        string ORCHESTRATOR_API_KEY_HASH
    }
    RBT_DOMAIN_REGISTRY {
        string DOMAIN_ID PK
        string TEAM_ID FK
        string DOMAIN_NAME
        string TARGET_SCHEMA
        string TARGET_TABLE
        string CRITICALITY
        variant BASELINE_SCHEMA_JSON
        string UPSTREAM_SOURCE_TABLE
        string TRANSFORMATION_SQL
    }
    RBT_TEST_RULES {
        string RULE_ID PK
        string TEAM_ID FK
        string DOMAIN_ID FK
        string RISK_NAME
        string RISK_CATEGORY
        string RISK_PRIORITY
        int LIKELIHOOD_SCORE
        int IMPACT_SCORE
        int RISK_SCORE
        string STATUS
    }
    RBT_EXECUTION_RUNS {
        string RUN_ID PK
        string TEAM_ID FK
        string DOMAIN_ID FK
        string EXECUTION_STATUS
        string GATE_DECISION
        string ENVIRONMENT
        string TARGET_SCHEMA_OVERRIDE
        float COVERAGE_PCT
        float RELEASE_CONFIDENCE_SCORE
        string SCHEMA_DRIFT_STATUS
    }
```
