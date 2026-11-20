export type UserRole = "AUTHOR" | "APPROVER" | "ADMIN";

export type RiskCategory =
  | "REFERENTIAL_INTEGRITY"
  | "HIERARCHY_LOOP"
  | "BOUNDARY_VALUE"
  | "DUPLICATE_KEY"
  | "FRESHNESS_ANOMALY"
  | "FROZEN_HORIZON_VIOLATION"
  | "SCHEMA_CONTRACT_DRIFT"
  | "PIPELINE_RECONCILIATION"
  | "REGRESSION_DIFF";

export type RiskPriority = "P1_BLOCKER" | "P2_WARNING" | "P3_INFO";
export type EnvironmentType = "DEV" | "UAT" | "PROD";
export type SchemaDriftStatus = "PASS" | "DRIFT_DETECTED" | "SKIPPED";
export type RuleStatus = "DRAFT" | "DRAFT_NEEDS_REVIEW" | "APPROVED" | "ARCHIVED";
export type GateDecision =
  | "PENDING"
  | "PASS"
  | "BLOCKED_DOWNSTREAM"
  | "PASS_WITH_WARNINGS"
  | "OVERRIDDEN_PASS";

export interface Team {
  team_id: string;
  team_name: string;
  snowflake_database: string;
  snowflake_warehouse: string;
  default_downstream_target?: string | null;
  has_api_key: boolean;
  alert_webhook_url?: string | null;
  created_ts: string;
}

export interface TeamMember {
  member_id: string;
  team_id: string;
  user_email: string;
  user_role: UserRole;
  created_ts: string;
}

export interface Domain {
  domain_id: string;
  team_id: string;
  domain_name: string;
  target_schema: string;
  target_table: string;
  related_tables: string[];
  temporal_cycle_column?: string | null;
  snapshot_ts_column?: string | null;
  max_hierarchy_depth: number;
  downstream_consumer?: string | null;
  business_context?: string | null;
  criticality: "CRITICAL" | "HIGH" | "MEDIUM";
  baseline_schema_json?: Record<string, unknown>[] | null;
  upstream_source_table?: string | null;
  transformation_sql?: string | null;
  is_active: boolean;
  created_ts: string;
}

export interface Rule {
  rule_id: string;
  team_id: string;
  domain_id: string;
  domain_name?: string | null;
  risk_name: string;
  risk_category?: RiskCategory | null;
  risk_priority: RiskPriority;
  likelihood_score: number;
  impact_score: number;
  risk_score: number;
  business_impact_desc?: string | null;
  validation_sql: string;
  status: RuleStatus;
  last_compile_error?: string | null;
  is_shared_template: boolean;
  created_by?: string | null;
  approved_by?: string | null;
  updated_ts: string;
}

export interface RuleHistory {
  history_id: string;
  rule_id: string;
  version_num: number;
  status_old?: string | null;
  status_new?: string | null;
  validation_sql_old?: string | null;
  validation_sql_new?: string | null;
  modified_by: string;
  modified_ts: string;
}

export interface DryRunResponse {
  rule_id: string;
  valid_ast: boolean;
  compiled: boolean;
  failed_row_count: number;
  sample_failing_keys: Record<string, unknown>[];
  execution_ms: number;
  executed_sql?: string | null;
  error_message?: string | null;
}

export interface AiDiscoverResponse {
  domain_id: string;
  grounding_steps: string[];
  grounding_summary: {
    columns: Array<{
      table_name: string;
      column_name: string;
      data_type: string;
      is_nullable: boolean;
      comment?: string | null;
    }>;
    constraints: Array<{
      constraint_type: string;
      table_name: string;
      column_name: string;
      referenced_table?: string | null;
      referenced_column?: string | null;
    }>;
    profiles: Array<{
      table_name: string;
      row_count: number;
      column_stats: Record<string, Record<string, unknown>>;
    }>;
  };
  generated_count: number;
  draft_count: number;
  needs_review_count: number;
  rules: Rule[];
}

export interface MarketplaceTemplate {
  rule_id: string;
  source_team_id: string;
  source_team_name: string;
  source_domain_name: string;
  risk_name: string;
  risk_category?: RiskCategory | null;
  risk_priority: RiskPriority;
  likelihood_score: number;
  impact_score: number;
  risk_score: number;
  business_impact_desc?: string | null;
  validation_sql: string;
  approved_by?: string | null;
}

export interface RuleResult {
  detail_id: string;
  rule_id: string;
  risk_name: string;
  risk_priority: RiskPriority;
  rule_run_status: "PASS" | "FAIL" | "ERROR" | "TIMEOUT";
  failed_row_count: number;
  sample_failing_keys: Record<string, unknown>[];
  export_csv_url?: string | null;
  error_message?: string | null;
  execution_ms: number;
}

export interface ExecutionRun {
  run_id: string;
  team_id: string;
  domain_id: string;
  domain_name?: string | null;
  triggered_by?: string | null;
  execution_status: "RUNNING" | "COMPLETED" | "FAILED" | "TIMED_OUT";
  gate_decision: GateDecision;
  allow_downstream: boolean;
  planning_cycle_id?: string | null;
  snapshot_ts?: string | null;
  environment?: EnvironmentType;
  target_schema_override?: string | null;
  coverage_pct?: number;
  release_confidence_score?: number;
  schema_drift_status?: SchemaDriftStatus;
  schema_drift_details?: Record<string, unknown>[];
  poll_url: string;
  metrics: {
    total_rules_run: number;
    p1_failures: number;
    p2_failures: number;
    duration_ms: number;
    coverage_pct?: number;
    release_confidence_score?: number;
  };
  ai_triage_summary?: string | null;
  override_by?: string | null;
  override_reason?: string | null;
  override_ts?: string | null;
  run_ts?: string | null;
  completed_ts?: string | null;
  rule_results: RuleResult[];
}

export interface AuthContextHeaders {
  email: string;
  role: UserRole;
  teamId?: string;
}

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function apiFetch<T>(
  path: string,
  auth: AuthContextHeaders,
  init: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Mock-User-Email": auth.email,
    "X-Mock-User-Role": auth.role,
    ...(auth.teamId ? { "X-Team-Id": auth.teamId } : {}),
    ...((init.headers as Record<string, string>) || {}),
  };

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });

  // HTTP 422 on run-gate is a valid blocked gate payload
  if (!res.ok && !(res.status === 422 && path.includes("/execution/run-gate"))) {
    let detail = `HTTP ${res.status}`;
    try {
      const errBody = await res.json();
      detail = errBody.detail || JSON.stringify(errBody);
    } catch {
      detail = await res.text();
    }
    throw new Error(detail);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return (await res.json()) as T;
}

export const rbtApi = {
  health: () => fetch(`${API_BASE}/health`).then((r) => r.json()),

  listCortexModels: () =>
    fetch(`${API_BASE}/api/v1/cortex/models`).then(
      (r) => r.json() as Promise<{ default_model: string; models: string[] }>
    ),

  getSsoStatus: () =>
    fetch(`${API_BASE}/api/v1/auth/sso-status`).then(
      (r) =>
        r.json() as Promise<{
          connected: boolean;
          mock_mode: boolean;
          authenticator: string;
          sso_url: string | null;
        }>
    ),

  startSsoLogin: () =>
    fetch(`${API_BASE}/api/v1/auth/sso-start`, { method: "POST" }).then(
      (r) =>
        r.json() as Promise<{
          connected: boolean;
          mock_mode: boolean;
          authenticator: string;
          sso_url: string | null;
        }>
    ),

  listTeams: (auth: AuthContextHeaders) => apiFetch<Team[]>("/api/v1/teams", auth),

  createTeam: (
    auth: AuthContextHeaders,
    payload: {
      team_name: string;
      snowflake_database: string;
      snowflake_warehouse: string;
      default_downstream_target?: string;
      alert_webhook_url?: string;
    }
  ) =>
    apiFetch<Team>("/api/v1/teams", auth, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateTeam: (
    auth: AuthContextHeaders,
    teamId: string,
    payload: Partial<Team>
  ) =>
    apiFetch<Team>(`/api/v1/teams/${teamId}`, auth, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  rotateApiKey: (auth: AuthContextHeaders, teamId: string) =>
    apiFetch<{ team_id: string; api_key: string; message: string }>(
      `/api/v1/teams/${teamId}/api-key`,
      auth,
      { method: "POST" }
    ),

  listTeamMembers: (auth: AuthContextHeaders, teamId: string) =>
    apiFetch<TeamMember[]>(`/api/v1/teams/${teamId}/members`, auth),

  upsertTeamMember: (
    auth: AuthContextHeaders,
    teamId: string,
    payload: { user_email: string; user_role: UserRole }
  ) =>
    apiFetch<TeamMember>(`/api/v1/teams/${teamId}/members`, auth, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  removeTeamMember: (auth: AuthContextHeaders, teamId: string, memberId: string) =>
    apiFetch<void>(`/api/v1/teams/${teamId}/members/${memberId}`, auth, {
      method: "DELETE",
    }),

  listSchemas: (auth: AuthContextHeaders, teamId: string) =>
    apiFetch<string[]>(`/api/v1/teams/${teamId}/schemas`, auth),

  listDomains: (auth: AuthContextHeaders, teamId: string) =>
    apiFetch<Domain[]>(`/api/v1/teams/${teamId}/domains`, auth),

  createDomain: (
    auth: AuthContextHeaders,
    teamId: string,
    payload: {
      domain_name: string;
      target_schema: string;
      target_table: string;
      related_tables: string[];
      temporal_cycle_column?: string;
      snapshot_ts_column?: string;
      max_hierarchy_depth: number;
      downstream_consumer?: string;
      business_context?: string;
      criticality: "CRITICAL" | "HIGH" | "MEDIUM";
      upstream_source_table?: string;
      transformation_sql?: string;
    }
  ) =>
    apiFetch<Domain>(`/api/v1/teams/${teamId}/domains`, auth, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  rebaselineDomain: (auth: AuthContextHeaders, domainId: string) =>
    apiFetch<{
      domain_id: string;
      column_count: number;
      baseline_schema_json: Record<string, unknown>[];
    }>(`/api/v1/domains/${domainId}/rebaseline`, auth, {
      method: "POST",
    }),

  aiDiscover: (
    auth: AuthContextHeaders,
    domainId: string,
    cortexModel?: string,
    prSqlDiff?: string
  ) =>
    apiFetch<AiDiscoverResponse>(`/api/v1/domains/${domainId}/ai-discover`, auth, {
      method: "POST",
      body: JSON.stringify({
        cortex_model: cortexModel,
        ...(prSqlDiff ? { pr_sql_diff: prSqlDiff } : {}),
      }),
    }),

  listRules: (
    auth: AuthContextHeaders,
    teamId: string,
    filters?: { domain_id?: string; status?: string; priority?: string; category?: string }
  ) => {
    const qs = new URLSearchParams();
    if (filters?.domain_id) qs.set("domain_id", filters.domain_id);
    if (filters?.status) qs.set("status", filters.status);
    if (filters?.priority) qs.set("priority", filters.priority);
    if (filters?.category) qs.set("category", filters.category);
    const query = qs.toString() ? `?${qs.toString()}` : "";
    return apiFetch<Rule[]>(`/api/v1/teams/${teamId}/rules${query}`, auth);
  },

  updateRule: (
    auth: AuthContextHeaders,
    ruleId: string,
    payload: Partial<{
      validation_sql: string;
      status: RuleStatus;
      is_shared_template: boolean;
      risk_priority: RiskPriority;
      likelihood_score: number;
      impact_score: number;
    }>
  ) =>
    apiFetch<Rule>(`/api/v1/rules/${ruleId}`, auth, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  getRuleHistory: (auth: AuthContextHeaders, ruleId: string) =>
    apiFetch<RuleHistory[]>(`/api/v1/rules/${ruleId}/history`, auth),

  dryRunRule: (
    auth: AuthContextHeaders,
    ruleId: string,
    payload: {
      validation_sql?: string;
      planning_cycle_id?: string;
      snapshot_ts?: string;
      environment?: EnvironmentType;
      target_schema_override?: string;
    }
  ) =>
    apiFetch<DryRunResponse>(`/api/v1/rules/${ruleId}/dry-run`, auth, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listMarketplace: (auth: AuthContextHeaders, category?: string) => {
    const q = category ? `?category=${encodeURIComponent(category)}` : "";
    return apiFetch<MarketplaceTemplate[]>(`/api/v1/marketplace${q}`, auth);
  },

  cloneTemplate: (
    auth: AuthContextHeaders,
    payload: {
      template_rule_id: string;
      target_team_id: string;
      target_domain_id: string;
      cortex_model?: string;
    }
  ) =>
    apiFetch<{ cloned_rule: Rule; mapping_notes: string }>(
      "/api/v1/marketplace/clone",
      auth,
      { method: "POST", body: JSON.stringify(payload) }
    ),

  listRuns: (auth: AuthContextHeaders, teamId: string) =>
    apiFetch<ExecutionRun[]>(`/api/v1/teams/${teamId}/runs`, auth),

  getRun: (auth: AuthContextHeaders, runId: string) =>
    apiFetch<ExecutionRun>(`/api/v1/runs/${runId}`, auth),

  runGate: (
    auth: AuthContextHeaders,
    payload: {
      team_id: string;
      domain_id: string;
      triggered_by: string;
      execution_mode: "SYNC" | "ASYNC";
      planning_cycle_id?: string;
      snapshot_ts?: string;
      cortex_model?: string;
      environment?: EnvironmentType;
      target_schema_override?: string;
    }
  ) =>
    apiFetch<ExecutionRun>("/api/v1/execution/run-gate", auth, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  overrideRun: (
    auth: AuthContextHeaders,
    runId: string,
    override_reason: string
  ) =>
    apiFetch<ExecutionRun>(`/api/v1/runs/${runId}/override`, auth, {
      method: "POST",
      body: JSON.stringify({ override_reason }),
    }),

  downloadCsv: async (
    auth: AuthContextHeaders,
    runId: string,
    detailId: string
  ) => {
    const res = await fetch(
      `${API_BASE}/api/v1/runs/${runId}/details/${detailId}/export-csv`,
      {
        headers: {
          "X-Mock-User-Email": auth.email,
          "X-Mock-User-Role": auth.role,
        },
      }
    );
    if (!res.ok) throw new Error(`CSV download failed (${res.status})`);
    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `rbt_failures_${runId}_${detailId}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  },
};
