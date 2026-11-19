import json
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Body, Depends, HTTPException

from app.auth.dependencies import require_role
from app.config import settings
from app.db.snowflake_client import sf_client
from app.schemas.models import (
    AiDiscoverRequest,
    AiDiscoverResponse,
    CORTEX_RULE_LIST_SCHEMA,
    CurrentUser,
    GroundingContext,
    RuleOut,
)
from app.security.sql_validator import SQLValidationError, validate_readonly_sql

router = APIRouter(prefix="/api/v1/domains", tags=["Cortex AI Discovery"])


def row_to_rule_out(row: dict) -> RuleOut:
    return RuleOut(
        rule_id=row["RULE_ID"],
        team_id=row["TEAM_ID"],
        domain_id=row["DOMAIN_ID"],
        domain_name=row.get("DOMAIN_NAME"),
        risk_name=row["RISK_NAME"],
        risk_category=row.get("RISK_CATEGORY"),
        risk_priority=row["RISK_PRIORITY"],
        business_impact_desc=row.get("BUSINESS_IMPACT_DESC"),
        validation_sql=row["VALIDATION_SQL"],
        status=row["STATUS"],
        last_compile_error=row.get("LAST_COMPILE_ERROR"),
        is_shared_template=bool(row.get("IS_SHARED_TEMPLATE", False)),
        created_by=row.get("CREATED_BY"),
        approved_by=row.get("APPROVED_BY"),
        updated_ts=row["UPDATED_TS"],
    )


def compile_and_auto_debug_rule(
    rule_draft: Dict[str, Any],
    team: Dict[str, Any],
    domain: Dict[str, Any],
    grounding: GroundingContext,
    cortex_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate SQL via sqlglot AST and Snowflake EXPLAIN compile check.
    If compilation fails, invoke Cortex AI self-correction up to MAX_RETRIES = 3.
    """
    max_retries = settings.MAX_AUTO_DEBUG_RETRIES  # 3
    current_sql = rule_draft["validation_sql"]
    last_error: Optional[str] = None

    for attempt in range(max_retries + 1):
        try:
            validate_readonly_sql(
                current_sql,
                max_hierarchy_depth=int(domain.get("MAX_HIERARCHY_DEPTH") or 10),
            )
            compiled, compile_err = sf_client.compile_check_sql(current_sql, team, domain)
            if compiled:
                return {
                    **rule_draft,
                    "validation_sql": current_sql,
                    "status": "DRAFT",
                    "last_compile_error": None,
                }
            last_error = compile_err or "Unknown Snowflake compile error."
        except SQLValidationError as exc:
            last_error = f"AST Guardrail Error: {exc}"

        if attempt < max_retries:
            fix_prompt = (
                f"FIX THIS SQL ERROR (Attempt {attempt + 1}/{max_retries}):\n"
                f"Error: {last_error}\n"
                f"Original SQL:\n{current_sql}\n"
                f"Target Table Alias: __TARGET_DATASET__\n"
                f"Available Columns: {[c.column_name for c in grounding.columns]}\n"
                f"Return a single valid Snowflake SELECT/WITH query returning failing rows."
            )
            repaired = sf_client.invoke_cortex_structured(
                prompt=fix_prompt,
                json_schema=CORTEX_RULE_LIST_SCHEMA,
                warehouse=team["SNOWFLAKE_WAREHOUSE"],
                domain=domain,
                grounding=grounding,
                model_name=cortex_model,
            )
            repaired_list = repaired.get("generated_rules") or []
            if repaired_list:
                current_sql = repaired_list[0]["validation_sql"]

    return {
        **rule_draft,
        "validation_sql": current_sql,
        "status": "DRAFT_NEEDS_REVIEW",
        "last_compile_error": last_error,
    }


@router.post("/{domain_id}/ai-discover", response_model=AiDiscoverResponse)
async def ai_discover_rules(
    domain_id: str,
    payload: Optional[AiDiscoverRequest] = Body(default=None),
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    domain = sf_client.get_domain(domain_id)
    if not domain:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found.")
    team = sf_client.get_team(domain["TEAM_ID"])
    if not team:
        raise HTTPException(status_code=404, detail="Parent team not found.")

    # Step 1-3: Native Snowflake Catalog Grounding
    grounding = sf_client.ground_domain_metadata(team, domain)
    steps = [
        f"Step 1: Introspected {len(grounding.columns)} columns from INFORMATION_SCHEMA.COLUMNS",
        f"Step 2: Extracted {len(grounding.constraints)} physical PK/FK/Unique constraints",
        f"Step 3: Computed non-PII statistical profiles across {sum(p.row_count for p in grounding.profiles)} rows",
    ]

    prompt = (
        f"You are an Enterprise Snowflake Data Quality & Risk-Based Testing Architect.\n"
        f"Dataset: {domain['DOMAIN_NAME']} ({domain['TARGET_SCHEMA']}.{domain['TARGET_TABLE']})\n"
        f"Downstream Consumer: {domain.get('DOWNSTREAM_CONSUMER')}\n"
        f"Business Context: {domain.get('BUSINESS_CONTEXT')}\n"
        f"Temporal Cycle Column: {domain.get('TEMPORAL_CYCLE_COLUMN')}\n"
        f"Max Hierarchy Depth: {domain.get('MAX_HIERARCHY_DEPTH', 10)}\n"
        f"Grounding Metadata:\n{json.dumps(grounding.model_dump(), default=str)}\n\n"
        f"Generate 5 to 8 Snowflake SQL validation rules referencing `__TARGET_DATASET__` "
        f"(0 rows returned = PASS). Include duplicate keys, referential integrity, "
        f"bounded recursive hierarchy loops (WHERE depth <= {domain.get('MAX_HIERARCHY_DEPTH', 10)}), "
        f"and frozen-horizon/boundary checks."
    )

    chosen_model = payload.cortex_model if payload and payload.cortex_model else settings.CORTEX_MODEL
    cortex_result = sf_client.invoke_cortex_structured(
        prompt=prompt,
        json_schema=CORTEX_RULE_LIST_SCHEMA,
        warehouse=team["SNOWFLAKE_WAREHOUSE"],
        domain=domain,
        grounding=grounding,
        model_name=chosen_model,
    )

    saved_rules: List[RuleOut] = []
    draft_cnt = 0
    review_cnt = 0

    for draft in cortex_result.get("generated_rules", []):
        checked = compile_and_auto_debug_rule(draft, team, domain, grounding, cortex_model=chosen_model)
        row = sf_client.insert_rule(
            {
                "team_id": team["TEAM_ID"],
                "domain_id": domain_id,
                "risk_name": checked["risk_name"],
                "risk_category": checked["risk_category"],
                "risk_priority": checked["risk_priority"],
                "business_impact_desc": checked["business_impact_desc"],
                "validation_sql": checked["validation_sql"],
                "status": checked["status"],
                "last_compile_error": checked.get("last_compile_error"),
                "created_by": user.email,
            }
        )
        rule_out = row_to_rule_out(row)
        saved_rules.append(rule_out)
        if rule_out.status == "DRAFT":
            draft_cnt += 1
        else:
            review_cnt += 1

    return AiDiscoverResponse(
        domain_id=domain_id,
        grounding_steps=steps,
        grounding_summary=grounding,
        generated_count=len(saved_rules),
        draft_count=draft_cnt,
        needs_review_count=review_cnt,
        rules=saved_rules,
    )
