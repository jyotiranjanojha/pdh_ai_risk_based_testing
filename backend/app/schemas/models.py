from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums & Literal Types
# ---------------------------------------------------------------------------

UserRole = Literal["AUTHOR", "APPROVER", "ADMIN"]

RiskCategory = Literal[
    "REFERENTIAL_INTEGRITY",
    "HIERARCHY_LOOP",
    "BOUNDARY_VALUE",
    "DUPLICATE_KEY",
    "FRESHNESS_ANOMALY",
    "FROZEN_HORIZON_VIOLATION",
    "SCHEMA_CONTRACT_DRIFT",
    "PIPELINE_RECONCILIATION",
    "REGRESSION_DIFF",
]

RiskPriority = Literal["P1_BLOCKER", "P2_WARNING", "P3_INFO"]

EnvironmentType = Literal["DEV", "UAT", "PROD"]

SchemaDriftStatus = Literal["PASS", "DRIFT_DETECTED", "SKIPPED"]


def map_risk_score_to_priority(risk_score: int) -> RiskPriority:
    """Auto-map Likelihood * Impact score (1-25) to RBT priority."""
    if risk_score >= 16:
        return "P1_BLOCKER"
    if risk_score >= 8:
        return "P2_WARNING"
    return "P3_INFO"

RuleStatus = Literal["DRAFT", "DRAFT_NEEDS_REVIEW", "APPROVED", "ARCHIVED"]

DomainCriticality = Literal["CRITICAL", "HIGH", "MEDIUM"]

ExecutionMode = Literal["SYNC", "ASYNC"]

ExecutionStatus = Literal["RUNNING", "COMPLETED", "FAILED", "TIMED_OUT"]

GateDecision = Literal[
    "PENDING",
    "PASS",
    "BLOCKED_DOWNSTREAM",
    "PASS_WITH_WARNINGS",
    "OVERRIDDEN_PASS",
]

RuleRunStatus = Literal["PASS", "FAIL", "ERROR", "TIMEOUT"]


# ---------------------------------------------------------------------------
# Cortex AI Structured Output JSON Schemas (Section 5.2 A)
# ---------------------------------------------------------------------------

CORTEX_RULE_LIST_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "generated_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "risk_name": {"type": "string"},
                    "risk_category": {
                        "type": "string",
                        "enum": [
                            "REFERENTIAL_INTEGRITY",
                            "HIERARCHY_LOOP",
                            "BOUNDARY_VALUE",
                            "DUPLICATE_KEY",
                            "FRESHNESS_ANOMALY",
                            "FROZEN_HORIZON_VIOLATION",
                            "SCHEMA_CONTRACT_DRIFT",
                            "PIPELINE_RECONCILIATION",
                            "REGRESSION_DIFF",
                        ],
                    },
                    "risk_priority": {
                        "type": "string",
                        "enum": ["P1_BLOCKER", "P2_WARNING", "P3_INFO"],
                    },
                    "likelihood_score": {"type": "integer", "minimum": 1, "maximum": 5},
                    "impact_score": {"type": "integer", "minimum": 1, "maximum": 5},
                    "business_impact_desc": {"type": "string"},
                    "validation_sql": {"type": "string"},
                },
                "required": [
                    "risk_name",
                    "risk_category",
                    "risk_priority",
                    "likelihood_score",
                    "impact_score",
                    "business_impact_desc",
                    "validation_sql",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["generated_rules"],
    "additionalProperties": False,
}

CORTEX_REMAP_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "remapped_sql": {"type": "string"},
        "mapping_notes": {"type": "string"},
    },
    "required": ["remapped_sql", "mapping_notes"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Auth / User Context
# ---------------------------------------------------------------------------

class CurrentUser(BaseModel):
    email: str
    role: UserRole
    team_id: Optional[str] = None
    auth_method: Literal["JWT", "MOCK_HEADER", "API_KEY"] = "MOCK_HEADER"


# ---------------------------------------------------------------------------
# Teams & RBAC Members
# ---------------------------------------------------------------------------

class TeamCreate(BaseModel):
    team_name: str = Field(..., min_length=2, max_length=120)
    snowflake_database: str = Field(..., min_length=1, max_length=120)
    snowflake_warehouse: str = Field(..., min_length=1, max_length=120)
    default_downstream_target: Optional[str] = None
    alert_webhook_url: Optional[str] = None


class TeamUpdate(BaseModel):
    team_name: Optional[str] = Field(None, min_length=2, max_length=120)
    snowflake_database: Optional[str] = Field(None, min_length=1, max_length=120)
    snowflake_warehouse: Optional[str] = Field(None, min_length=1, max_length=120)
    default_downstream_target: Optional[str] = None
    alert_webhook_url: Optional[str] = None


class TeamOut(BaseModel):
    team_id: str
    team_name: str
    snowflake_database: str
    snowflake_warehouse: str
    default_downstream_target: Optional[str] = None
    has_api_key: bool = False
    alert_webhook_url: Optional[str] = None
    created_ts: datetime


class ApiKeyRotateResponse(BaseModel):
    team_id: str
    api_key: str
    message: str = "Store this API key securely; it will not be shown again."


class TeamMemberCreate(BaseModel):
    user_email: str = Field(..., min_length=3)
    user_role: UserRole


class TeamMemberOut(BaseModel):
    member_id: str
    team_id: str
    user_email: str
    user_role: UserRole
    created_ts: datetime


# ---------------------------------------------------------------------------
# Domains & Native Grounding
# ---------------------------------------------------------------------------

class DomainCreate(BaseModel):
    domain_name: str = Field(..., min_length=2, max_length=120)
    target_schema: str = Field(..., min_length=1, max_length=120)
    target_table: str = Field(..., min_length=1, max_length=120)
    related_tables: List[str] = Field(default_factory=list)
    temporal_cycle_column: Optional[str] = None
    snapshot_ts_column: Optional[str] = None
    max_hierarchy_depth: int = Field(default=10, ge=1, le=100)
    downstream_consumer: Optional[str] = None
    business_context: Optional[str] = None
    criticality: DomainCriticality = "HIGH"
    upstream_source_table: Optional[str] = None
    transformation_sql: Optional[str] = None


class DomainOut(BaseModel):
    domain_id: str
    team_id: str
    domain_name: str
    target_schema: str
    target_table: str
    related_tables: List[str] = Field(default_factory=list)
    temporal_cycle_column: Optional[str] = None
    snapshot_ts_column: Optional[str] = None
    max_hierarchy_depth: int = 10
    downstream_consumer: Optional[str] = None
    business_context: Optional[str] = None
    criticality: DomainCriticality = "HIGH"
    baseline_schema_json: Optional[List[Dict[str, Any]]] = None
    upstream_source_table: Optional[str] = None
    transformation_sql: Optional[str] = None
    is_active: bool = True
    created_ts: datetime


class RebaselineResponse(BaseModel):
    domain_id: str
    column_count: int
    baseline_schema_json: List[Dict[str, Any]] = Field(default_factory=list)


class GroundingColumnInfo(BaseModel):
    table_name: str
    column_name: str
    data_type: str
    is_nullable: bool
    comment: Optional[str] = None
    numeric_precision: Optional[int] = None
    numeric_scale: Optional[int] = None


class GroundingConstraintInfo(BaseModel):
    constraint_type: Literal["PRIMARY KEY", "FOREIGN KEY", "UNIQUE"]
    table_name: str
    column_name: str
    referenced_table: Optional[str] = None
    referenced_column: Optional[str] = None


class GroundingStatProfile(BaseModel):
    table_name: str
    row_count: int
    column_stats: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class GroundingContext(BaseModel):
    columns: List[GroundingColumnInfo] = Field(default_factory=list)
    constraints: List[GroundingConstraintInfo] = Field(default_factory=list)
    profiles: List[GroundingStatProfile] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Rules, History & Marketplace
# ---------------------------------------------------------------------------

class GeneratedRuleDraft(BaseModel):
    risk_name: str
    risk_category: RiskCategory
    risk_priority: RiskPriority
    likelihood_score: int = Field(default=3, ge=1, le=5)
    impact_score: int = Field(default=3, ge=1, le=5)
    business_impact_desc: str
    validation_sql: str


class RuleOut(BaseModel):
    rule_id: str
    team_id: str
    domain_id: str
    domain_name: Optional[str] = None
    risk_name: str
    risk_category: Optional[RiskCategory] = None
    risk_priority: RiskPriority
    likelihood_score: int = Field(default=3, ge=1, le=5)
    impact_score: int = Field(default=3, ge=1, le=5)
    risk_score: int = Field(default=9, ge=1, le=25)
    business_impact_desc: Optional[str] = None
    validation_sql: str
    status: RuleStatus = "DRAFT"
    last_compile_error: Optional[str] = None
    is_shared_template: bool = False
    created_by: Optional[str] = None
    approved_by: Optional[str] = None
    updated_ts: datetime


class RuleUpdate(BaseModel):
    risk_name: Optional[str] = None
    risk_category: Optional[RiskCategory] = None
    risk_priority: Optional[RiskPriority] = None
    likelihood_score: Optional[int] = Field(default=None, ge=1, le=5)
    impact_score: Optional[int] = Field(default=None, ge=1, le=5)
    business_impact_desc: Optional[str] = None
    validation_sql: Optional[str] = None
    status: Optional[RuleStatus] = None
    is_shared_template: Optional[bool] = None


class RuleHistoryOut(BaseModel):
    history_id: str
    rule_id: str
    version_num: int
    status_old: Optional[str] = None
    status_new: Optional[str] = None
    validation_sql_old: Optional[str] = None
    validation_sql_new: Optional[str] = None
    modified_by: str
    modified_ts: datetime


class RuleDryRunRequest(BaseModel):
    validation_sql: Optional[str] = None
    planning_cycle_id: Optional[str] = None
    snapshot_ts: Optional[str] = None
    environment: EnvironmentType = "PROD"
    target_schema_override: Optional[str] = None


class RuleDryRunResponse(BaseModel):
    rule_id: str
    valid_ast: bool
    compiled: bool
    failed_row_count: int = 0
    sample_failing_keys: List[Dict[str, Any]] = Field(default_factory=list)
    execution_ms: int = 0
    executed_sql: Optional[str] = None
    error_message: Optional[str] = None


class AiDiscoverRequest(BaseModel):
    cortex_model: Optional[str] = None
    pr_sql_diff: Optional[str] = None


class AiDiscoverResponse(BaseModel):
    domain_id: str
    grounding_steps: List[str]
    grounding_summary: GroundingContext
    generated_count: int
    draft_count: int
    needs_review_count: int
    rules: List[RuleOut]


class MarketplaceTemplateOut(BaseModel):
    rule_id: str
    source_team_id: str
    source_team_name: str
    source_domain_name: str
    risk_name: str
    risk_category: Optional[RiskCategory] = None
    risk_priority: RiskPriority
    likelihood_score: int = 3
    impact_score: int = 3
    risk_score: int = 9
    business_impact_desc: Optional[str] = None
    validation_sql: str
    approved_by: Optional[str] = None


class MarketplaceCloneRequest(BaseModel):
    template_rule_id: str
    target_team_id: str
    target_domain_id: str
    cortex_model: Optional[str] = None


class MarketplaceCloneResponse(BaseModel):
    cloned_rule: RuleOut
    mapping_notes: str


# ---------------------------------------------------------------------------
# Execution Gate & Break-Glass Override (Section 5.2 B, C, D)
# ---------------------------------------------------------------------------

class RunGateRequest(BaseModel):
    team_id: str
    domain_id: str
    triggered_by: str = "MANUAL_UI"
    execution_mode: ExecutionMode = "SYNC"
    planning_cycle_id: Optional[str] = None
    snapshot_ts: Optional[str] = None
    cortex_model: Optional[str] = None
    environment: EnvironmentType = "PROD"
    target_schema_override: Optional[str] = None


class RunMetricsOut(BaseModel):
    total_rules_run: int
    p1_failures: int
    p2_failures: int
    duration_ms: int
    coverage_pct: float = 0.0
    release_confidence_score: float = 100.0


class RuleResultOut(BaseModel):
    detail_id: str
    rule_id: str
    risk_name: str
    risk_priority: RiskPriority
    rule_run_status: RuleRunStatus
    failed_row_count: int
    sample_failing_keys: List[Dict[str, Any]] = Field(default_factory=list)
    export_csv_url: Optional[str] = None
    error_message: Optional[str] = None
    execution_ms: int = 0


class RunGateResponse(BaseModel):
    run_id: str
    team_id: str
    domain_id: str
    domain_name: Optional[str] = None
    triggered_by: Optional[str] = None
    execution_status: ExecutionStatus
    gate_decision: GateDecision
    allow_downstream: bool
    planning_cycle_id: Optional[str] = None
    snapshot_ts: Optional[str] = None
    environment: EnvironmentType = "PROD"
    target_schema_override: Optional[str] = None
    coverage_pct: float = 0.0
    release_confidence_score: float = 100.0
    schema_drift_status: SchemaDriftStatus = "PASS"
    schema_drift_details: List[Dict[str, Any]] = Field(default_factory=list)
    poll_url: str
    metrics: RunMetricsOut
    ai_triage_summary: Optional[str] = None
    override_by: Optional[str] = None
    override_reason: Optional[str] = None
    override_ts: Optional[datetime] = None
    run_ts: Optional[datetime] = None
    completed_ts: Optional[datetime] = None
    rule_results: List[RuleResultOut] = Field(default_factory=list)


class BreakGlassOverrideRequest(BaseModel):
    override_reason: str = Field(..., min_length=20, max_length=2000)

    @field_validator("override_reason")
    @classmethod
    def strip_and_validate_reason(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 20:
            raise ValueError("override_reason must be at least 20 non-whitespace characters.")
        return cleaned
