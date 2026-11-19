import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import require_role
from app.db.snowflake_client import sf_client
from app.schemas.models import (
    CurrentUser,
    DomainCreate,
    DomainOut,
    GroundingContext,
)

router = APIRouter(prefix="/api/v1", tags=["Domains & Grounding"])


def row_to_domain_out(row: dict) -> DomainOut:
    related = row.get("RELATED_TABLES")
    if isinstance(related, str):
        related = json.loads(related)
    return DomainOut(
        domain_id=row["DOMAIN_ID"],
        team_id=row["TEAM_ID"],
        domain_name=row["DOMAIN_NAME"],
        target_schema=row["TARGET_SCHEMA"],
        target_table=row["TARGET_TABLE"],
        related_tables=related or [],
        temporal_cycle_column=row.get("TEMPORAL_CYCLE_COLUMN"),
        snapshot_ts_column=row.get("SNAPSHOT_TS_COLUMN"),
        max_hierarchy_depth=int(row.get("MAX_HIERARCHY_DEPTH") or 10),
        downstream_consumer=row.get("DOWNSTREAM_CONSUMER"),
        business_context=row.get("BUSINESS_CONTEXT"),
        criticality=row.get("CRITICALITY") or "HIGH",
        is_active=bool(row.get("IS_ACTIVE", True)),
        created_ts=row["CREATED_TS"],
    )


@router.get("/teams/{team_id}/schemas", response_model=List[str])
async def list_schemas(
    team_id: str,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    return sf_client.list_schemas(team_id)


@router.get("/teams/{team_id}/domains", response_model=List[DomainOut])
async def list_domains(
    team_id: str,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    rows = sf_client.list_domains(team_id)
    return [row_to_domain_out(r) for r in rows]


@router.post("/teams/{team_id}/domains", response_model=DomainOut, status_code=status.HTTP_201_CREATED)
async def create_domain(
    team_id: str,
    payload: DomainCreate,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    team = sf_client.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{team_id}' not found.")
    row = sf_client.create_domain(team_id, payload.model_dump())
    return row_to_domain_out(row)


@router.get("/domains/{domain_id}/grounding", response_model=GroundingContext)
async def get_domain_grounding(
    domain_id: str,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    domain = sf_client.get_domain(domain_id)
    if not domain:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found.")
    team = sf_client.get_team(domain["TEAM_ID"])
    if not team:
        raise HTTPException(status_code=404, detail="Parent team not found.")
    return sf_client.ground_domain_metadata(team, domain)
