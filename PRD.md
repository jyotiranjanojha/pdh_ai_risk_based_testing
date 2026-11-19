# Product Requirements Document (PRD) — Enterprise AI Risk-Based Testing (RBT) Platform (PDH Production Ready)

**Project Name:** Enterprise AI Risk-Based Testing (RBT) Platform  
**Version:** 2.0 (PDH Optimized & Production Hardened for Antigravity Build)

---

## 1. Executive Summary & Vision
Build a multi-tenant, external web application that enables any enterprise data engineering or data hub team (e.g., Planning Data Hub, Finance, Customer 360, Logistics, Commercial Analytics) to define, generate, approve, and execute Risk-Based Testing (RBT) rules before data is published to downstream consumers (ERPs, Planning Engines/Solvers, APIs, ML Pipelines, or BI Marts).

* **Core Architectural Principle (Zero Data Egress):** The web application serves purely as the control plane and UI, while Snowflake serves as the metadata store, SQL execution engine, and AI engine (via Snowflake Cortex AI).
* **100% Generic & Metadata-Driven:** No domain schemas, table names, or business rules are hardcoded. Teams dynamically onboard any Snowflake table or view, provide plain-English business context, and let Cortex AI generate domain-aware risk validations.
* **Native Grounding on Snowflake:** Since the Planning Data Hub (PDH) is built natively on Snowflake, the AI engine is dynamically grounded using Snowflake's own metadata catalogs. Rather than exporting underlying data, the platform queries metadata (`INFORMATION_SCHEMA.COLUMNS`, data types, physical primary/foreign key constraints, table/column `COMMENT` descriptions, and non-PII statistical boundaries) in real time, injecting this structural and semantic context directly into the Cortex AI prompt.

---

## 2. Target Tech Stack & Repository Structure
Scaffold a clean monorepo with the following stack:
* **Frontend:** React (Next.js 14+ App Router), TypeScript, Tailwind CSS, Lucide Icons, CodeMirror/Monaco SQL Editor.
* **Backend:** Python 3.11+, FastAPI, Pydantic v2, `sqlglot` (Snowflake SQL AST validation), `snowflake-connector-python` (supporting Key-Pair, Password/SSO, and async query execution), `uvicorn`.
* **Security & Auth:** OIDC/SSO JWT header parsing (`X-User-Email`, `X-User-Role`) for UI routes; `X-API-Key` header authentication (SHA-256 hashed per team) for external orchestrator webhooks.
* **Database & AI Engine:** Snowflake (`RBT_GLOBAL_PLATFORM` database) + `SNOWFLAKE.CORTEX.COMPLETE` (utilizing Structured Outputs / JSON Schema).
* **Deployment:** Docker & `docker-compose.yml` for one-command local and cloud deployment.

```text
enterprise-rbt-platform/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI entrypoint, middleware & CORS
│   │   ├── config.py                # Env vars, timeouts & Snowflake connection settings
│   │   ├── auth/
│   │   │   └── dependencies.py      # SSO/OIDC JWT RBAC & Orchestrator X-API-Key guards
│   │   ├── db/
│   │   │   ├── snowflake_client.py  # Pooled Snowflake connection, async polling & Cortex wrapper
│   │   │   └── migrations/
│   │   │       └── 01_init_ddl.sql  # Multi-tenant Snowflake DDL (PDH Production Ready)
│   │   ├── security/
│   │   │   └── sql_validator.py     # sqlglot AST read-only validator & runtime CTE wrapper
│   │   ├── api/
│   │   │   ├── teams.py             # Tenant/team management & API key rotation endpoints
│   │   │   ├── domains.py           # Dataset onboarding & 3-step native schema grounding
│   │   │   ├── ai_generator.py      # Cortex AI Structured Output generation & auto-debug loop
│   │   │   ├── rules.py             # Rule CRUD, dry-run, CSV export & RBAC approval workflow
│   │   │   └── execution.py         # Async/sync pre-flight gate, break-glass override & webhooks
│   │   └── schemas/
│   │       └── models.py            # Strict Pydantic v2 & Cortex JSON schemas
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx             # Executive Dashboard & Gate Health
│   │   │   ├── onboard/page.tsx     # AI Dataset Onboarding Wizard
│   │   │   ├── rules/page.tsx       # Risk Matrix, SQL Editor & Approver Queue
│   │   │   ├── marketplace/page.tsx # Cross-Team Shared Rule Templates
│   │   │   └── runs/page.tsx        # Execution Logs, Break-Glass Override & Cortex Triage
│   │   ├── components/              # Reusable UI components (TeamSwitcher, SqlEditor, RoleBadge)
│   │   └── lib/api.ts               # Typed API client
│   ├── package.json
│   └── Dockerfile
└── docker-compose.yml
```

---

## 3. Snowflake Backend Data Model (`01_init_ddl.sql`)

```sql
CREATE DATABASE IF NOT EXISTS RBT_GLOBAL_PLATFORM;
CREATE SCHEMA IF NOT EXISTS RBT_GLOBAL_PLATFORM.CORE;
CREATE SCHEMA IF NOT EXISTS RBT_GLOBAL_PLATFORM.FAILURES_TRANSIENT DATA_RETENTION_TIME_IN_DAYS = 7;

-- 1. Multi-Tenant Team Registry (Isolated Compute & Orchestrator Auth)
CREATE TABLE IF NOT EXISTS RBT_GLOBAL_PLATFORM.CORE.RBT_TEAMS (
    TEAM_ID STRING PRIMARY KEY,
    TEAM_NAME STRING NOT NULL,
    SNOWFLAKE_DATABASE STRING NOT NULL,
    SNOWFLAKE_WAREHOUSE STRING NOT NULL,     -- Isolated compute warehouse per tenant (e.g., 'PDH_RBT_WH')
    DEFAULT_DOWNSTREAM_TARGET STRING,        -- Free-text tag (e.g., 'ERP_CORE', 'PLANNING_SOLVER', 'FINANCE_MART')
    ORCHESTRATOR_API_KEY_HASH STRING,        -- SHA-256 hash for X-API-Key service-to-service gate triggers
    ALERT_WEBHOOK_URL STRING,
    CREATED_TS TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- 2. Multi-Tenant User Role Mapping (RBAC)
CREATE TABLE IF NOT EXISTS RBT_GLOBAL_PLATFORM.CORE.RBT_TEAM_MEMBERS (
    MEMBER_ID STRING PRIMARY KEY,
    TEAM_ID STRING REFERENCES RBT_GLOBAL_PLATFORM.CORE.RBT_TEAMS(TEAM_ID),
    USER_EMAIL STRING NOT NULL,
    USER_ROLE STRING NOT NULL,               -- 'AUTHOR' | 'APPROVER' | 'ADMIN'
    CREATED_TS TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- 3. Onboarded Datasets / Data Areas per Team (PDH Temporal & Hierarchy Aware)
CREATE TABLE IF NOT EXISTS RBT_GLOBAL_PLATFORM.CORE.RBT_DOMAIN_REGISTRY (
    DOMAIN_ID STRING PRIMARY KEY,
    TEAM_ID STRING REFERENCES RBT_GLOBAL_PLATFORM.CORE.RBT_TEAMS(TEAM_ID),
    DOMAIN_NAME STRING NOT NULL,             -- Friendly name (e.g., 'Intersite BOM', 'Demand Netting', 'GL Journal')
    TARGET_SCHEMA STRING NOT NULL,
    TARGET_TABLE STRING NOT NULL,
    RELATED_TABLES VARIANT,                  -- JSON array of parent/reference tables for FK checks
    TEMPORAL_CYCLE_COLUMN STRING,            -- Optional column for cycle filtering (e.g., 'PLANNING_CYCLE_ID')
    SNAPSHOT_TS_COLUMN STRING,               -- Optional column for snapshot filtering (e.g., 'SNAPSHOT_TS')
    MAX_HIERARCHY_DEPTH INT DEFAULT 10,      -- Guardrail to prevent infinite recursion in BOM hierarchy checks
    DOWNSTREAM_CONSUMER STRING,              -- Specific downstream system consuming this dataset
    BUSINESS_CONTEXT STRING,                 -- Plain-English description of business rules & failure impacts
    CRITICALITY STRING DEFAULT 'HIGH',       -- 'CRITICAL', 'HIGH', 'MEDIUM'
    IS_ACTIVE BOOLEAN DEFAULT TRUE,
    CREATED_TS TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- 4. Risk-Based Test Rules (AI-Generated & Human-Approved)
CREATE TABLE IF NOT EXISTS RBT_GLOBAL_PLATFORM.CORE.RBT_TEST_RULES (
    RULE_ID STRING PRIMARY KEY,
    TEAM_ID STRING REFERENCES RBT_GLOBAL_PLATFORM.CORE.RBT_TEAMS(TEAM_ID),
    DOMAIN_ID STRING REFERENCES RBT_GLOBAL_PLATFORM.CORE.RBT_DOMAIN_REGISTRY(DOMAIN_ID),
    RISK_NAME STRING NOT NULL,
    RISK_CATEGORY STRING,                    -- 'REFERENTIAL_INTEGRITY', 'HIERARCHY_LOOP', 'BOUNDARY_VALUE', 'DUPLICATE_KEY', 'FRESHNESS_ANOMALY', 'FROZEN_HORIZON_VIOLATION'
    RISK_PRIORITY STRING NOT NULL,           -- 'P1_BLOCKER', 'P2_WARNING', 'P3_INFO'
    BUSINESS_IMPACT_DESC STRING,
    VALIDATION_SQL STRING NOT NULL,          -- Must return failing rows (0 rows = PASS); supports :planning_cycle_id
    STATUS STRING DEFAULT 'DRAFT',           -- 'DRAFT', 'DRAFT_NEEDS_REVIEW', 'APPROVED', 'ARCHIVED'
    LAST_COMPILE_ERROR STRING,               -- Populated if auto-debug loop exhausts MAX_RETRIES = 3
    IS_SHARED_TEMPLATE BOOLEAN DEFAULT FALSE,
    CREATED_BY STRING,
    APPROVED_BY STRING,
    UPDATED_TS TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- 5. Audit Trail & Rule History Versioning Table
CREATE TABLE IF NOT EXISTS RBT_GLOBAL_PLATFORM.CORE.RBT_TEST_RULE_HISTORY (
    HISTORY_ID STRING PRIMARY KEY,
    RULE_ID STRING REFERENCES RBT_GLOBAL_PLATFORM.CORE.RBT_TEST_RULES(RULE_ID),
    VERSION_NUM INT NOT NULL,
    STATUS_OLD STRING,
    STATUS_NEW STRING,
    VALIDATION_SQL_OLD STRING,
    VALIDATION_SQL_NEW STRING,
    MODIFIED_BY STRING NOT NULL,
    MODIFIED_TS TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- 6. Execution Runs, Async Status, Break-Glass Override & AI Triage Log
CREATE TABLE IF NOT EXISTS RBT_GLOBAL_PLATFORM.CORE.RBT_EXECUTION_RUNS (
    RUN_ID STRING PRIMARY KEY,
    TEAM_ID STRING REFERENCES RBT_GLOBAL_PLATFORM.CORE.RBT_TEAMS(TEAM_ID),
    DOMAIN_ID STRING REFERENCES RBT_GLOBAL_PLATFORM.CORE.RBT_DOMAIN_REGISTRY(DOMAIN_ID),
    TRIGGERED_BY STRING,                     -- 'ORCHESTRATOR_API', 'CI_CD', 'MANUAL_UI'
    EXECUTION_STATUS STRING DEFAULT 'RUNNING', -- 'RUNNING', 'COMPLETED', 'FAILED', 'TIMED_OUT'
    PLANNING_CYCLE_ID STRING,                -- Runtime cycle filter (e.g., 'WEEK_42_POR')
    SNAPSHOT_TS TIMESTAMP_NTZ,               -- Runtime snapshot filter
    GATE_DECISION STRING,                    -- 'PENDING', 'PASS', 'BLOCKED_DOWNSTREAM', 'PASS_WITH_WARNINGS', 'OVERRIDDEN_PASS'
    TOTAL_RULES_RUN INT DEFAULT 0,
    P1_FAILURES INT DEFAULT 0,
    P2_FAILURES INT DEFAULT 0,
    AI_TRIAGE_SUMMARY STRING,
    OVERRIDE_BY STRING,                      -- Populated only on emergency Break-Glass override by APPROVER/ADMIN
    OVERRIDE_REASON STRING,                  -- Mandatory justification for audit compliance
    OVERRIDE_TS TIMESTAMP_NTZ,
    RUN_TS TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    COMPLETED_TS TIMESTAMP_NTZ
);

-- 7. Per-Rule Execution Details & Transient Failure Table Reference
CREATE TABLE IF NOT EXISTS RBT_GLOBAL_PLATFORM.CORE.RBT_EXECUTION_DETAILS (
    DETAIL_ID STRING PRIMARY KEY,
    RUN_ID STRING REFERENCES RBT_GLOBAL_PLATFORM.CORE.RBT_EXECUTION_RUNS(RUN_ID),
    RULE_ID STRING REFERENCES RBT_GLOBAL_PLATFORM.CORE.RBT_TEST_RULES(RULE_ID),
    RISK_NAME STRING,
    RISK_PRIORITY STRING,
    RULE_RUN_STATUS STRING,                  -- 'PASS', 'FAIL', 'ERROR', 'TIMEOUT'
    FAILED_ROW_COUNT INT,
    SAMPLE_FAILING_KEYS VARIANT,             -- Top 5 failing rows as JSON
    FAILURE_TABLE_REF STRING,                -- Optional transient table (e.g., 'RBT_GLOBAL_PLATFORM.FAILURES_TRANSIENT.FAIL_{DETAIL_ID}')
    ERROR_MESSAGE STRING,
    EXECUTION_MS INT
);
```

---

## 4. Functional Requirements & User Workflows

### Module 1: Multi-Tenant Workspace, Team Switcher & RBAC
* **FR-1.1:** Users select their active team from a persistent top navigation switcher, or create a new team workspace (requires `ADMIN` role).
* **FR-1.2:** Every API query filters strictly by `team_id` to isolate schemas, compute warehouses, rules, and execution logs across teams.
* **FR-1.3 (Team Settings & API Keys):** Configure the team's `SNOWFLAKE_DATABASE`, `SNOWFLAKE_WAREHOUSE`, default downstream target, Slack/Teams webhook URL, and generate/rotate the `X-API-Key` for external orchestrators.
* **FR-1.4 (Enterprise Authentication & RBAC):**
  * **UI/Interactive Endpoints:** Parse SSO/OIDC JWT headers (`Authorization: Bearer <token>`, or mock headers `X-Mock-User-Email` and `X-Mock-User-Role` when `SNOWFLAKE_MOCK_MODE=true`) mapped against `RBT_TEAM_MEMBERS` into three roles:
    * `AUTHOR`: Onboard domains, trigger AI discovery, edit `DRAFT` SQL, and run dry-runs.
    * `APPROVER`: All `AUTHOR` permissions + promote rules to `APPROVED`, archive rules, and execute emergency Break-Glass gate overrides.
    * `ADMIN`: All `APPROVER` permissions + manage team settings, warehouses, members, and orchestrator API keys.
  * **Service-to-Service Endpoints (`POST /api/v1/execution/run-gate` & `GET /api/v1/runs/{run_id}`):** Accept either a valid SSO JWT or an `X-API-Key` header verified against `RBT_TEAMS.ORCHESTRATOR_API_KEY_HASH`.

### Module 2: Dynamic Dataset Onboarding & Native Snowflake AI Grounding
* **FR-2.1:** Users register any dataset by entering `domain_name`, `target_schema`, `target_table`, `temporal_cycle_column` (optional), `snapshot_ts_column` (optional), `max_hierarchy_depth` (default `10`), `downstream_consumer`, `criticality`, `related_tables` (optional), and `business_context`.
* **FR-2.2 (3-Step Native Catalog Grounding):** On clicking "Discover Risks with Cortex AI", the backend extracts Snowflake's catalog context:
  1. `INFORMATION_SCHEMA.COLUMNS`: Column names, data types, nullability, and `COMMENT` descriptions for target and related tables.
  2. Physical Constraints: `SHOW PRIMARY KEYS`, `SHOW FOREIGN KEYS`, and `SHOW UNIQUE KEYS` metadata.
  3. Non-PII Statistical Profiles: Fast aggregate query inside Snowflake (`COUNT(*)`, `MIN`/`MAX` on numeric/date columns, `NULL` ratios) without exporting raw cell values.
* **FR-2.3 (Structured Output Prompting & Dynamic Cycle Contract):** The backend invokes `SNOWFLAKE.CORTEX.COMPLETE('claude-3-5-sonnet', ..., {'response_format': {'type': 'json', 'schema': CORTEX_RULE_LIST_SCHEMA}})` to guarantee deterministic JSON output (never markdown-wrapped strings) generating 5–8 risk rules covering:
  * Duplicate primary/natural keys.
  * Orphan foreign keys against related parent tables.
  * Recursive/circular hierarchy loops (`WITH RECURSIVE`) strictly bounded by `WHERE depth <= <MAX_HIERARCHY_DEPTH>`.
  * Frozen Horizon integrity violations and supply/demand dampening boundary violations.
  * **Dynamic Temporal Cycle Contract:** All generated `VALIDATION_SQL` queries MUST reference the target table via an alias/CTE name `__TARGET_DATASET__` (or include explicit `(:planning_cycle_id IS NULL OR <TEMPORAL_CYCLE_COLUMN> = :planning_cycle_id)` predicates). At runtime (dry-run or gate execution), the backend dynamically wraps `__TARGET_DATASET__` as a filtered CTE scoping the table to Snowflake bind parameters `:planning_cycle_id` and `:snapshot_ts` whenever provided, preventing costly full-table historical scans.
* **FR-2.4 (Bounded Auto-Debugging Loop):** Before storing generated rules, the backend runs `EXPLAIN USING TEXT` (or `LIMIT 1` dry-run) inside Snowflake. If compilation fails, the backend feeds the exact Snowflake error back to Cortex AI to self-correct the SQL, strictly capped at **`MAX_RETRIES = 3`**. Validated rules save as `STATUS = 'DRAFT'`; any rule still failing after 3 retries saves as `STATUS = 'DRAFT_NEEDS_REVIEW'` with `LAST_COMPILE_ERROR` populated so Cortex credits are never burned in an infinite loop.

### Module 3: Risk Matrix, Dry-Run, Failure Export & QA Approval Workflow
* **FR-3.1:** Display rules filterable by Dataset, Risk Category, Status (`DRAFT`, `DRAFT_NEEDS_REVIEW`, `APPROVED`, `ARCHIVED`), and Risk Priority (`P1_BLOCKER`, `P2_WARNING`, `P3_INFO`).
* **FR-3.2:** Provide an inline Monaco/CodeMirror SQL Editor allowing `AUTHOR`/`APPROVER` users to refine `VALIDATION_SQL` and execute an interactive "Dry Run" (with optional `planning_cycle_id` test input). Only `APPROVER` or `ADMIN` users can promote rules to `APPROVED` (enforcing separation of duties where `APPROVED_BY` is logged). Every SQL modification or status transition writes an immutable audit row to `RBT_TEST_RULE_HISTORY`.
* **FR-3.3:** Only `APPROVED` rules execute during automated downstream pre-flight gates.

### Module 4: Cross-Team Rule Marketplace (Template Cloning)
* **FR-4.1:** Teams can publish any `APPROVED` rule to the internal marketplace by toggling `IS_SHARED_TEMPLATE = TRUE`.
* **FR-4.2:** Any other team can browse shared templates, filter by risk category, and click "Clone to My Team".
* **FR-4.3:** During cloning, Cortex AI (using Structured Outputs) inspects the target dataset's `INFORMATION_SCHEMA.COLUMNS` and remaps SQL column/table references, followed by the `MAX_RETRIES = 3` compile check before saving as `DRAFT`.

### Module 5: Automated Pre-Flight Gate, Async Polling, Break-Glass & AI Triage
* **FR-5.1 (Sync & Async Gate Trigger):** Expose `POST /api/v1/execution/run-gate` accepting `execution_mode` (`"SYNC"` default or `"ASYNC"`), `planning_cycle_id`, and `snapshot_ts`.
  * In `"ASYNC"` mode, immediately create `RBT_EXECUTION_RUNS` (`EXECUTION_STATUS = 'RUNNING'`, `GATE_DECISION = 'PENDING'`), dispatch execution in a background task, and return **HTTP 202 Accepted** with `run_id` and `poll_url` (`/api/v1/runs/{run_id}`) so orchestrators polling multi-million-row PDH BOM/netting runs never hit 60–120s HTTP gateway timeouts.
  * In `"SYNC"` mode, await completion (up to configurable `SYNC_HTTP_TIMEOUT_SECONDS = 90`) and return HTTP `200` (`PASS` / `PASS_WITH_WARNINGS`) or HTTP `422` (`BLOCKED_DOWNSTREAM`).
* **FR-5.2 (Concurrent Execution, Timeouts & Transient Failure Capture):**
  * Execute all `APPROVED` rules concurrently (bounded semaphore of 5 concurrent queries on the team's `SNOWFLAKE_WAREHOUSE`) using Python `asyncio` + Snowflake async query execution (`execute_async` + status polling), enforcing `STATEMENT_TIMEOUT_IN_SECONDS = 300` per rule.
  * Record `FAILED_ROW_COUNT`, `EXECUTION_MS`, and top 5 `SAMPLE_FAILING_KEYS`. When `FAILED_ROW_COUNT > 5`, materialize up to 1,000 failing rows into a transient Snowflake table (`RBT_GLOBAL_PLATFORM.FAILURES_TRANSIENT.FAIL_<DETAIL_ID>`, 7-day retention) stored in `FAILURE_TABLE_REF`, and expose `GET /api/v1/runs/{run_id}/details/{detail_id}/export-csv` so PDH planners can download up to 1,000 failing keys directly from the UI.
* **FR-5.3 (Gate Decision Logic):**
  * If any `P1_BLOCKER` rule fails (`FAILED_ROW_COUNT > 0`) or errors/times out (fail-closed safety), set `GATE_DECISION = 'BLOCKED_DOWNSTREAM'` (`allow_downstream: false`, HTTP `422` in sync mode).
  * If `P1_FAILURES == 0` and `P2_FAILURES > 0`, set `GATE_DECISION = 'PASS_WITH_WARNINGS'` (`allow_downstream: true`, HTTP `200`).
  * If all rules pass, set `GATE_DECISION = 'PASS'` (`allow_downstream: true`, HTTP `200`).
* **FR-5.4 (Cortex AI Triage & Webhook Alerting):** Whenever a gate results in `BLOCKED_DOWNSTREAM` or `PASS_WITH_WARNINGS`, invoke `SNOWFLAKE.CORTEX.COMPLETE` to generate a plain-English Root-Cause & Downstream Impact Summary saved in `AI_TRIAGE_SUMMARY` and dispatched to `ALERT_WEBHOOK_URL`.
* **FR-5.5 (Emergency Break-Glass Gate Override):** Expose `POST /api/v1/runs/{run_id}/override` (restricted strictly to `APPROVER` and `ADMIN` roles) allowing business leads during urgent weekly POR cutoffs to transition a `BLOCKED_DOWNSTREAM` run to `GATE_DECISION = 'OVERRIDDEN_PASS'` (`allow_downstream: true`) by providing a mandatory `override_reason` (minimum 20 characters), recording `OVERRIDE_BY`, `OVERRIDE_REASON`, and `OVERRIDE_TS` without disabling the underlying rule.

---

## 5. Core API Specification & Exact JSON Contracts (FastAPI)

### 5.1 Endpoint Summary
| Method | Endpoint | Auth | Description |
|:---|:---|:---|:---|
| `GET / POST` | `/api/v1/teams` | JWT (`ADMIN` on POST) | List or register multi-tenant teams, warehouse & webhook settings |
| `POST` | `/api/v1/teams/{team_id}/api-key` | JWT (`ADMIN`) | Generate/rotate orchestrator `X-API-Key` (returns plaintext once) |
| `GET / POST` | `/api/v1/teams/{team_id}/domains` | JWT (`AUTHOR+`) | List or onboard a dataset with temporal cycle & hierarchy depth settings |
| `POST` | `/api/v1/domains/{domain_id}/ai-discover` | JWT (`AUTHOR+`) | Run 3-step Snowflake grounding, Cortex Structured Output & `MAX_RETRIES=3` check |
| `GET / PATCH` | `/api/v1/rules/{rule_id}` | JWT (`AUTHOR+` edit, `APPROVER+` approve) | Edit SQL, toggle template sharing, or approve rule (logs to `RBT_TEST_RULE_HISTORY`) |
| `POST` | `/api/v1/rules/{rule_id}/dry-run` | JWT (`AUTHOR+`) | Validate via `sqlglot` AST, execute in Snowflake, return count + 5 sample rows |
| `GET / POST` | `/api/v1/marketplace` & `/clone` | JWT (`AUTHOR+`) | Browse shared templates and clone with Cortex AI column remapping |
| `POST` | `/api/v1/execution/run-gate` | `X-API-Key` or JWT | Trigger `SYNC` (HTTP 200/422) or `ASYNC` (HTTP 202) pre-flight validation gate |
| `GET` | `/api/v1/runs/{run_id}` | `X-API-Key` or JWT | Poll execution status, gate decision, rule metrics & Cortex AI triage summary |
| `GET` | `/api/v1/runs/{run_id}/details/{detail_id}/export-csv` | JWT (`AUTHOR+`) | Stream CSV of up to 1,000 failing rows from `FAILURE_TABLE_REF` |
| `POST` | `/api/v1/runs/{run_id}/override` | JWT (`APPROVER+`) | Break-glass override (`BLOCKED_DOWNSTREAM` -> `OVERRIDDEN_PASS`) with audit reason |

### 5.2 Exact JSON Schema Contracts (`backend/app/schemas/models.py`)

#### A. Cortex AI Structured Output Schema (`CORTEX_RULE_LIST_SCHEMA`)
Pass this JSON Schema into `SNOWFLAKE.CORTEX.COMPLETE` `response_format` so `ai_generator.py` parses deterministic JSON without regex/markdown stripping:
```json
{
  "type": "object",
  "properties": {
    "generated_rules": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "risk_name": { "type": "string" },
          "risk_category": {
            "type": "string",
            "enum": [
              "REFERENTIAL_INTEGRITY",
              "HIERARCHY_LOOP",
              "BOUNDARY_VALUE",
              "DUPLICATE_KEY",
              "FRESHNESS_ANOMALY",
              "FROZEN_HORIZON_VIOLATION"
            ]
          },
          "risk_priority": {
            "type": "string",
            "enum": ["P1_BLOCKER", "P2_WARNING", "P3_INFO"]
          },
          "business_impact_desc": { "type": "string" },
          "validation_sql": { "type": "string" }
        },
        "required": [
          "risk_name",
          "risk_category",
          "risk_priority",
          "business_impact_desc",
          "validation_sql"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": ["generated_rules"],
  "additionalProperties": false
}
```

#### B. Pre-Flight Gate Request (`POST /api/v1/execution/run-gate`)
```json
{
  "team_id": "team_pdh_01",
  "domain_id": "dom_intersite_bom",
  "triggered_by": "ORCHESTRATOR_API",
  "execution_mode": "SYNC",
  "planning_cycle_id": "2025_W42_POR",
  "snapshot_ts": "2025-10-15T06:00:00Z"
}
```

#### C. Pre-Flight Gate Response (`HTTP 200` Pass/Warn, `HTTP 422` Blocked, `HTTP 202` Async Pending)
```json
{
  "run_id": "run_9f82c1a0",
  "team_id": "team_pdh_01",
  "domain_id": "dom_intersite_bom",
  "execution_status": "COMPLETED",
  "gate_decision": "BLOCKED_DOWNSTREAM",
  "allow_downstream": false,
  "planning_cycle_id": "2025_W42_POR",
  "snapshot_ts": "2025-10-15T06:00:00Z",
  "poll_url": "/api/v1/runs/run_9f82c1a0",
  "metrics": {
    "total_rules_run": 6,
    "p1_failures": 1,
    "p2_failures": 1,
    "duration_ms": 4820
  },
  "ai_triage_summary": "Downstream solver export blocked: 142 circular parent-child loops detected in Intersite BOM for cycle 2025_W42_POR, which would cause infinite netting traversal.",
  "rule_results": [
    {
      "detail_id": "det_001",
      "rule_id": "rule_bom_loop_01",
      "risk_name": "Intersite BOM Circular Hierarchy Loop",
      "risk_priority": "P1_BLOCKER",
      "rule_run_status": "FAIL",
      "failed_row_count": 142,
      "sample_failing_keys": [
        { "ITEM_ID": "SKU-9921", "SITE_ID": "FAB-AZ", "PARENT_SITE_ID": "FAB-AZ" }
      ],
      "export_csv_url": "/api/v1/runs/run_9f82c1a0/details/det_001/export-csv",
      "execution_ms": 1940
    }
  ]
}
```

#### D. Break-Glass Override Request (`POST /api/v1/runs/{run_id}/override`)
```json
{
  "override_reason": "Approved by PDH Release Lead: Fab-AZ loopback rows are quarantined in downstream solver filter for 2025_W42_POR."
}
```

---

## 6. Non-Functional Requirements & Guardrails

1. **AST-Based Read-Only SQL Guardrail (`sqlglot`):** Validate every `VALIDATION_SQL` string in `backend/app/security/sql_validator.py` using `sqlglot.parse(sql, read="snowflake")` before dry-run, saving, or execution:
   * Must parse into **exactly one** statement, and the root AST node MUST be `sqlglot.exp.Select` or `sqlglot.exp.Union` (with `WITH` CTEs permitted inside `Select`).
   * Walk the entire AST to reject any `Insert`, `Update`, `Delete`, `Merge`, `Drop`, `Alter`, `Create`, `TruncateTable`, `Command`, `Grant`, `Copy`, or `Call` nodes. Unlike naive regex checks, AST validation cleanly permits legitimate PDH columns named `UPDATE_TS`, `DELETE_FLAG`, or `CREATE_DATE`.
   * Bind runtime parameters (`:planning_cycle_id`, `:snapshot_ts`) exclusively via `snowflake-connector-python` parameterized binding (`qmark`/`numeric` or safe literal AST injection) to prevent SQL injection.
2. **Bounded AI & Compute Quotas:**
   * Cortex AI self-correction retries are strictly capped at `MAX_RETRIES = 3` per rule.
   * Snowflake rule execution enforces `STATEMENT_TIMEOUT_IN_SECONDS = 300` at the session/query level and limits concurrency to `5` parallel queries per gate run.
3. **Local Mock Mode (`SNOWFLAKE_MOCK_MODE=true`):** Include an environment flag so the entire application—including simulated 3-step catalog grounding, Cortex AI Structured Output generation, `sqlglot` AST validation, dry runs, CSV failure export, RBAC role switching (`AUTHOR` / `APPROVER` / `ADMIN`), async polling, and break-glass overrides—works out of the box locally without live Snowflake credentials.
4. **Pre-Seeded PDH & Multi-Team Demo Data:** On startup in mock mode, auto-populate 3 distinct teams:
   * **Planning Data Hub (PDH):** Onboarded datasets for *Intersite BOM* and *Weekly POR Demand Netting* (including sample `FROZEN_HORIZON_VIOLATION` and `HIERARCHY_LOOP` rules, plus a blocked run ready to test CSV export and Break-Glass override).
   * **Financial Data Hub:** *GL Journal Postings* dataset and boundary/referential rules.
   * **Customer Master Hub:** *Account Hierarchy* dataset with shared marketplace templates ready for cross-team cloning.
