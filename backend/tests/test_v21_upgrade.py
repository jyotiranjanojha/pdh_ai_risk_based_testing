import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.execution import compute_release_confidence_and_coverage
from app.db.snowflake_client import SnowflakePlatformClient, sf_client
from app.main import app
from app.schemas.models import map_risk_score_to_priority
from app.security.sql_validator import (
    SQLValidationError,
    extract_sql_lineage_summary,
    wrap_with_target_dataset_cte,
)

ADMIN_HEADERS = {"X-Mock-User-Email": "pdh.admin@enterprise.com", "X-Mock-User-Role": "ADMIN"}
APPROVER_HEADERS = {"X-Mock-User-Email": "pdh.lead@enterprise.com", "X-Mock-User-Role": "APPROVER"}
AUTHOR_HEADERS = {"X-Mock-User-Email": "pdh.author@enterprise.com", "X-Mock-User-Role": "AUTHOR"}


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Backward Compatibility: v2.0 payloads omitting all v2.1 fields
# ---------------------------------------------------------------------------


def test_v20_payloads_still_pass_unchanged(client: TestClient):
    dom = client.post(
        "/api/v1/teams/team_ip_supply/domains",
        json={
            "domain_name": "v2.0 Compat Dataset",
            "target_schema": "SUPPLY_CHAIN",
            "target_table": "WEEKLY_POR_NETTING",
            "related_tables": [],
            "temporal_cycle_column": "PLANNING_CYCLE_ID",
            "criticality": "HIGH",
        },
        headers=AUTHOR_HEADERS,
    )
    assert dom.status_code == 201
    dom_data = dom.json()
    assert dom_data["domain_name"] == "v2.0 Compat Dataset"
    assert dom_data["upstream_source_table"] is None

    disc = client.post(
        f"/api/v1/domains/{dom_data['domain_id']}/ai-discover",
        headers=AUTHOR_HEADERS,
    )
    assert disc.status_code == 200
    assert disc.json()["generated_count"] >= 5

    gate = client.post(
        "/api/v1/execution/run-gate",
        json={
            "team_id": "team_ip_supply",
            "domain_id": "dom_demand_netting",
            "triggered_by": "ORCHESTRATOR_API",
            "execution_mode": "SYNC",
            "planning_cycle_id": "2025_W42_POR",
        },
        headers=AUTHOR_HEADERS,
    )
    assert gate.status_code == 200
    body = gate.json()
    assert body["environment"] == "PROD"
    assert body["gate_decision"] == "PASS"


# ---------------------------------------------------------------------------
# 2. Scope 1: Likelihood x Impact Auto-Priority & Release Confidence Math
# ---------------------------------------------------------------------------


def test_likelihood_impact_priority_mapping():
    assert map_risk_score_to_priority(25) == "P1_BLOCKER"
    assert map_risk_score_to_priority(16) == "P1_BLOCKER"
    assert map_risk_score_to_priority(15) == "P2_WARNING"
    assert map_risk_score_to_priority(8) == "P2_WARNING"
    assert map_risk_score_to_priority(7) == "P3_INFO"
    assert map_risk_score_to_priority(1) == "P3_INFO"


def test_rule_update_recalculates_risk_score_and_priority(client: TestClient):
    # Update likelihood=5, impact=4 => score=20 => auto-maps to P1_BLOCKER
    res = client.patch(
        "/api/v1/rules/rule_bom_draft_05",
        json={"likelihood_score": 5, "impact_score": 4},
        headers=AUTHOR_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["risk_score"] == 20
    assert data["risk_priority"] == "P1_BLOCKER"

    # Manual priority override is respected even with low LxI
    res2 = client.patch(
        "/api/v1/rules/rule_bom_draft_05",
        json={"likelihood_score": 1, "impact_score": 2, "risk_priority": "P2_WARNING"},
        headers=AUTHOR_HEADERS,
    )
    assert res2.status_code == 200
    assert res2.json()["risk_score"] == 2
    assert res2.json()["risk_priority"] == "P2_WARNING"


def test_release_confidence_score_math():
    domain = {
        "BASELINE_SCHEMA_JSON": [
            {"column_name": "ITEM_ID"},
            {"column_name": "SITE_ID"},
            {"column_name": "BOM_QTY"},
            {"column_name": "UPDATE_TS"},
        ]
    }
    rules = [
        {"VALIDATION_SQL": "SELECT ITEM_ID, SITE_ID FROM __TARGET_DATASET__ WHERE BOM_QTY < 0"},
        {"VALIDATION_SQL": "SELECT ITEM_ID FROM __TARGET_DATASET__ WHERE UPDATE_TS IS NULL"},
    ]
    cov, conf = compute_release_confidence_and_coverage(
        domain=domain,
        approved_rules=rules,
        schema_drift_status="PASS",
        p1_failures=0,
        p2_failures=0,
    )
    assert cov == 100.0
    assert conf == 100.0

    cov_drift, conf_drift = compute_release_confidence_and_coverage(
        domain=domain,
        approved_rules=rules,
        schema_drift_status="DRIFT_DETECTED",
        p1_failures=1,
        p2_failures=0,
    )
    assert cov_drift == 100.0
    assert conf_drift == 35.0  # 100 - 40 - 25 = 35


# ---------------------------------------------------------------------------
# 3. Scope 2: Step-0 Schema Drift Detection & Re-Baseline RBAC
# ---------------------------------------------------------------------------


def test_schema_drift_detects_dropped_column_and_narrowed_precision():
    baseline = [
        {"column_name": "ITEM_ID", "data_type": "VARCHAR(64)", "numeric_precision": None, "numeric_scale": None},
        {"column_name": "BOM_QTY", "data_type": "NUMBER(18,4)", "numeric_precision": 18, "numeric_scale": 4},
        {"column_name": "SITE_ID", "data_type": "VARCHAR(32)", "numeric_precision": None, "numeric_scale": None},
    ]
    current_narrowed = [
        {"column_name": "ITEM_ID", "data_type": "VARCHAR(64)", "numeric_precision": None, "numeric_scale": None},
        {"column_name": "BOM_QTY", "data_type": "NUMBER(18,0)", "numeric_precision": 18, "numeric_scale": 0},
        # SITE_ID dropped
    ]
    res = SnowflakePlatformClient.compare_schema_contracts(baseline, current_narrowed)
    assert res["status"] == "DRIFT_DETECTED"
    issues = {d["column_name"]: d["issue"] for d in res["drifts"]}
    assert issues["SITE_ID"] == "DROPPED_COLUMN"
    assert issues["BOM_QTY"] == "SCALE_NARROWED"


def test_step0_schema_drift_blocks_gate_and_rebaseline_unblocks(client: TestClient):
    # Create a custom SQLite mock table and onboard it
    sf_client._mock_execute("CREATE TABLE IF NOT EXISTS CONTRACT_TEST_TBL (ID TEXT, QTY REAL)")
    dom = client.post(
        "/api/v1/teams/team_ip_supply/domains",
        json={
            "domain_name": "Schema Contract Test",
            "target_schema": "SUPPLY_CHAIN",
            "target_table": "CONTRACT_TEST_TBL",
            "criticality": "CRITICAL",
        },
        headers=AUTHOR_HEADERS,
    )
    assert dom.status_code == 201
    domain_id = dom.json()["domain_id"]
    assert len(dom.json()["baseline_schema_json"]) == 2

    # Simulate dropped column by recreating table without QTY
    sf_client._mock_execute("DROP TABLE CONTRACT_TEST_TBL")
    sf_client._mock_execute("CREATE TABLE CONTRACT_TEST_TBL (ID TEXT)")

    blocked = client.post(
        "/api/v1/execution/run-gate",
        json={"team_id": "team_ip_supply", "domain_id": domain_id, "execution_mode": "SYNC"},
        headers=AUTHOR_HEADERS,
    )
    assert blocked.status_code == 422
    b_data = blocked.json()
    assert b_data["gate_decision"] == "BLOCKED_DOWNSTREAM"
    assert b_data["schema_drift_status"] == "DRIFT_DETECTED"
    assert b_data["schema_drift_details"][0]["issue"] == "DROPPED_COLUMN"

    # AUTHOR cannot re-baseline
    denied = client.post(f"/api/v1/domains/{domain_id}/rebaseline", headers=AUTHOR_HEADERS)
    assert denied.status_code == 403

    # APPROVER re-baselines schema contract
    rebased = client.post(f"/api/v1/domains/{domain_id}/rebaseline", headers=APPROVER_HEADERS)
    assert rebased.status_code == 200
    assert rebased.json()["column_count"] == 1


# ---------------------------------------------------------------------------
# 4. Scope 3: Transformation SQL & PR-Diff Grounding
# ---------------------------------------------------------------------------


def test_transformation_sql_and_pr_diff_grounding(client: TestClient):
    pr_diff = """
    + SELECT b.ITEM_ID, b.SITE_ID, CASE WHEN b.BOM_QTY > 0 THEN b.BOM_QTY ELSE 0 END AS ADJ_QTY
    + FROM INTERSITE_BOM_STAGING b
    + JOIN SITE_MASTER s ON b.SITE_ID = s.SITE_ID
    + WHERE b.DELETE_FLAG = 0
    """
    lineage = extract_sql_lineage_summary(pr_diff)
    assert "INTERSITE_BOM_STAGING" in lineage["tables"]
    assert lineage["case_branches"] == 1
    assert len(lineage["joins"]) == 1
    assert len(lineage["filters"]) == 1

    dom = client.post(
        "/api/v1/teams/team_ip_supply/domains",
        json={
            "domain_name": "PR Diff Grounded BOM",
            "target_schema": "SUPPLY_CHAIN",
            "target_table": "INTERSITE_BOM",
            "upstream_source_table": "SUPPLY_CHAIN.INTERSITE_BOM_STAGING",
            "transformation_sql": "SELECT ITEM_ID, SITE_ID, SUM(BOM_QTY) FROM INTERSITE_BOM_STAGING GROUP BY 1, 2",
        },
        headers=AUTHOR_HEADERS,
    )
    assert dom.status_code == 201
    domain_id = dom.json()["domain_id"]

    disc = client.post(
        f"/api/v1/domains/{domain_id}/ai-discover",
        json={"pr_sql_diff": pr_diff},
        headers=AUTHOR_HEADERS,
    )
    assert disc.status_code == 200
    d_data = disc.json()
    assert any("Step 4:" in s for s in d_data["grounding_steps"])
    assert any("Step 5:" in s for s in d_data["grounding_steps"])
    categories = {r["risk_category"] for r in d_data["rules"]}
    assert "PIPELINE_RECONCILIATION" in categories


# ---------------------------------------------------------------------------
# 5. Scope 4: Multi-Env Target Schema Override & REGRESSION_DIFF CTE Binding
# ---------------------------------------------------------------------------


def test_regression_diff_cte_binding_and_override_sanitization():
    sql = (
        "SELECT t.ITEM_ID FROM __TARGET_DATASET__ t "
        "JOIN __BASELINE_DATASET__ b ON t.ITEM_ID = b.ITEM_ID "
        "WHERE ABS(t.NET_DEMAND_QTY - b.NET_DEMAND_QTY) > 10"
    )
    wrapped, params = wrap_with_target_dataset_cte(
        sql=sql,
        target_fqn="PDH_UAT_DB.PR_1042_SCHEMA.WEEKLY_POR_NETTING",
        baseline_fqn="IF_SUPPLY_CHAIN.PDH_SUPPLY_PLANNING_SRC_BASE.WEEKLY_POR_NETTING",
        temporal_cycle_column="PLANNING_CYCLE_ID",
        planning_cycle_id="2025_W42_POR",
    )
    assert "__TARGET_DATASET__ AS" in wrapped
    assert "PDH_UAT_DB.PR_1042_SCHEMA.WEEKLY_POR_NETTING" in wrapped
    assert "__BASELINE_DATASET__ AS" in wrapped
    assert "IF_SUPPLY_CHAIN.PDH_SUPPLY_PLANNING_SRC_BASE.WEEKLY_POR_NETTING" in wrapped
    assert params["planning_cycle_id"] == "2025_W42_POR"

    with pytest.raises(SQLValidationError):
        wrap_with_target_dataset_cte(
            sql="SELECT * FROM __TARGET_DATASET__",
            target_fqn="PDH_UAT_DB.PR_1042; DROP TABLE RBT_TEAMS",
        )


def test_dry_run_and_gate_reject_unsafe_schema_override(client: TestClient):
    bad_dry = client.post(
        "/api/v1/rules/rule_netting_neg_04/dry-run",
        json={
            "environment": "UAT",
            "target_schema_override": "PR_1042; DROP TABLE RBT_TEAMS",
        },
        headers=AUTHOR_HEADERS,
    )
    assert bad_dry.status_code == 200
    assert bad_dry.json()["valid_ast"] is False

    bad_gate = client.post(
        "/api/v1/execution/run-gate",
        json={
            "team_id": "team_ip_supply",
            "domain_id": "dom_demand_netting",
            "environment": "UAT",
            "target_schema_override": "PR_1042; DROP TABLE RBT_TEAMS",
        },
        headers=AUTHOR_HEADERS,
    )
    assert bad_gate.status_code == 400


# ---------------------------------------------------------------------------
# 6. Admin Module & Full RBAC Hierarchy (ADMIN > APPROVER > AUTHOR)
# ---------------------------------------------------------------------------


def test_admin_workspace_and_user_role_mapping_hierarchy(client: TestClient):
    # APPROVER cannot create team workspace
    approver_create = client.post(
        "/api/v1/teams",
        json={"team_name": "Approver Denied Hub", "snowflake_database": "DB", "snowflake_warehouse": "WH"},
        headers=APPROVER_HEADERS,
    )
    assert approver_create.status_code == 403

    # APPROVER cannot map user to role
    approver_map = client.post(
        "/api/v1/teams/team_ip_supply/members",
        json={"user_email": "new.user@intel.com", "user_role": "AUTHOR"},
        headers=APPROVER_HEADERS,
    )
    assert approver_map.status_code == 403

    # ADMIN can map user to role and upsert (update) role cleanly without duplicates
    m1 = client.post(
        "/api/v1/teams/team_ip_supply/members",
        json={"user_email": "new.user@intel.com", "user_role": "AUTHOR"},
        headers=ADMIN_HEADERS,
    )
    assert m1.status_code == 201
    assert m1.json()["user_role"] == "AUTHOR"

    m2 = client.post(
        "/api/v1/teams/team_ip_supply/members",
        json={"user_email": "new.user@intel.com", "user_role": "APPROVER"},
        headers=ADMIN_HEADERS,
    )
    assert m2.status_code == 201
    assert m2.json()["member_id"] == m1.json()["member_id"]
    assert m2.json()["user_role"] == "APPROVER"

    # APPROVER cannot remove member; ADMIN can
    del_denied = client.delete(
        f"/api/v1/teams/team_ip_supply/members/{m1.json()['member_id']}",
        headers=APPROVER_HEADERS,
    )
    assert del_denied.status_code == 403

    del_ok = client.delete(
        f"/api/v1/teams/team_ip_supply/members/{m1.json()['member_id']}",
        headers=ADMIN_HEADERS,
    )
    assert del_ok.status_code == 204

    # ADMIN also has APPROVER + AUTHOR privileges (e.g., approve rule & dry-run)
    dry_admin = client.post(
        "/api/v1/rules/rule_bom_draft_05/dry-run",
        json={"planning_cycle_id": "2025_W42_POR"},
        headers=ADMIN_HEADERS,
    )
    assert dry_admin.status_code == 200

    approve_admin = client.patch(
        "/api/v1/rules/rule_bom_draft_05",
        json={"status": "APPROVED"},
        headers=ADMIN_HEADERS,
    )
    assert approve_admin.status_code == 200
    assert approve_admin.json()["status"] == "APPROVED"

    # APPROVER also has AUTHOR privileges (e.g., dry-run)
    dry_approver = client.post(
        "/api/v1/rules/rule_bom_draft_05/dry-run",
        json={"planning_cycle_id": "2025_W42_POR"},
        headers=APPROVER_HEADERS,
    )
    assert dry_approver.status_code == 200
