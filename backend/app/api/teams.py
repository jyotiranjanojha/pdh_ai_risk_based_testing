import secrets
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import require_role
from app.db.snowflake_client import sf_client
from app.schemas.models import (
    ApiKeyRotateResponse,
    CurrentUser,
    TeamCreate,
    TeamMemberCreate,
    TeamMemberOut,
    TeamOut,
    TeamUpdate,
)

router = APIRouter(prefix="/api/v1/teams", tags=["Teams & RBAC"])


def _row_to_team_out(row: dict) -> TeamOut:
    return TeamOut(
        team_id=row["TEAM_ID"],
        team_name=row["TEAM_NAME"],
        snowflake_database=row["SNOWFLAKE_DATABASE"],
        snowflake_warehouse=row["SNOWFLAKE_WAREHOUSE"],
        default_downstream_target=row.get("DEFAULT_DOWNSTREAM_TARGET"),
        has_api_key=bool(row.get("ORCHESTRATOR_API_KEY_HASH")),
        alert_webhook_url=row.get("ALERT_WEBHOOK_URL"),
        created_ts=row["CREATED_TS"],
    )


@router.get("", response_model=List[TeamOut])
async def list_teams(user: CurrentUser = Depends(require_role("AUTHOR"))):
    rows = sf_client.list_teams()
    return [_row_to_team_out(r) for r in rows]


@router.post("", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
async def create_team(
    payload: TeamCreate,
    user: CurrentUser = Depends(require_role("ADMIN")),
):
    row = sf_client.create_team(payload.model_dump())
    sf_client.add_team_member(row["TEAM_ID"], user.email, "ADMIN")
    return _row_to_team_out(row)


@router.patch("/{team_id}", response_model=TeamOut)
async def update_team(
    team_id: str,
    payload: TeamUpdate,
    user: CurrentUser = Depends(require_role("ADMIN")),
):
    team = sf_client.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{team_id}' not found.")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return _row_to_team_out(team)

    updated = sf_client.update_team(team_id, updates)
    return _row_to_team_out(updated)  # type: ignore[arg-type]


@router.post("/{team_id}/api-key", response_model=ApiKeyRotateResponse)
async def rotate_api_key(
    team_id: str,
    user: CurrentUser = Depends(require_role("ADMIN")),
):
    team = sf_client.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{team_id}' not found.")
    raw_key = f"rbt_live_{secrets.token_urlsafe(24)}"
    sf_client.rotate_team_api_key(team_id, raw_key)
    return ApiKeyRotateResponse(team_id=team_id, api_key=raw_key)


@router.get("/{team_id}/members", response_model=List[TeamMemberOut])
async def list_team_members(
    team_id: str,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    rows = sf_client.list_team_members(team_id)
    return [
        TeamMemberOut(
            member_id=r["MEMBER_ID"],
            team_id=r["TEAM_ID"],
            user_email=r["USER_EMAIL"],
            user_role=r["USER_ROLE"],
            created_ts=r["CREATED_TS"],
        )
        for r in rows
    ]


@router.post("/{team_id}/members", response_model=TeamMemberOut, status_code=status.HTTP_201_CREATED)
async def add_team_member(
    team_id: str,
    payload: TeamMemberCreate,
    user: CurrentUser = Depends(require_role("ADMIN")),
):
    team = sf_client.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{team_id}' not found.")
    r = sf_client.add_team_member(team_id, payload.user_email, payload.user_role)
    return TeamMemberOut(
        member_id=r["MEMBER_ID"],
        team_id=r["TEAM_ID"],
        user_email=r["USER_EMAIL"],
        user_role=r["USER_ROLE"],
        created_ts=r["CREATED_TS"],
    )


@router.delete("/{team_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_team_member(
    team_id: str,
    member_id: str,
    user: CurrentUser = Depends(require_role("ADMIN")),
):
    team = sf_client.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{team_id}' not found.")
    deleted = sf_client.remove_team_member(team_id, member_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Member '{member_id}' not found in team.")
