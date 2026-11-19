import sys
from pathlib import Path
import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.dependencies import (
    auth_lookup,
    get_service_or_user,
    hash_api_key,
    require_role,
)
from app.config import settings
from app.schemas.models import CurrentUser
from app.security.sql_validator import (
    SQLValidationError,
    validate_readonly_sql,
    wrap_with_target_dataset_cte,
)


# ---------------------------------------------------------------------------
# SQL AST Validator Tests
# ---------------------------------------------------------------------------

def test_allows_select_with_pdh_column_names():
    sql = """
    SELECT ITEM_ID, SITE_ID, UPDATE_TS, DELETE_FLAG, CREATE_DATE
    FROM __TARGET_DATASET__
    WHERE DELETE_FLAG = FALSE AND UPDATE_TS IS NOT NULL
    """
    ast = validate_readonly_sql(sql)
    assert ast is not None


def test_allows_union_and_cte():
    sql = """
    WITH base AS (
        SELECT ITEM_ID FROM __TARGET_DATASET__
    )
    SELECT ITEM_ID FROM base
    UNION ALL
    SELECT 'FALLBACK' AS ITEM_ID
    """
    ast = validate_readonly_sql(sql)
    assert ast is not None


@pytest.mark.parametrize(
    "bad_sql",
    [
        "INSERT INTO PDH.BOM VALUES ('A', 'B')",
        "UPDATE PDH.BOM SET QTY = 0",
        "DELETE FROM PDH.BOM WHERE ITEM_ID = 'X'",
        "MERGE INTO PDH.BOM t USING src s ON t.ID = s.ID WHEN MATCHED THEN DELETE",
        "DROP TABLE PDH.BOM",
        "ALTER TABLE PDH.BOM ADD COLUMN FOO STRING",
        "CREATE TABLE PDH.HACK AS SELECT * FROM PDH.BOM",
        "TRUNCATE TABLE PDH.BOM",
        "GRANT SELECT ON PDH.BOM TO ROLE PUBLIC",
        "CALL SYSTEM$WAIT(5)",
        "SELECT * FROM PDH.BOM; DROP TABLE PDH.BOM",
        "WITH c AS (SELECT 1) DELETE FROM PDH.BOM",
    ],
)
def test_blocks_dml_ddl_and_multi_statement(bad_sql: str):
    with pytest.raises(SQLValidationError):
        validate_readonly_sql(bad_sql)


def test_recursive_hierarchy_depth_guardrail():
    valid_recursive = """
    WITH RECURSIVE bom_tree AS (
        SELECT ITEM_ID, PARENT_SITE_ID, SITE_ID, 1 AS depth
        FROM __TARGET_DATASET__
        UNION ALL
        SELECT t.ITEM_ID, t.PARENT_SITE_ID, t.SITE_ID, bt.depth + 1
        FROM __TARGET_DATASET__ t
        JOIN bom_tree bt ON t.PARENT_SITE_ID = bt.SITE_ID
        WHERE bt.depth <= 10
    )
    SELECT * FROM bom_tree
    """
    validate_readonly_sql(valid_recursive, max_hierarchy_depth=10)

    unbounded_recursive = """
    WITH RECURSIVE bom_tree AS (
        SELECT ITEM_ID, PARENT_SITE_ID, SITE_ID, 1 AS depth
        FROM __TARGET_DATASET__
        UNION ALL
        SELECT t.ITEM_ID, t.PARENT_SITE_ID, t.SITE_ID, bt.depth + 1
        FROM __TARGET_DATASET__ t
        JOIN bom_tree bt ON t.PARENT_SITE_ID = bt.SITE_ID
    )
    SELECT * FROM bom_tree
    """
    with pytest.raises(SQLValidationError, match="depth bound"):
        validate_readonly_sql(unbounded_recursive, max_hierarchy_depth=10)

    excessive_recursive = valid_recursive.replace("bt.depth <= 10", "bt.depth <= 25")
    with pytest.raises(SQLValidationError, match="exceeds MAX_HIERARCHY_DEPTH"):
        validate_readonly_sql(excessive_recursive, max_hierarchy_depth=10)


def test_wrap_with_target_dataset_cte_injects_parameterized_filters():
    sql = "SELECT ITEM_ID, SITE_ID FROM __TARGET_DATASET__ WHERE QTY < 0"
    wrapped, params = wrap_with_target_dataset_cte(
        sql=sql,
        target_fqn="PDH_DB.SUPPLY_CHAIN.INTERSITE_BOM",
        temporal_cycle_column="PLANNING_CYCLE_ID",
        snapshot_ts_column="SNAPSHOT_TS",
        planning_cycle_id="2025_W42_POR",
        snapshot_ts="2025-10-15T06:00:00Z",
    )
    assert "WITH __TARGET_DATASET__ AS" in wrapped
    assert "PLANNING_CYCLE_ID = %(planning_cycle_id)s" in wrapped
    assert "SNAPSHOT_TS = %(snapshot_ts)s" in wrapped
    assert params == {
        "planning_cycle_id": "2025_W42_POR",
        "snapshot_ts": "2025-10-15T06:00:00Z",
    }


def test_wrap_merges_with_existing_recursive_cte():
    sql = """
    WITH RECURSIVE bom_tree AS (
        SELECT ITEM_ID, 1 AS depth FROM __TARGET_DATASET__
        UNION ALL
        SELECT t.ITEM_ID, bt.depth + 1 FROM __TARGET_DATASET__ t JOIN bom_tree bt ON 1=1 WHERE bt.depth <= 5
    )
    SELECT * FROM bom_tree
    """
    wrapped, params = wrap_with_target_dataset_cte(
        sql=sql,
        target_fqn="PDH_DB.SUPPLY_CHAIN.INTERSITE_BOM",
        temporal_cycle_column="PLANNING_CYCLE_ID",
        planning_cycle_id="2025_W42_POR",
        max_hierarchy_depth=10,
    )
    assert wrapped.startswith("WITH RECURSIVE __TARGET_DATASET__ AS")
    assert "bom_tree AS" in wrapped
    assert params["planning_cycle_id"] == "2025_W42_POR"


# ---------------------------------------------------------------------------
# RBAC & Service Auth Tests
# ---------------------------------------------------------------------------

def _build_test_app() -> FastAPI:
    app = FastAPI()

    @app.post("/approve")
    async def approve(user: CurrentUser = Depends(require_role("APPROVER"))):
        return {"ok": True, "role": user.role, "email": user.email}

    @app.post("/admin")
    async def admin(user: CurrentUser = Depends(require_role("ADMIN"))):
        return {"ok": True, "role": user.role}

    @app.post("/run-gate")
    async def run_gate(user: CurrentUser = Depends(get_service_or_user)):
        return {"ok": True, "team_id": user.team_id, "auth_method": user.auth_method}

    return app


def test_rbac_author_forbidden_for_approve_and_override(monkeypatch):
    monkeypatch.setattr(settings, "SNOWFLAKE_MOCK_MODE", True)
    client = TestClient(_build_test_app())

    resp = client.post(
        "/approve",
        headers={"X-Mock-User-Email": "alice@enterprise.com", "X-Mock-User-Role": "AUTHOR"},
    )
    assert resp.status_code == 403

    resp_ok = client.post(
        "/approve",
        headers={"X-Mock-User-Email": "bob@enterprise.com", "X-Mock-User-Role": "APPROVER"},
    )
    assert resp_ok.status_code == 200
    assert resp_ok.json()["role"] == "APPROVER"

    resp_admin_denied = client.post(
        "/admin",
        headers={"X-Mock-User-Email": "bob@enterprise.com", "X-Mock-User-Role": "APPROVER"},
    )
    assert resp_admin_denied.status_code == 403


def test_api_key_authentication(monkeypatch):
    raw_key = "rbt_live_pdh_secret_12345"
    expected_hash = hash_api_key(raw_key)
    monkeypatch.setattr(
        auth_lookup,
        "get_team_by_api_key_hash",
        lambda h: "team_pdh_01" if h == expected_hash else None,
    )
    client = TestClient(_build_test_app())

    bad_resp = client.post("/run-gate", headers={"X-API-Key": "wrong-key"})
    assert bad_resp.status_code == 401

    good_resp = client.post("/run-gate", headers={"X-API-Key": raw_key})
    assert good_resp.status_code == 200
    assert good_resp.json()["team_id"] == "team_pdh_01"
    assert good_resp.json()["auth_method"] == "API_KEY"


def test_jwt_authentication(monkeypatch):
    monkeypatch.setattr(settings, "SNOWFLAKE_MOCK_MODE", False)
    monkeypatch.setattr(
        auth_lookup,
        "get_member_role",
        lambda team_id, email: "APPROVER" if email == "lead@enterprise.com" else None,
    )
    token = jwt.encode(
        {"email": "lead@enterprise.com", "team_id": "team_pdh_01", "aud": settings.OIDC_AUDIENCE},
        settings.OIDC_JWT_SECRET,
        algorithm=settings.OIDC_JWT_ALGORITHM,
    )
    client = TestClient(_build_test_app())

    resp = client.post("/approve", headers={"Authorization": f"Bearer {token}", "X-Team-Id": "team_pdh_01"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "lead@enterprise.com"
