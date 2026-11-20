import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.ai_generator import compile_and_auto_debug_rule, row_to_rule_out
from app.auth.dependencies import ROLE_HIERARCHY, require_role
from app.db.snowflake_client import sf_client
from app.schemas.models import (
    CORTEX_REMAP_SCHEMA,
    CurrentUser,
    MarketplaceCloneRequest,
    MarketplaceCloneResponse,
    MarketplaceTemplateOut,
    RuleDryRunRequest,
    RuleDryRunResponse,
    RuleHistoryOut,
    RuleOut,
    RuleUpdate,
)
from app.security.sql_validator import SQLValidationError, sanitize_sql_identifier, validate_readonly_sql

router = APIRouter(prefix="/api/v1", tags=["Rules & Marketplace"])


@router.get("/teams/{team_id}/rules", response_model=List[RuleOut])
async def list_team_rules(
    team_id: str,
    domain_id: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    priority: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    rows = sf_client.list_rules(
        team_id=team_id,
        domain_id=domain_id,
        status=status_filter,
        priority=priority,
        category=category,
    )
    return [row_to_rule_out(r) for r in rows]


@router.get("/rules/{rule_id}", response_model=RuleOut)
async def get_rule(
    rule_id: str,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    row = sf_client.get_rule(rule_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    return row_to_rule_out(row)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(
    rule_id: str,
    payload: RuleUpdate,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    existing = sf_client.get_rule(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")

    updates = payload.model_dump(exclude_unset=True)

    # Separation of duties: only APPROVER or ADMIN can promote to APPROVED or ARCHIVED
    if updates.get("status") in ("APPROVED", "ARCHIVED"):
        if ROLE_HIERARCHY[user.role] < ROLE_HIERARCHY["APPROVER"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only APPROVER or ADMIN roles can approve or archive rules.",
            )
        if updates["status"] == "APPROVED":
            updates["approved_by"] = user.email

    # Validate SQL AST if validation_sql is being modified
    if "validation_sql" in updates and updates["validation_sql"] is not None:
        domain = sf_client.get_domain(existing["DOMAIN_ID"])
        max_depth = int(domain.get("MAX_HIERARCHY_DEPTH") or 10) if domain else 10
        try:
            validate_readonly_sql(updates["validation_sql"], max_hierarchy_depth=max_depth)
        except SQLValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SQL AST Guardrail rejected query: {exc}",
            ) from exc
        # Reset approved_by if SQL changed without explicit approval in same request
        if "status" not in updates and existing["STATUS"] == "APPROVED":
            updates["status"] = "DRAFT"

    updated = sf_client.update_rule_and_log_history(rule_id, updates, modified_by=user.email)
    return row_to_rule_out(updated)


@router.get("/rules/{rule_id}/history", response_model=List[RuleHistoryOut])
async def get_rule_history(
    rule_id: str,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    existing = sf_client.get_rule(rule_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    rows = sf_client.list_rule_history(rule_id)
    return [
        RuleHistoryOut(
            history_id=r["HISTORY_ID"],
            rule_id=r["RULE_ID"],
            version_num=int(r["VERSION_NUM"]),
            status_old=r.get("STATUS_OLD"),
            status_new=r.get("STATUS_NEW"),
            validation_sql_old=r.get("VALIDATION_SQL_OLD"),
            validation_sql_new=r.get("VALIDATION_SQL_NEW"),
            modified_by=r["MODIFIED_BY"],
            modified_ts=r["MODIFIED_TS"],
        )
        for r in rows
    ]


@router.post("/rules/{rule_id}/dry-run", response_model=RuleDryRunResponse)
async def dry_run_rule(
    rule_id: str,
    payload: RuleDryRunRequest,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    rule = sf_client.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    team = sf_client.get_team(rule["TEAM_ID"])
    domain = sf_client.get_domain(rule["DOMAIN_ID"])
    if not team or not domain:
        raise HTTPException(status_code=404, detail="Team or domain not found for rule.")

    sql_to_test = payload.validation_sql or rule["VALIDATION_SQL"]
    max_depth = int(domain.get("MAX_HIERARCHY_DEPTH") or 10)

    try:
        validate_readonly_sql(sql_to_test, max_hierarchy_depth=max_depth)
        if payload.target_schema_override and payload.target_schema_override.strip():
            sanitize_sql_identifier(payload.target_schema_override, "target_schema_override")
    except SQLValidationError as exc:
        return RuleDryRunResponse(
            rule_id=rule_id,
            valid_ast=False,
            compiled=False,
            error_message=str(exc),
        )

    try:
        res = await sf_client.execute_rule_sql(
            sql=sql_to_test,
            team=team,
            domain=domain,
            planning_cycle_id=payload.planning_cycle_id,
            snapshot_ts=payload.snapshot_ts,
            target_schema_override=payload.target_schema_override,
        )
        return RuleDryRunResponse(
            rule_id=rule_id,
            valid_ast=True,
            compiled=True,
            failed_row_count=res["failed_row_count"],
            sample_failing_keys=res["sample_failing_keys"],
            execution_ms=res["execution_ms"],
            executed_sql=res["executed_sql"],
        )
    except Exception as exc:
        return RuleDryRunResponse(
            rule_id=rule_id,
            valid_ast=True,
            compiled=False,
            error_message=str(exc),
        )


@router.get("/marketplace", response_model=List[MarketplaceTemplateOut])
async def list_marketplace(
    category: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    rows = sf_client.list_marketplace_templates(category=category)
    return [
        MarketplaceTemplateOut(
            rule_id=r["RULE_ID"],
            source_team_id=r["TEAM_ID"],
            source_team_name=r["SOURCE_TEAM_NAME"],
            source_domain_name=r["SOURCE_DOMAIN_NAME"],
            risk_name=r["RISK_NAME"],
            risk_category=r.get("RISK_CATEGORY"),
            risk_priority=r["RISK_PRIORITY"],
            likelihood_score=int(r.get("LIKELIHOOD_SCORE") or 3),
            impact_score=int(r.get("IMPACT_SCORE") or 3),
            risk_score=int(r.get("RISK_SCORE") or (int(r.get("LIKELIHOOD_SCORE") or 3) * int(r.get("IMPACT_SCORE") or 3))),
            business_impact_desc=r.get("BUSINESS_IMPACT_DESC"),
            validation_sql=r["VALIDATION_SQL"],
            approved_by=r.get("APPROVED_BY"),
        )
        for r in rows
    ]


@router.post("/marketplace/clone", response_model=MarketplaceCloneResponse, status_code=status.HTTP_201_CREATED)
async def clone_marketplace_template(
    payload: MarketplaceCloneRequest,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    template = sf_client.get_rule(payload.template_rule_id)
    if not template or not template.get("IS_SHARED_TEMPLATE"):
        raise HTTPException(status_code=404, detail="Shared marketplace template not found.")

    target_team = sf_client.get_team(payload.target_team_id)
    target_domain = sf_client.get_domain(payload.target_domain_id)
    if not target_team or not target_domain:
        raise HTTPException(status_code=404, detail="Target team or domain not found.")

    grounding = sf_client.ground_domain_metadata(target_team, target_domain)
    remap_prompt = (
        f"Remap this shared RBT SQL rule to target dataset '{target_domain['DOMAIN_NAME']}' "
        f"({target_domain['TARGET_SCHEMA']}.{target_domain['TARGET_TABLE']}).\n"
        f"Source SQL:\n{template['VALIDATION_SQL']}\n"
        f"Target Columns:\n{json.dumps([c.model_dump() for c in grounding.columns])}\n"
        f"Keep target table alias as `__TARGET_DATASET__`."
    )

    remap_res = sf_client.invoke_cortex_structured(
        prompt=remap_prompt,
        json_schema=CORTEX_REMAP_SCHEMA,
        warehouse=target_team["SNOWFLAKE_WAREHOUSE"],
        domain=target_domain,
        grounding=grounding,
        model_name=payload.cortex_model,
    )

    draft = {
        "risk_name": f"{template['RISK_NAME']} (Cloned)",
        "risk_category": template.get("RISK_CATEGORY") or "REFERENTIAL_INTEGRITY",
        "risk_priority": template["RISK_PRIORITY"],
        "business_impact_desc": template.get("BUSINESS_IMPACT_DESC") or "",
        "validation_sql": remap_res.get("remapped_sql") or template["VALIDATION_SQL"],
    }
    checked = compile_and_auto_debug_rule(
        draft, target_team, target_domain, grounding, cortex_model=payload.cortex_model
    )
    saved = sf_client.insert_rule(
        {
            "team_id": target_team["TEAM_ID"],
            "domain_id": target_domain["DOMAIN_ID"],
            "risk_name": checked["risk_name"],
            "risk_category": checked["risk_category"],
            "risk_priority": checked["risk_priority"],
            "likelihood_score": int(template.get("LIKELIHOOD_SCORE") or 3),
            "impact_score": int(template.get("IMPACT_SCORE") or 3),
            "business_impact_desc": checked["business_impact_desc"],
            "validation_sql": checked["validation_sql"],
            "status": checked["status"],
            "last_compile_error": checked.get("last_compile_error"),
            "created_by": user.email,
        }
    )

    return MarketplaceCloneResponse(
        cloned_rule=row_to_rule_out(saved),
        mapping_notes=remap_res.get("mapping_notes") or "Cloned and compiled against target schema.",
    )
