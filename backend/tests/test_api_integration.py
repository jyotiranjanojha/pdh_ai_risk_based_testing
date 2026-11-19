import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.db.snowflake_client import sf_client


ADMIN_HEADERS = {"X-Mock-User-Email": "pdh.admin@enterprise.com", "X-Mock-User-Role": "ADMIN"}
APPROVER_HEADERS = {"X-Mock-User-Email": "pdh.lead@enterprise.com", "X-Mock-User-Role": "APPROVER"}
AUTHOR_HEADERS = {"X-Mock-User-Email": "pdh.author@enterprise.com", "X-Mock-User-Role": "AUTHOR"}


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_health_and_seeded_teams(client: TestClient):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["mock_mode"] is True

    teams = client.get("/api/v1/teams", headers=AUTHOR_HEADERS)
    assert teams.status_code == 200
    names = [t["team_name"] for t in teams.json()]
    assert "IP Supply Planning Data Hub" in names
    assert "IP Demand Planning Data Hub" in names
    assert "IP Material Planning Data Hub" in names
    assert "IF Supply Planning Data Hub" in names
    assert "IF Demand Planning Data Hub" in names
    assert "IF Material Planning Data Hub" in names


def test_team_creation_and_api_key_rotation(client: TestClient):
    # AUTHOR cannot create team
    denied = client.post(
        "/api/v1/teams",
        json={"team_name": "Unauthorized Team", "snowflake_database": "DB", "snowflake_warehouse": "WH"},
        headers=AUTHOR_HEADERS,
    )
    assert denied.status_code == 403

    created = client.post(
        "/api/v1/teams",
        json={
            "team_name": "Logistics Data Hub",
            "snowflake_database": "LOG_DB",
            "snowflake_warehouse": "LOG_WH",
            "default_downstream_target": "TMS_CORE",
        },
        headers=ADMIN_HEADERS,
    )
    assert created.status_code == 201
    team_id = created.json()["team_id"]

    key_resp = client.post(f"/api/v1/teams/{team_id}/api-key", headers=ADMIN_HEADERS)
    assert key_resp.status_code == 200
    assert key_resp.json()["api_key"].startswith("rbt_live_")


def test_domain_onboarding_and_ai_discovery(client: TestClient):
    dom_resp = client.post(
        "/api/v1/teams/team_ip_supply/domains",
        json={
            "domain_name": "Factory Capacity Snapshot",
            "target_schema": "SUPPLY_CHAIN",
            "target_table": "INTERSITE_BOM",
            "temporal_cycle_column": "PLANNING_CYCLE_ID",
            "snapshot_ts_column": "SNAPSHOT_TS",
            "max_hierarchy_depth": 10,
            "downstream_consumer": "PLANNING_SOLVER",
            "criticality": "CRITICAL",
            "business_context": "Checks factory capacity and BOM loops before solver run.",
        },
        headers=AUTHOR_HEADERS,
    )
    assert dom_resp.status_code == 201
    domain_id = dom_resp.json()["domain_id"]

    disc = client.post(f"/api/v1/domains/{domain_id}/ai-discover", headers=AUTHOR_HEADERS)
    assert disc.status_code == 200
    body = disc.json()
    assert len(body["grounding_steps"]) == 3
    assert body["generated_count"] >= 5
    assert body["draft_count"] >= 1


def test_rule_edit_dry_run_history_and_rbac_approval(client: TestClient):
    rule_id = "rule_bom_draft_05"

    # Dry-run with planning_cycle_id
    dry = client.post(
        f"/api/v1/rules/{rule_id}/dry-run",
        json={"planning_cycle_id": "2025_W42_POR"},
        headers=AUTHOR_HEADERS,
    )
    assert dry.status_code == 200
    assert dry.json()["valid_ast"] is True
    assert dry.json()["compiled"] is True

    # Reject DML edit
    bad_edit = client.patch(
        f"/api/v1/rules/{rule_id}",
        json={"validation_sql": "DELETE FROM INTERSITE_BOM"},
        headers=AUTHOR_HEADERS,
    )
    assert bad_edit.status_code == 400

    # Valid SQL edit by AUTHOR
    good_edit = client.patch(
        f"/api/v1/rules/{rule_id}",
        json={"validation_sql": "SELECT ITEM_ID, SITE_ID, UPDATE_TS FROM __TARGET_DATASET__ WHERE SITE_ID IS NULL"},
        headers=AUTHOR_HEADERS,
    )
    assert good_edit.status_code == 200

    # AUTHOR cannot approve
    auth_approve = client.patch(
        f"/api/v1/rules/{rule_id}",
        json={"status": "APPROVED"},
        headers=AUTHOR_HEADERS,
    )
    assert auth_approve.status_code == 403

    # APPROVER can approve
    lead_approve = client.patch(
        f"/api/v1/rules/{rule_id}",
        json={"status": "APPROVED"},
        headers=APPROVER_HEADERS,
    )
    assert lead_approve.status_code == 200
    assert lead_approve.json()["status"] == "APPROVED"
    assert lead_approve.json()["approved_by"] == "pdh.lead@enterprise.com"

    # Verify immutable audit history
    hist = client.get(f"/api/v1/rules/{rule_id}/history", headers=AUTHOR_HEADERS)
    assert hist.status_code == 200
    assert len(hist.json()) >= 2


def test_marketplace_list_and_clone(client: TestClient):
    mkt = client.get("/api/v1/marketplace", headers=AUTHOR_HEADERS)
    assert mkt.status_code == 200
    templates = mkt.json()
    assert len(templates) >= 2

    clone = client.post(
        "/api/v1/marketplace/clone",
        json={
            "template_rule_id": templates[0]["rule_id"],
            "target_team_id": "team_ip_supply",
            "target_domain_id": "dom_intersite_bom",
        },
        headers=AUTHOR_HEADERS,
    )
    assert clone.status_code == 201
    assert clone.json()["cloned_rule"]["team_id"] == "team_ip_supply"
    assert clone.json()["cloned_rule"]["status"] == "DRAFT"


def test_execution_gate_sync_async_and_api_key(client: TestClient):
    # SYNC blocked on 2025_W42_POR -> HTTP 422
    blocked = client.post(
        "/api/v1/execution/run-gate",
        json={
            "team_id": "team_ip_supply",
            "domain_id": "dom_intersite_bom",
            "triggered_by": "ORCHESTRATOR_API",
            "execution_mode": "SYNC",
            "planning_cycle_id": "2025_W42_POR",
        },
        headers={"X-API-Key": "rbt_pdh_demo_key_2025"},
    )
    assert blocked.status_code == 422
    assert blocked.json()["gate_decision"] == "BLOCKED_DOWNSTREAM"
    assert blocked.json()["allow_downstream"] is False

    # SYNC clean on 2025_W43_POR -> HTTP 200
    passed = client.post(
        "/api/v1/execution/run-gate",
        json={
            "team_id": "team_ip_supply",
            "domain_id": "dom_intersite_bom",
            "triggered_by": "ORCHESTRATOR_API",
            "execution_mode": "SYNC",
            "planning_cycle_id": "2025_W43_POR",
        },
        headers={"X-API-Key": "rbt_pdh_demo_key_2025"},
    )
    assert passed.status_code == 200
    assert passed.json()["gate_decision"] == "PASS"
    assert passed.json()["allow_downstream"] is True

    # ASYNC mode -> HTTP 202 + poll_url
    async_resp = client.post(
        "/api/v1/execution/run-gate",
        json={
            "team_id": "team_ip_supply",
            "domain_id": "dom_intersite_bom",
            "triggered_by": "CI_CD",
            "execution_mode": "ASYNC",
            "planning_cycle_id": "2025_W42_POR",
        },
        headers={"X-API-Key": "rbt_pdh_demo_key_2025"},
    )
    assert async_resp.status_code == 202
    poll_url = async_resp.json()["poll_url"]

    polled = client.get(poll_url, headers={"X-API-Key": "rbt_pdh_demo_key_2025"})
    assert polled.status_code == 200
    assert polled.json()["run_id"] == async_resp.json()["run_id"]


def test_csv_export_and_break_glass_override_on_seeded_run(client: TestClient):
    # Pre-seeded PDH blocked run: run_9f82c1a0 / det_001
    csv_resp = client.get(
        "/api/v1/runs/run_9f82c1a0/details/det_001/export-csv",
        headers=AUTHOR_HEADERS,
    )
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    lines = csv_resp.text.strip().splitlines()
    assert len(lines) == 26  # header + 25 seeded transient rows

    # AUTHOR cannot override
    denied = client.post(
        "/api/v1/runs/run_9f82c1a0/override",
        json={"override_reason": "Approved by PDH Release Lead for emergency weekly POR cutoff."},
        headers=AUTHOR_HEADERS,
    )
    assert denied.status_code == 403

    # Reason < 20 chars rejected
    short_reason = client.post(
        "/api/v1/runs/run_9f82c1a0/override",
        json={"override_reason": "Too short"},
        headers=APPROVER_HEADERS,
    )
    assert short_reason.status_code == 422

    # APPROVER override succeeds
    ok = client.post(
        "/api/v1/runs/run_9f82c1a0/override",
        json={"override_reason": "Approved by PDH Release Lead: Fab-AZ loopback rows are quarantined in downstream solver filter for 2025_W42_POR."},
        headers=APPROVER_HEADERS,
    )
    assert ok.status_code == 200
    assert ok.json()["gate_decision"] == "OVERRIDDEN_PASS"
    assert ok.json()["allow_downstream"] is True
    assert ok.json()["override_by"] == "pdh.lead@enterprise.com"


def test_auto_debug_max_retries_fallback(monkeypatch):
    # Force compile_check_sql to always fail to verify MAX_RETRIES=3 -> DRAFT_NEEDS_REVIEW
    from app.api.ai_generator import compile_and_auto_debug_rule
    from app.schemas.models import GroundingContext

    calls = {"cortex": 0}

    def fake_cortex(**kwargs):
        calls["cortex"] += 1
        return {
            "generated_rules": [
                {
                    "risk_name": "Broken",
                    "risk_category": "BOUNDARY_VALUE",
                    "risk_priority": "P2_WARNING",
                    "business_impact_desc": "Test",
                    "validation_sql": "SELECT BAD_COL FROM __TARGET_DATASET__",
                }
            ]
        }

    monkeypatch.setattr(sf_client, "compile_check_sql", lambda sql, t, d: (False, "SQL compilation error: invalid identifier 'BAD_COL'"))
    monkeypatch.setattr(sf_client, "invoke_cortex_structured", fake_cortex)

    res = compile_and_auto_debug_rule(
        {"risk_name": "Broken", "risk_category": "BOUNDARY_VALUE", "risk_priority": "P2_WARNING", "business_impact_desc": "Test", "validation_sql": "SELECT BAD_COL FROM __TARGET_DATASET__"},
        {"SNOWFLAKE_WAREHOUSE": "PDH_RBT_WH"},
        {"MAX_HIERARCHY_DEPTH": 10},
        GroundingContext(),
    )
    assert calls["cortex"] == 3  # strictly capped at MAX_RETRIES = 3
    assert res["status"] == "DRAFT_NEEDS_REVIEW"
    assert "BAD_COL" in res["last_compile_error"]
