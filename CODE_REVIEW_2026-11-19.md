# Code Review – pdh_ai_rbt (2026-11-19)

Scope: full scan of backend (`app/api`, `auth`, `security`, `db/snowflake_client.py`, `schemas`, `config`, DDL, tests, Docker/compose) and a targeted pass over the frontend (`lib/api.ts`, auth/headers, Dockerfile). Static review only; the tests were not run and the code was not modified.

Severity: **C** critical, **H** high, **M** medium, **L** low.

---

## 1. Security

### C1. No tenant (team) isolation on most endpoints
`require_role()` checks only a global role. No endpoint verifies that the caller belongs to the `team_id` of the resource being accessed.
- `GET /teams/{team_id}/rules|domains|schemas|members|runs`, `GET /rules/{id}`, `/rules/{id}/history`, `PATCH /rules/{id}`, `/rules/{id}/dry-run`, `/domains/{id}/grounding`, `/domains/{id}/ai-discover`, `POST /teams/{id}/domains`.
- `export-csv` returns another team's failing rows.
- `POST /runs/{id}/override` lets any APPROVER override any team's BLOCKED gate.
- `PATCH /teams/{id}`, `POST /teams/{id}/api-key` and `POST /teams/{id}/members` need only ADMIN, so an ADMIN of team A can rotate team B's key or add members.
- `GET /runs/{id}` enforces the team match only for API-key callers, not for JWT users.
- `GET /teams` returns every team (including webhook URLs) to any AUTHOR.
- `run-gate`: JWT/mock users can trigger runs for any `team_id`; only API keys are checked.
- Marketplace clone does not check that the user belongs to `target_team_id`.

Fix: add a `require_team_member(team_id, min_role)` dependency and resolve the owning team from the resource (rule, run or domain) before authorising. Return 404 instead of 403 for foreign resources.

### C2. Insecure default auth configuration
- `SNOWFLAKE_MOCK_MODE` defaults to `True`. With no `Authorization` header, an anonymous caller becomes **ADMIN** by default (`auth/dependencies.py`). `docker-compose.yml` also defaults mock mode to true.
- `ALLOW_DEMO_AUTH_HEADERS` has the same effect in non-mock mode.
- `OIDC_JWT_SECRET="change-me-in-production"` is a default; there is no startup check. Tokens can be forged if it is left unchanged.
- The default mock role should be the lowest privilege. Fail fast at startup when mock/demo headers are enabled together with a live Snowflake account, or when the secret is the default.
- The mock header's "no role header → look up the DB role" path only runs when a role header is absent, but the default `ADMIN` is applied before it. The branch is dead for the default path, so that lookup never applies.

### H1. JWT handling
- HS256 with a shared secret. Real OIDC providers use asymmetric JWKS (RS256/ES256). `.env.example` itself says "oidc-public-key-or-secret".
- If `OIDC_AUDIENCE` is empty, audience verification is silently disabled.
- `iss`, `exp` and `nbf` are not required (`options={"require": [...]}` is missing).
- The error text returns the raw PyJWT exception to the client.
- A role in the token claim is trusted when there is no DB membership. Make the DB authoritative.
- The caller-supplied `X-Team-Id` selects which team's role applies, but is never bound to the resources accessed (see C1).

### H2. SQL injection via onboarding fields (`ground_domain_metadata`)
`DomainCreate.target_schema`, `target_table` and the temporal columns are free text with no identifier validation. They are interpolated with f-strings into:
- `SHOW PRIMARY KEYS IN TABLE {db}.{schema}.{table}`
- `SELECT COUNT(*) FROM ...`
- `SELECT COUNT(*) - COUNT({c_name}) ...`
- (mock) `PRAGMA table_info({target_table})` and `SELECT COUNT(*) FROM {target_table}`

`team.snowflake_database` and `snowflake_warehouse` are also interpolated unvalidated (`USE WAREHOUSE {target_wh}`, `{db_name}.INFORMATION_SCHEMA...`). The `_validate_identifier` helper exists in `sql_validator.py` but is only applied at execution time, after grounding has already run. Add Pydantic validators on `DomainCreate`/`TeamCreate`/`TeamUpdate` and call `_validate_identifier` (with proper double-quote quoting) in every f-string path. `FAILURE_TABLE_REF` is also interpolated into `SELECT * FROM {ref}`; it is system-generated but should be validated.

### H3. "Read-only" SQL guard is a deny-list and does not enforce the target-dataset boundary
`validate_readonly_sql` only rejects a fixed set of AST node types. It still permits:
- Side-effecting or abusive Snowflake functions in a SELECT, e.g. `SYSTEM$ABORT_SESSION`, `SYSTEM$CANCEL_ALL_QUERIES`, `SNOWFLAKE.CORTEX.*` (credit burn), external functions and UDFs.
- Reading **any** table the service role can see, not just `__TARGET_DATASET__` (e.g. `SELECT * FROM OTHER_DB.HR.SALARIES`). Dry-run returns the first 5 rows to the caller and a full failure copy is persisted, which defeats the "zero data egress / tenant isolation" claim.
- Stage reads (`@stage`), `TABLE(...)` functions, `INFORMATION_SCHEMA`/`ACCOUNT_USAGE` queries.
- Unvalidated cross-tenant joins.

Recommendations:
- Convert to an allow-list: allowed tables (only `__TARGET_DATASET__`, the CTEs defined in the query, and the domain's `related_tables`) and allowed functions.
- Run rule SQL under a dedicated least-privilege **read-only** role scoped to the team's database/schema.
- Set `QUERY_TAG`, resource monitors and a per-statement row/byte limit.

### H4. Recursive-depth guardrail is regex-based and bypassable
`_check_recursive_depth_guardrail` serialises the WITH clause and regex-matches `depth <= N`.
- Any `depth <= 5` string anywhere satisfies it, including in a comment, a string literal, an unrelated column, or `... OR 1=1`.
- It only inspects the top-level `WITH`; a nested or subquery `WITH RECURSIVE` is ignored.
- `if not with_clause.args.get("recursive")` skips non-top-level recursion.
- Snowflake has no built-in recursion cap, so a bad rule can run until the 300 s statement timeout while holding the global lock (see M1).

Walk the AST for `exp.With(recursive=True)` at any depth, and verify the predicate is attached to the recursive member's WHERE/JOIN condition. Pair it with a hard server-side limit.

### H5. Separation of duties is incomplete
- An APPROVER can approve a rule they authored (`created_by == user.email` is not rejected).
- `PATCH` allows an AUTHOR to set `is_shared_template` (publishing to the cross-team marketplace) and `risk_priority`/`risk_category` (downgrading a P1 to P3, so the gate never blocks) on an already APPROVED rule without a re-approval. Only `validation_sql` edits reset the status to DRAFT.
- Status transitions are unconstrained (e.g. ARCHIVED → APPROVED, DRAFT_NEEDS_REVIEW → APPROVED without reviewing the compile error).
- Marketplace clone checks `IS_SHARED_TEMPLATE` but not `STATUS == 'APPROVED'`, so draft/archived templates can be cloned.
- Break-glass override: no dual control, no cap, and any APPROVER can override.

### M-SEC1. API-key auth
- Unsalted SHA-256 comparison through a DB equality query (acceptable for 192-bit random keys, but there is no expiry, scoping or last-used tracking). Use `hmac.compare_digest` if hashes are ever compared in code.
- The service principal is always `role="ADMIN"`. It should be a restricted `SERVICE` role (run-gate and read-run only).
- No rate limiting or lockout. Add it on `/execution/run-gate`.
- Key rotation overwrites the single key immediately, with no grace period for orchestrators.
- Demo keys are seeded in code (`rbt_pdh_demo_key_2025`, etc.). If mock seeding ever runs outside dev, they are valid credentials. Gate seeding behind an explicit `SEED_DEMO_DATA` flag.

### M-SEC2. Webhook SSRF
`ALERT_WEBHOOK_URL` is team-controlled and posted to server-side with `httpx` and no validation: no scheme/host allow-list, no private/link-local IP block (cloud metadata), no redirect restriction. Any exception is swallowed with no log. Validate at write time (`https` only, allowed hosts such as `hooks.slack.com`), disable redirects, and log failures. The payload includes AI triage text, so it can carry data.

### M-SEC3. Unauthenticated endpoints and information disclosure
- `/api/v1/auth/sso-start` and `/sso-status` have no auth. Anyone can trigger an SSO browser flow on the server and read the pending SSO URL. Because `webbrowser.open` is **monkey-patched globally** during the flow, this is also a thread-safety hazard.
- `/health` exposes mock mode and version; `/cortex/models` is open. Minor, but combine with C2.
- `str(exc)` from Snowflake is persisted to `ERROR_MESSAGE` and returned to callers (schema/table names, SQL fragments). Sanitise before returning.
- `print("OPEN_THIS_SSO_URL:", url)` writes SSO URLs to stdout/logs. Use the logging module and avoid logging URLs.

### M-SEC4. Prompt injection into Cortex
Triage and discovery prompts interpolate DB-sourced text (`business_context`, `DOMAIN_NAME`, `downstream_consumer`, column comments, rule names). Generated SQL is passed through the AST check, but the triage summary is stored and rendered/sent to webhooks without sanitisation. Delimit untrusted content in the prompt and escape/encode on output. The marketplace clone prompt embeds another team's SQL as instructions.

### L-SEC
- CSV export is vulnerable to formula injection (cells starting with `= + - @`). The filename uses un-validated IDs in `Content-Disposition`.
- CORS: `allow_credentials=True` together with `allow_methods/headers=["*"]`; fine for fixed origins, but keep origins pinned.
- Containers run as root (both Dockerfiles). Add a non-root `USER`.
- `pytest` is in the production `requirements.txt`; split into dev requirements. No dependency pinning for transitive packages, and no hash/lock file for the backend.
- Frontend: `X-Mock-User-*` headers are hard-wired in `lib/api.ts`, so the real SSO/JWT path is not wired in the UI. The role is a client-side selection, i.e. trivially spoofable.

---

## 2. Correctness bugs

### B1. `NameError` in `snowflake_client.py` (`HTTPException` not imported)
`HTTPException` is imported only locally inside `_get_live_connection` (line ~159), but is used in `list_schemas` (line ~916) and `ground_domain_metadata` (line ~1132). `GET /teams/{id}/schemas` for a missing team, and grounding of a missing/inaccessible table in live mode, will raise `NameError` → HTTP 500 instead of the intended 404. Import it at module scope. Also, DB-layer code should not raise HTTP exceptions; raise a domain error and map it in the API layer.

### B2. The global lock serialises all live queries and defeats concurrency controls
`_run_live` holds `self._lock` (RLock) for the entire Snowflake query, so `MAX_CONCURRENT_QUERIES` and the `asyncio.Semaphore` have no effect: all gate runs and every metadata call that takes the lock are serialised. A slow rule blocks every other tenant. Meanwhile `asyncio.wait_for` times out the awaiting coroutine, but the worker thread and the Snowflake statement keep running (and keep holding the lock). Use a per-call connection (or pool), no global lock around network I/O, and cancel the statement server-side on timeout (`cursor.cancel()` / `SYSTEM$CANCEL_QUERY`).

### B3. Blocking I/O in async handlers
Most `sf_client` methods are synchronous (Snowflake connector or SQLite) and are called directly inside `async def` handlers and inside `_execute_gate_run` (`finalize_execution_run`, `invoke_cortex_text`, `list_rules`, ...). This blocks the event loop; `/health` stalls while a Cortex call runs. A new connection is opened for every call (`_get_live_connection`), which is slow. Make the handlers `def` (threadpool) or wrap in `asyncio.to_thread`, and use a connection pool.

### B4. ASYNC gate runs can get stuck in RUNNING/PENDING forever
`_execute_gate_run` has no top-level try/except. If `invoke_cortex_text`, `finalize_execution_run` or the webhook raises, the task dies, the run stays `RUNNING/PENDING`, and `allow_downstream` stays false with no error recorded. `EXECUTION_STATUS` values `FAILED`/`TIMED_OUT` exist in the schema but are never written. Also, a run with **zero approved rules** returns `PASS` and `allow_downstream=True` — a domain with no rules silently passes the gate. Decide whether that should be `PASS_WITH_WARNINGS`/blocked. Triage failure should not fail the whole run.

### B5. Gate decision ignores P3 and treats ERROR/TIMEOUT inconsistently
`ERROR`/`TIMEOUT` on a P1 rule blocks (good), but P3 failures are silently ignored. Result status/`rule_run_status` values are not validated against `RuleRunStatus` on write. `sync` mode returns HTTP 422 for `BLOCKED_DOWNSTREAM`, which is non-standard and conflates validation errors with business outcome; the UI special-cases it by string-matching the path.

### B6. No idempotency or concurrency control on runs
Two simultaneous `run-gate` calls for the same domain run in parallel, and each creates transient failure tables. `ASYNC` returns before rules are even queried, with no limit on in-flight tasks or per-team quota (DoS/credit exhaustion).

### B7. Parameter binding bugs in `wrap_with_target_dataset_cte`
- `:planning_cycle_id` / `:snapshot_ts` are replaced by plain `str.replace`, which also rewrites occurrences inside string literals/comments and also matches longer names (`:planning_cycle_id_prev`).
- When the bind is referenced but no value is supplied, `None` is bound, so `col = NULL` is never true: a rule using `:planning_cycle_id` silently returns 0 rows (PASS) if the caller omits the cycle. This is a *false-pass* risk for a gate. Fail the run or require the parameter.
- The CTE is injected by regex on the string `^\s*WITH`, so a leading comment (`-- note\nWITH ...`) or a lowercase/odd whitespace form breaks merge logic. Do the injection with the already-parsed sqlglot AST instead.
- Validation is run on the pre-substitution SQL, but the executed SQL is the rewritten string; validate what is actually executed.
- Passing `%(name)s` bind style requires the connector's `pyformat`; any literal `%` in rule SQL (e.g. `LIKE 'A%'`) will break or be misinterpreted by the connector when params are provided. (Test with `LIKE '%x'`.)
- Temporal filter is an equality (`= planning_cycle_id`); the DDL mentions "frozen horizon" ranges. If the cycle is `None`, the filter is dropped, silently widening the scope to all cycles.

### B8. Auto-debug loop issues (`compile_and_auto_debug_rule`)
- Runs `max_retries+1` compile checks and up to 3 Cortex calls **per rule, sequentially, synchronously** inside the request (5–8 rules → long blocking request, easily >1 min; no timeout/budget).
- Only the first rule from the repair response is used; if `repaired_list` is empty the same SQL is retried unchanged, wasting attempts.
- The repaired rule can differ in intent from the original (the mock returns a different risk), but only SQL is replaced; the metadata (name, category, priority) is kept, so the rule name no longer matches its SQL.
- The compile-check passes if the target table is not in the mock SQLite (`if pragma:`), so any SQL "compiles" in mock mode, hiding bugs from tests.
- `compile_check_sql` ignores `planning_cycle_id` and checks with `None` binds.

### B9. `update_rule_and_log_history`
- `assert existing is not None` after the API already checked (race between check and update; asserts vanish with `-O`). Use a proper 404/409.
- `updates[k] is not None` skips `None`, so a field can never be cleared (e.g. `last_compile_error`, `business_impact_desc`).
- History is written only when status or SQL changes, so edits to priority/category/shared flag are not audited, and `modified_by` for `is_shared_template` changes is lost. Version number computed as `MAX+1` without a transaction → duplicate versions under concurrent edits.
- `approved_by` is not cleared when a previously approved rule is demoted/edited; stale approver remains on a DRAFT.
- Live path: `UPDATE` + history insert are not in an explicit transaction (Snowflake autocommit), so a failure leaves the audit trail inconsistent.

### B10. Data-model problems
- DDL (`01_init_ddl.sql`) declares `PRIMARY KEY`/`REFERENCES` but Snowflake does not enforce them (informational only); there are no uniqueness guarantees for `(TEAM_ID, USER_EMAIL)` — `add_team_member` happily inserts duplicates (the role lookup returns `rows[0]` arbitrarily, so a duplicate with a different role gives non-deterministic privileges).
- `RBT_EXECUTION_RUNS.SNAPSHOT_TS` is `TIMESTAMP_NTZ`, while the API treats it as a free string; no timezone handling.
- `RBT_TEST_RULES.IS_SHARED_TEMPLATE`, `RELATED_TABLES` etc. are positional in `INSERT ... VALUES (?, ?, ...)` in mock mode. Any column change breaks silently. Use named columns.
- Live failure tables are created as `{meta_prefix}.RBT_FAIL_{id}` in the **metadata schema**, not in `FAILURES_TRANSIENT` as the DDL/documentation states, and the 7-day retention is not a cleanup. Failing rows (potentially PII) are copied into a shared platform schema readable by the platform role, with no per-tenant ACL and no purge job.
- Failure export (`export-csv`) selects `SELECT * FROM {ref}` with `ref` taken from the DB row; ensure this cannot be tampered with.
- `GATE_DECISION` default in DDL is NULL, though the code expects `'PENDING'`.

### B11. Smaller bugs
- `_parse_sample_keys` swallows every exception and returns `[]`.
- `row_to_domain_out` calls `json.loads` on `RELATED_TABLES` without error handling; Snowflake VARIANT may return a string or list.
- `list_rule` default `max_depth = int(domain.get("MAX_HIERARCHY_DEPTH") or 10)` treats 0 as 10.
- `ground_domain_metadata` live: normalises `target_schema` containing `.` by taking parts, but `table_parts` handling can then overwrite `db_name` from the *team* with the user input — potential cross-database access (see C1/H2) since the db is overridden by user input rather than the team's database.
- Profiling queries run `COUNT/MIN/MAX` over **all** rows of the first 8 columns for each column (8 full scans) and `MIN/MAX` values are sent to the LLM prompt (potential PII/sensitive data leak to the model; comment says "non-PII" but nothing enforces it, e.g. `MIN(EMAIL)`). Use sampling and exclude/mask columns flagged sensitive.
- `invoke_cortex_text` in mock returns `prompt.splitlines()[-1]` as the "summary".
- `ThreadPool`/`threading` usage in `start_sso_login` reads `_pending_sso_url` without the lock; races between concurrent callers.
- Status codes: `POST /runs/{id}/override` for a non-blocked run returns 400 (should be 409); duplicate member returns 201 instead of 409.
- Dry-run swallows all exceptions into `error_message` (returns 200) and leaks the full executed SQL and DB errors.
- `compile_check_sql` swallows exceptions into `(False, str(exc))`; transient network errors are indistinguishable from SQL errors and burn auto-debug attempts.

---

## 3. Design / maintainability

- `snowflake_client.py` is ~2,070 lines and mixes: SQLite mock engine + seed data (≈500 lines), Snowflake connector, Cortex, SSO flow, and all CRUD. Every method has a duplicated `if MOCK ... else live` branch. Split into a repository interface with `MockRepository` / `SnowflakeRepository`, a `CortexClient`, and an `SsoManager`. Parameter style differs (`?` vs `%s`), and logic drifts between branches (e.g. `is_shared_template` is handled outside `col_map` in mock but inside it in live).
- Mock mode transpiles Snowflake SQL to SQLite through sqlglot; many Snowflake constructs behave differently, so passing tests in mock mode do not prove live behaviour. Add a small live-integration (or at least a compile-only) test suite.
- Module-level singleton `sf_client = SnowflakePlatformClient()` creates the SQLite DB and registers `auth_lookup` hooks at import time. This makes testing and configuration reloads awkward; `auth_lookup` as a mutable global class attribute hides the dependency. Use FastAPI dependencies / lifespan.
- Business rules live in routers (separation-of-duties, depth validation, history). Move them into a service layer.
- Pydantic models: no validators for identifiers (`target_table`, columns, warehouse), email format (`user_email: str` – use `EmailStr`), URL (`alert_webhook_url`), max length of `validation_sql`, `related_tables` entries, or `planning_cycle_id`/`snapshot_ts` format. `snapshot_ts` is a free string.
- `AVAILABLE_CORTEX_MODELS` is advertised, but the `cortex_model` in requests is not validated against it, so a user can select any model (cost control). Defaults differ between `config.py` (`claude-sonnet-4-5`) and `.env.example` (`claude-3-5-sonnet`).
- `main.py`: no structured logging, request IDs, metrics, rate limiting or global exception handler. `print()` is used for diagnostics. Swallowed exceptions (`except Exception: pass`) in `_get_live_connection` (`USE WAREHOUSE`) and constraint discovery hide permission issues.
- `.env.example` has `SNOWFLAKE_DATABASE=RBT_GLOBAL_PLATFORM`, while `config.py` defaults to `IF_SUPPLY_CHAIN` / a production-looking role name (`ROLE_IF_CDA_PDH_SUPPLY_PLANNING_ANALYSIS_CONTRIBUTOR`) — real internal object names are committed as defaults. Replace with neutral placeholders.
- `docker-compose.yml` does not pass `OIDC_*`, `ALLOW_DEMO_AUTH_HEADERS`, `SNOWFLAKE_AUTHENTICATOR`, `CORTEX_MODEL` or the key passphrase; the container config cannot be made production-ready without editing it. The backend Dockerfile has no `HEALTHCHECK` and no pinned digest; the backend image also copies no `.env`, good, but `MOCK_DB_PATH` has no volume.
- API design: `GET /teams/{id}/runs` fetches details per run (N+1 queries, each opening a new Snowflake connection) and has no pagination; the same applies to rules/history/marketplace lists.
- Frontend/BE contract: the frontend `api.ts` hand-maintains types that duplicate the Pydantic models; generate from OpenAPI to avoid drift.

---

## 4. Testing

- Tests only exercise mock mode and mostly happy-path RBAC. Missing:
  - Cross-tenant access tests (C1) — they would currently *fail*, which is the point.
  - SQL guard bypass tests (functions, other tables, nested recursive CTE, comments, string-literal `depth <= 5`, `%` in LIKE, lowercase/leading-comment `WITH`).
  - Injection tests on onboarding fields.
  - Gate-decision matrix (no rules, P3 fail, ERROR/TIMEOUT, missing `:planning_cycle_id`).
  - Async failure path (exception inside `_execute_gate_run`).
  - Self-approval and status-transition rules; marketplace clone of non-approved template.
  - JWT: expired, wrong audience/issuer, `alg=none`, missing role, empty audience.
- Tests mutate shared in-memory DB state (module-scoped client + singleton); ordering dependence risk. `test_jwt_authentication` monkeypatches `get_member_role` on a global — fine, but also sets mock mode false while the `sf_client` singleton is already initialised.
- No CI config, linting (ruff/black/mypy) or frontend tests; `npm run lint` is defined but there is no ESLint config in the tree.

---

## 5. Frontend (spot check)

- Positive: no `dangerouslySetInnerHTML`, no token in `localStorage`, `window.open(..., "noopener,noreferrer")`.
- Auth: the UI always sends `X-Mock-User-Email/Role` from client state, so the role/identity is user-controlled and there is no OIDC login flow. This is acceptable only for a demo; document it clearly and prevent production use (see C2).
- Rotated API key is held in component state and displayed once — good; ensure it is cleared on modal close and never logged.
- `apiFetch`: on non-JSON error bodies it calls `res.text()` after `res.json()` already consumed the body (the stream is already read), so the fallback throws; the error message is lost. Read text once and then try `JSON.parse`.
- `422` is special-cased by URL substring match for run-gate; fragile.
- Docker: runs as root; `.next/standalone` requires `output: "standalone"` in `next.config.mjs` (verify), no `public/` copy, no healthcheck.
- Large screenshots committed under `frontend/public/screenshots` (binary bloat); move to docs/ or LFS.

---

## 6. Priority list

1. **Tenant isolation** (C1) — add team-membership checks everywhere, plus tests.
2. **Auth defaults** (C2/H1) — lowest-privilege default, startup validation, JWKS/asymmetric verification, required claims.
3. **Injection and SQL guard** (H2/H3/H4) — identifier validation, allow-list/read-only role, AST-based recursion check.
4. **Fix `HTTPException` NameError** (B1) and add a global exception handler.
5. **Concurrency model** (B2/B3/B4) — remove the global lock, pool connections, cancel on timeout, wrap background runs in try/except and persist `FAILED`.
6. **Gate correctness** (B5/B7) — no-rules and missing-bind false-pass cases; validate the SQL actually executed.
7. **Separation of duties** (H5) — no self-approval, constrained transitions, re-approval on any material change.
8. **Webhook/SSRF, API-key hardening, error-message sanitising** (M-SEC1–3).
9. Refactor `snowflake_client.py`, add structured logging, pagination and a live-mode test strategy.
10. Hygiene: non-root containers, dev vs prod requirements, neutral defaults, CI/lint.

_Not covered in depth:_ lines 211–700 of `snowflake_client.py` (mock DDL/seed data, skimmed only), frontend page components (`rules`, `runs`, `onboard`, `marketplace`), and the markdown docs (PRD/architecture/HANDOFF).
