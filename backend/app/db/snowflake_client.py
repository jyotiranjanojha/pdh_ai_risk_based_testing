import asyncio
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import sqlglot
from fastapi import HTTPException

from app.auth.dependencies import auth_lookup, hash_api_key
from app.config import settings
from app.schemas.models import (
    CORTEX_REMAP_SCHEMA,
    CORTEX_RULE_LIST_SCHEMA,
    GroundingColumnInfo,
    GroundingConstraintInfo,
    GroundingContext,
    GroundingStatProfile,
    UserRole,
    map_risk_score_to_priority,
)
from app.security.sql_validator import sanitize_sql_identifier, wrap_with_target_dataset_cte


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class SnowflakePlatformClient:
    """
    Dual-mode database, metadata, Cortex AI, and execution client.
    - SNOWFLAKE_MOCK_MODE=true: SQLite-backed stateful engine with real SQL execution via sqlglot transpilation.
    - SNOWFLAKE_MOCK_MODE=false: Live snowflake-connector-python + Snowflake Cortex AI.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._sso_conn: Any = None
        self._pending_sso_url: Optional[str] = None
        self._sso_connecting: bool = False
        if settings.SNOWFLAKE_MOCK_MODE:
            db_path = settings.MOCK_DB_PATH or ":memory:"
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._init_mock_schema_and_seed()
        self._register_auth_hooks()

    @property
    def _meta_prefix(self) -> str:
        return f"{settings.SNOWFLAKE_DATABASE}.{settings.SNOWFLAKE_SCHEMA}"

    def get_sso_status(self) -> Dict[str, Any]:
        if settings.SNOWFLAKE_MOCK_MODE:
            return {"connected": True, "mock_mode": True, "authenticator": "mock", "sso_url": None}
        is_connected = self._sso_conn is not None and not self._sso_conn.is_closed()
        if settings.SNOWFLAKE_AUTHENTICATOR.lower() != "externalbrowser":
            is_connected = True
        return {
            "connected": is_connected,
            "mock_mode": False,
            "authenticator": settings.SNOWFLAKE_AUTHENTICATOR or "password/keypair",
            "sso_url": None if is_connected else self._pending_sso_url,
        }

    def start_sso_login(self) -> Dict[str, Any]:
        if settings.SNOWFLAKE_MOCK_MODE or settings.SNOWFLAKE_AUTHENTICATOR.lower() != "externalbrowser":
            return self.get_sso_status()
        if self._sso_conn is not None and not self._sso_conn.is_closed():
            return self.get_sso_status()

        if self._pending_sso_url and self._sso_connecting:
            return self.get_sso_status()

        url_ready = threading.Event()
        self._pending_sso_url = None
        self._sso_connecting = True

        def _bg_connect() -> None:
            import webbrowser
            import snowflake.connector

            orig_open = webbrowser.open

            def _capture_url(url: str, *a, **kw) -> bool:
                self._pending_sso_url = url
                url_ready.set()
                print("OPEN_THIS_SSO_URL:", url, flush=True)
                return True

            webbrowser.open = _capture_url
            try:
                conn = snowflake.connector.connect(
                    account=settings.SNOWFLAKE_ACCOUNT,
                    user=settings.SNOWFLAKE_USER,
                    authenticator="externalbrowser",
                    role=settings.SNOWFLAKE_ROLE,
                    database=settings.SNOWFLAKE_DATABASE,
                    schema=settings.SNOWFLAKE_SCHEMA,
                    warehouse=settings.SNOWFLAKE_WAREHOUSE,
                )
                cur = conn.cursor()
                try:
                    cur.execute(
                        f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {int(settings.STATEMENT_TIMEOUT_IN_SECONDS)}"
                    )
                finally:
                    cur.close()
                with self._lock:
                    self._sso_conn = conn
                    self._pending_sso_url = None
            except Exception as exc:
                print("SSO_CONNECT_ERROR:", exc, flush=True)
            finally:
                webbrowser.open = orig_open
                self._sso_connecting = False
                url_ready.set()

        t = threading.Thread(target=_bg_connect, daemon=True)
        t.start()
        url_ready.wait(timeout=8.0)
        return self.get_sso_status()

    def _register_auth_hooks(self) -> None:
        auth_lookup.get_member_role = self.get_member_role
        auth_lookup.get_team_by_api_key_hash = self.get_team_by_api_key_hash

    # -----------------------------------------------------------------------
    # Live Snowflake Connection Helpers (SNOWFLAKE_MOCK_MODE=false)
    # -----------------------------------------------------------------------

    def _get_live_connection(self, warehouse: Optional[str] = None):
        import webbrowser
        import snowflake.connector
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization

        target_wh = warehouse or settings.SNOWFLAKE_WAREHOUSE

        if settings.SNOWFLAKE_AUTHENTICATOR.lower() == "externalbrowser":
            with self._lock:
                if self._sso_conn is not None and not self._sso_conn.is_closed():
                    if target_wh:
                        cur = self._sso_conn.cursor()
                        try:
                            cur.execute(f"USE WAREHOUSE {target_wh}")
                        except Exception:
                            pass
                        finally:
                            cur.close()
                    return _NoCloseConnProxy(self._sso_conn)

            from fastapi import HTTPException

            raise HTTPException(
                status_code=503,
                detail="Snowflake SSO sign-in required. Click 'Snowflake SSO' in the top bar to authenticate.",
            )

        conn_kwargs: Dict[str, Any] = {
            "account": settings.SNOWFLAKE_ACCOUNT,
            "user": settings.SNOWFLAKE_USER,
            "role": settings.SNOWFLAKE_ROLE,
            "database": settings.SNOWFLAKE_DATABASE,
            "schema": settings.SNOWFLAKE_SCHEMA,
            "warehouse": target_wh,
        }

        if settings.SNOWFLAKE_PRIVATE_KEY_PATH:
            with open(settings.SNOWFLAKE_PRIVATE_KEY_PATH, "rb") as key_file:
                passphrase = (
                    settings.SNOWFLAKE_PRIVATE_KEY_PASSPHRASE.encode()
                    if settings.SNOWFLAKE_PRIVATE_KEY_PASSPHRASE
                    else None
                )
                p_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=passphrase,
                    backend=default_backend(),
                )
            conn_kwargs["private_key"] = p_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        elif settings.SNOWFLAKE_AUTHENTICATOR:
            conn_kwargs["authenticator"] = settings.SNOWFLAKE_AUTHENTICATOR
        else:
            conn_kwargs["password"] = settings.SNOWFLAKE_PASSWORD

        conn = snowflake.connector.connect(**conn_kwargs)
        cur = conn.cursor()
        try:
            cur.execute(
                f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {int(settings.STATEMENT_TIMEOUT_IN_SECONDS)}"
            )
        finally:
            cur.close()
        return conn

    # -----------------------------------------------------------------------
    # Mock SQLite Initialization & Pre-Seeded Multi-Tenant Data (NFR-4)
    # -----------------------------------------------------------------------

    def _init_mock_schema_and_seed(self) -> None:
        assert self._conn is not None
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS RBT_TEAMS (
                    TEAM_ID TEXT PRIMARY KEY,
                    TEAM_NAME TEXT NOT NULL,
                    SNOWFLAKE_DATABASE TEXT NOT NULL,
                    SNOWFLAKE_WAREHOUSE TEXT NOT NULL,
                    DEFAULT_DOWNSTREAM_TARGET TEXT,
                    ORCHESTRATOR_API_KEY_HASH TEXT,
                    ALERT_WEBHOOK_URL TEXT,
                    CREATED_TS TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS RBT_TEAM_MEMBERS (
                    MEMBER_ID TEXT PRIMARY KEY,
                    TEAM_ID TEXT NOT NULL,
                    USER_EMAIL TEXT NOT NULL,
                    USER_ROLE TEXT NOT NULL,
                    CREATED_TS TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS RBT_DOMAIN_REGISTRY (
                    DOMAIN_ID TEXT PRIMARY KEY,
                    TEAM_ID TEXT NOT NULL,
                    DOMAIN_NAME TEXT NOT NULL,
                    TARGET_SCHEMA TEXT NOT NULL,
                    TARGET_TABLE TEXT NOT NULL,
                    RELATED_TABLES TEXT,
                    TEMPORAL_CYCLE_COLUMN TEXT,
                    SNAPSHOT_TS_COLUMN TEXT,
                    MAX_HIERARCHY_DEPTH INTEGER DEFAULT 10,
                    DOWNSTREAM_CONSUMER TEXT,
                    BUSINESS_CONTEXT TEXT,
                    CRITICALITY TEXT DEFAULT 'HIGH',
                    BASELINE_SCHEMA_JSON TEXT,
                    UPSTREAM_SOURCE_TABLE TEXT,
                    TRANSFORMATION_SQL TEXT,
                    IS_ACTIVE INTEGER DEFAULT 1,
                    CREATED_TS TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS RBT_TEST_RULES (
                    RULE_ID TEXT PRIMARY KEY,
                    TEAM_ID TEXT NOT NULL,
                    DOMAIN_ID TEXT NOT NULL,
                    RISK_NAME TEXT NOT NULL,
                    RISK_CATEGORY TEXT,
                    RISK_PRIORITY TEXT NOT NULL,
                    LIKELIHOOD_SCORE INTEGER DEFAULT 3,
                    IMPACT_SCORE INTEGER DEFAULT 3,
                    RISK_SCORE INTEGER DEFAULT 9,
                    BUSINESS_IMPACT_DESC TEXT,
                    VALIDATION_SQL TEXT NOT NULL,
                    STATUS TEXT DEFAULT 'DRAFT',
                    LAST_COMPILE_ERROR TEXT,
                    IS_SHARED_TEMPLATE INTEGER DEFAULT 0,
                    CREATED_BY TEXT,
                    APPROVED_BY TEXT,
                    UPDATED_TS TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS RBT_TEST_RULE_HISTORY (
                    HISTORY_ID TEXT PRIMARY KEY,
                    RULE_ID TEXT NOT NULL,
                    VERSION_NUM INTEGER NOT NULL,
                    STATUS_OLD TEXT,
                    STATUS_NEW TEXT,
                    VALIDATION_SQL_OLD TEXT,
                    VALIDATION_SQL_NEW TEXT,
                    MODIFIED_BY TEXT NOT NULL,
                    MODIFIED_TS TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS RBT_EXECUTION_RUNS (
                    RUN_ID TEXT PRIMARY KEY,
                    TEAM_ID TEXT NOT NULL,
                    DOMAIN_ID TEXT NOT NULL,
                    TRIGGERED_BY TEXT,
                    EXECUTION_STATUS TEXT DEFAULT 'RUNNING',
                    PLANNING_CYCLE_ID TEXT,
                    SNAPSHOT_TS TEXT,
                    GATE_DECISION TEXT,
                    TOTAL_RULES_RUN INTEGER DEFAULT 0,
                    P1_FAILURES INTEGER DEFAULT 0,
                    P2_FAILURES INTEGER DEFAULT 0,
                    ENVIRONMENT TEXT DEFAULT 'PROD',
                    TARGET_SCHEMA_OVERRIDE TEXT,
                    COVERAGE_PCT REAL,
                    RELEASE_CONFIDENCE_SCORE REAL,
                    SCHEMA_DRIFT_STATUS TEXT DEFAULT 'PASS',
                    SCHEMA_DRIFT_DETAILS TEXT,
                    AI_TRIAGE_SUMMARY TEXT,
                    OVERRIDE_BY TEXT,
                    OVERRIDE_REASON TEXT,
                    OVERRIDE_TS TEXT,
                    RUN_TS TEXT NOT NULL,
                    COMPLETED_TS TEXT
                );

                CREATE TABLE IF NOT EXISTS RBT_EXECUTION_DETAILS (
                    DETAIL_ID TEXT PRIMARY KEY,
                    RUN_ID TEXT NOT NULL,
                    RULE_ID TEXT NOT NULL,
                    RISK_NAME TEXT,
                    RISK_PRIORITY TEXT,
                    RULE_RUN_STATUS TEXT,
                    FAILED_ROW_COUNT INTEGER DEFAULT 0,
                    SAMPLE_FAILING_KEYS TEXT,
                    FAILURE_TABLE_REF TEXT,
                    ERROR_MESSAGE TEXT,
                    EXECUTION_MS INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS MOCK_TRANSIENT_FAILURES (
                    TABLE_REF TEXT NOT NULL,
                    ROW_JSON TEXT NOT NULL
                );

                -- Realistic Domain Data Tables for Mock SQL Execution
                CREATE TABLE IF NOT EXISTS INTERSITE_BOM (
                    ITEM_ID TEXT,
                    SITE_ID TEXT,
                    PARENT_SITE_ID TEXT,
                    BOM_QTY REAL,
                    FROZEN_HORIZON_FLAG INTEGER,
                    PLANNING_CYCLE_ID TEXT,
                    SNAPSHOT_TS TEXT,
                    UPDATE_TS TEXT,
                    DELETE_FLAG INTEGER
                );

                CREATE TABLE IF NOT EXISTS WEEKLY_POR_NETTING (
                    ITEM_ID TEXT,
                    SITE_ID TEXT,
                    DEMAND_QTY REAL,
                    SUPPLY_QTY REAL,
                    NET_DEMAND_QTY REAL,
                    FROZEN_WEEK_OFFSET INTEGER,
                    PLANNING_CYCLE_ID TEXT,
                    SNAPSHOT_TS TEXT,
                    UPDATE_TS TEXT
                );

                CREATE TABLE IF NOT EXISTS GL_JOURNAL_POSTINGS (
                    JOURNAL_ID TEXT,
                    COST_CENTER_ID TEXT,
                    DEBIT_AMT REAL,
                    CREDIT_AMT REAL,
                    FISCAL_PERIOD TEXT,
                    UPDATE_TS TEXT
                );

                CREATE TABLE IF NOT EXISTS ACCOUNT_HIERARCHY (
                    ACCOUNT_ID TEXT,
                    PARENT_ACCOUNT_ID TEXT,
                    DUNS_NUMBER TEXT,
                    ACCOUNT_NAME TEXT,
                    UPDATE_TS TEXT
                );

                CREATE TABLE IF NOT EXISTS ITEM_MASTER (
                    ITEM_ID TEXT PRIMARY KEY,
                    ITEM_DESC TEXT
                );

                CREATE TABLE IF NOT EXISTS SITE_MASTER (
                    SITE_ID TEXT PRIMARY KEY,
                    REGION TEXT
                );
                """
            )

            existing = cur.execute("SELECT COUNT(*) FROM RBT_TEAMS").fetchone()[0]
            if existing > 0:
                return

            now = _utcnow_iso()
            ip_sup_hash = hash_api_key("rbt_pdh_demo_key_2025")
            ip_dem_hash = hash_api_key("rbt_ip_demand_key_2025")
            ip_mat_hash = hash_api_key("rbt_ip_material_key_2025")
            if_sup_hash = hash_api_key("rbt_if_supply_key_2025")
            if_dem_hash = hash_api_key("rbt_if_demand_key_2025")
            if_mat_hash = hash_api_key("rbt_if_material_key_2025")

            # Seed 6 Enterprise Planning Data Hubs (IP & IF)
            cur.executemany(
                "INSERT INTO RBT_TEAMS VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("team_ip_supply", "IP Supply Planning Data Hub", "IP_SUPPLY_PDH_DB", "IP_SUPPLY_RBT_WH", "PLANNING_SOLVER", ip_sup_hash, "https://hooks.slack.com/services/T000/IP-SUP/ALERT", now),
                    ("team_ip_demand", "IP Demand Planning Data Hub", "IP_DEMAND_PDH_DB", "IP_DEMAND_RBT_WH", "DEMAND_FORECAST_ENGINE", ip_dem_hash, "https://hooks.slack.com/services/T000/IP-DEM/ALERT", now),
                    ("team_ip_material", "IP Material Planning Data Hub", "IP_MATERIAL_PDH_DB", "IP_MATERIAL_RBT_WH", "MRP_ENGINE", ip_mat_hash, "https://hooks.slack.com/services/T000/IP-MAT/ALERT", now),
                    ("team_if_supply", "IF Supply Planning Data Hub", "IF_SUPPLY_PDH_DB", "IF_SUPPLY_RBT_WH", "FOUNDRY_SOLVER", if_sup_hash, "https://hooks.slack.com/services/T000/IF-SUP/ALERT", now),
                    ("team_if_demand", "IF Demand Planning Data Hub", "IF_DEMAND_PDH_DB", "IF_DEMAND_RBT_WH", "FOUNDRY_DEMAND_MART", if_dem_hash, "https://hooks.slack.com/services/T000/IF-DEM/ALERT", now),
                    ("team_if_material", "IF Material Planning Data Hub", "IF_MATERIAL_PDH_DB", "IF_MATERIAL_RBT_WH", "FOUNDRY_MRP", if_mat_hash, "https://hooks.slack.com/services/T000/IF-MAT/ALERT", now),
                ],
            )

            # Seed RBAC Members across all 6 Data Hubs
            cur.executemany(
                "INSERT INTO RBT_TEAM_MEMBERS VALUES (?, ?, ?, ?, ?)",
                [
                    ("mem_01", "team_ip_supply", "pdh.author@enterprise.com", "AUTHOR", now),
                    ("mem_02", "team_ip_supply", "pdh.lead@enterprise.com", "APPROVER", now),
                    ("mem_03", "team_ip_supply", "pdh.admin@enterprise.com", "ADMIN", now),
                    ("mem_04", "team_ip_demand", "pdh.admin@enterprise.com", "ADMIN", now),
                    ("mem_05", "team_ip_material", "pdh.admin@enterprise.com", "ADMIN", now),
                    ("mem_06", "team_if_supply", "pdh.admin@enterprise.com", "ADMIN", now),
                    ("mem_07", "team_if_demand", "pdh.admin@enterprise.com", "ADMIN", now),
                    ("mem_08", "team_if_material", "pdh.admin@enterprise.com", "ADMIN", now),
                ],
            )

            # Seed Domains across all 6 Data Hubs
            bom_baseline = json.dumps([
                {"column_name": "ITEM_ID", "data_type": "TEXT", "numeric_precision": None, "numeric_scale": None, "is_nullable": True},
                {"column_name": "SITE_ID", "data_type": "TEXT", "numeric_precision": None, "numeric_scale": None, "is_nullable": True},
                {"column_name": "PARENT_SITE_ID", "data_type": "TEXT", "numeric_precision": None, "numeric_scale": None, "is_nullable": True},
                {"column_name": "BOM_QTY", "data_type": "REAL", "numeric_precision": None, "numeric_scale": None, "is_nullable": True},
                {"column_name": "FROZEN_HORIZON_FLAG", "data_type": "INTEGER", "numeric_precision": None, "numeric_scale": None, "is_nullable": True},
                {"column_name": "PLANNING_CYCLE_ID", "data_type": "TEXT", "numeric_precision": None, "numeric_scale": None, "is_nullable": True},
                {"column_name": "SNAPSHOT_TS", "data_type": "TEXT", "numeric_precision": None, "numeric_scale": None, "is_nullable": True},
                {"column_name": "UPDATE_TS", "data_type": "TEXT", "numeric_precision": None, "numeric_scale": None, "is_nullable": True},
                {"column_name": "DELETE_FLAG", "data_type": "INTEGER", "numeric_precision": None, "numeric_scale": None, "is_nullable": True},
            ])
            cur.executemany(
                """
                INSERT INTO RBT_DOMAIN_REGISTRY
                (DOMAIN_ID, TEAM_ID, DOMAIN_NAME, TARGET_SCHEMA, TARGET_TABLE, RELATED_TABLES,
                 TEMPORAL_CYCLE_COLUMN, SNAPSHOT_TS_COLUMN, MAX_HIERARCHY_DEPTH, DOWNSTREAM_CONSUMER,
                 BUSINESS_CONTEXT, CRITICALITY, BASELINE_SCHEMA_JSON, UPSTREAM_SOURCE_TABLE, TRANSFORMATION_SQL, IS_ACTIVE, CREATED_TS)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "dom_intersite_bom",
                        "team_ip_supply",
                        "Intersite BOM",
                        "SUPPLY_CHAIN",
                        "INTERSITE_BOM",
                        json.dumps(["SUPPLY_CHAIN.ITEM_MASTER", "SUPPLY_CHAIN.SITE_MASTER"]),
                        "PLANNING_CYCLE_ID",
                        "SNAPSHOT_TS",
                        10,
                        "PLANNING_SOLVER",
                        "Multi-echelon intersite bill-of-materials network for IP Supply Planning. Circular parent-child site loops or frozen-horizon overrides crash the weekly POR solver.",
                        "CRITICAL",
                        bom_baseline,
                        None,
                        None,
                        1,
                        now,
                    ),
                    (
                        "dom_demand_netting",
                        "team_ip_supply",
                        "Weekly POR Demand Netting",
                        "SUPPLY_CHAIN",
                        "WEEKLY_POR_NETTING",
                        json.dumps(["SUPPLY_CHAIN.SITE_MASTER"]),
                        "PLANNING_CYCLE_ID",
                        "SNAPSHOT_TS",
                        10,
                        "PLANNING_SOLVER",
                        "Weekly Plan of Record demand netting table. Negative net demand or frozen-week supply spikes violate planning stability.",
                        "CRITICAL",
                        None,
                        None,
                        None,
                        1,
                        now,
                    ),
                    (
                        "dom_ip_demand_fcst",
                        "team_ip_demand",
                        "IP Consensus Demand Forecast",
                        "DEMAND_PLANNING",
                        "WEEKLY_POR_NETTING",
                        json.dumps(["SUPPLY_CHAIN.SITE_MASTER"]),
                        "PLANNING_CYCLE_ID",
                        "SNAPSHOT_TS",
                        10,
                        "DEMAND_FORECAST_ENGINE",
                        "IP Demand Planning weekly consensus forecast & netting feed.",
                        "CRITICAL",
                        None,
                        None,
                        None,
                        1,
                        now,
                    ),
                    (
                        "dom_ip_mat_bom",
                        "team_ip_material",
                        "IP Material BOM Explosion",
                        "MATERIAL_PLANNING",
                        "INTERSITE_BOM",
                        json.dumps(["SUPPLY_CHAIN.ITEM_MASTER"]),
                        "PLANNING_CYCLE_ID",
                        "SNAPSHOT_TS",
                        10,
                        "MRP_ENGINE",
                        "IP Material Planning substrate & component BOM explosion.",
                        "HIGH",
                        None,
                        None,
                        None,
                        1,
                        now,
                    ),
                    (
                        "dom_if_sup_cap",
                        "team_if_supply",
                        "IF Foundry Wafer Supply Network",
                        "FOUNDRY_SUPPLY",
                        "INTERSITE_BOM",
                        json.dumps(["SUPPLY_CHAIN.SITE_MASTER"]),
                        "PLANNING_CYCLE_ID",
                        "SNAPSHOT_TS",
                        10,
                        "FOUNDRY_SOLVER",
                        "Intel Foundry multi-fab wafer supply & intersite transfer network.",
                        "CRITICAL",
                        None,
                        None,
                        None,
                        1,
                        now,
                    ),
                    (
                        "dom_if_dem_por",
                        "team_if_demand",
                        "IF Foundry POR Demand Netting",
                        "FOUNDRY_DEMAND",
                        "WEEKLY_POR_NETTING",
                        json.dumps([]),
                        "PLANNING_CYCLE_ID",
                        "SNAPSHOT_TS",
                        10,
                        "FOUNDRY_DEMAND_MART",
                        "Intel Foundry customer POR demand netting and backlog reconciliation.",
                        "HIGH",
                        None,
                        None,
                        None,
                        1,
                        now,
                    ),
                    (
                        "dom_if_mat_sub",
                        "team_if_material",
                        "IF Foundry Material Allocation",
                        "FOUNDRY_MATERIAL",
                        "INTERSITE_BOM",
                        json.dumps(["SUPPLY_CHAIN.ITEM_MASTER"]),
                        "PLANNING_CYCLE_ID",
                        "SNAPSHOT_TS",
                        8,
                        "FOUNDRY_MRP",
                        "Intel Foundry raw wafer and mask-set material allocation.",
                        "HIGH",
                        None,
                        None,
                        None,
                        1,
                        now,
                    ),
                ],
            )

            # Seed Rules
            bom_loop_sql = (
                "SELECT ITEM_ID, SITE_ID, PARENT_SITE_ID, PLANNING_CYCLE_ID\n"
                "FROM __TARGET_DATASET__\n"
                "WHERE SITE_ID = PARENT_SITE_ID AND DELETE_FLAG = 0"
            )
            bom_frozen_sql = (
                "SELECT ITEM_ID, SITE_ID, FROZEN_HORIZON_FLAG, UPDATE_TS\n"
                "FROM __TARGET_DATASET__\n"
                "WHERE FROZEN_HORIZON_FLAG = 1 AND BOM_QTY <= 0"
            )
            bom_dup_sql = (
                "SELECT ITEM_ID, SITE_ID, PARENT_SITE_ID, COUNT(*) AS DUP_CNT\n"
                "FROM __TARGET_DATASET__\n"
                "GROUP BY ITEM_ID, SITE_ID, PARENT_SITE_ID\n"
                "HAVING COUNT(*) > 1"
            )
            netting_neg_sql = (
                "SELECT ITEM_ID, SITE_ID, NET_DEMAND_QTY, PLANNING_CYCLE_ID\n"
                "FROM __TARGET_DATASET__\n"
                "WHERE NET_DEMAND_QTY < 0"
            )
            bom_draft_sql = (
                "SELECT ITEM_ID, SITE_ID\n"
                "FROM __TARGET_DATASET__\n"
                "WHERE SITE_ID IS NULL OR ITEM_ID IS NULL"
            )
            fin_bal_sql = (
                "SELECT JOURNAL_ID, SUM(DEBIT_AMT) AS TOTAL_DEBIT, SUM(CREDIT_AMT) AS TOTAL_CREDIT\n"
                "FROM __TARGET_DATASET__\n"
                "GROUP BY JOURNAL_ID\n"
                "HAVING ABS(SUM(DEBIT_AMT) - SUM(CREDIT_AMT)) > 0.01"
            )
            cust_dup_sql = (
                "SELECT DUNS_NUMBER, COUNT(*) AS CNT\n"
                "FROM __TARGET_DATASET__\n"
                "GROUP BY DUNS_NUMBER\n"
                "HAVING COUNT(*) > 1"
            )
            cust_loop_sql = (
                "SELECT ACCOUNT_ID, PARENT_ACCOUNT_ID\n"
                "FROM __TARGET_DATASET__\n"
                "WHERE ACCOUNT_ID = PARENT_ACCOUNT_ID"
            )

            cur.executemany(
                """
                INSERT INTO RBT_TEST_RULES
                (RULE_ID, TEAM_ID, DOMAIN_ID, RISK_NAME, RISK_CATEGORY, RISK_PRIORITY,
                 LIKELIHOOD_SCORE, IMPACT_SCORE, RISK_SCORE, BUSINESS_IMPACT_DESC, VALIDATION_SQL,
                 STATUS, LAST_COMPILE_ERROR, IS_SHARED_TEMPLATE, CREATED_BY, APPROVED_BY, UPDATED_TS)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("rule_bom_loop_01", "team_ip_supply", "dom_intersite_bom", "Intersite BOM Circular Hierarchy Loop", "HIERARCHY_LOOP", "P1_BLOCKER", 4, 5, 20, "Self-referencing or circular parent-child site links cause infinite traversal in the Planning Solver.", bom_loop_sql, "APPROVED", None, 0, "pdh.author@enterprise.com", "pdh.lead@enterprise.com", now),
                    ("rule_bom_frozen_02", "team_ip_supply", "dom_intersite_bom", "Frozen Horizon Modification Violation", "FROZEN_HORIZON_VIOLATION", "P1_BLOCKER", 4, 5, 20, "Zero or negative BOM quantities inside the frozen planning horizon corrupt committed factory schedules.", bom_frozen_sql, "APPROVED", None, 0, "pdh.author@enterprise.com", "pdh.lead@enterprise.com", now),
                    ("rule_bom_dup_03", "team_ip_supply", "dom_intersite_bom", "Duplicate Intersite BOM Natural Key", "DUPLICATE_KEY", "P2_WARNING", 3, 4, 12, "Duplicate (ITEM_ID, SITE_ID, PARENT_SITE_ID) rows cause double-counting in capacity rollups.", bom_dup_sql, "APPROVED", None, 0, "pdh.author@enterprise.com", "pdh.lead@enterprise.com", now),
                    ("rule_netting_neg_04", "team_ip_supply", "dom_demand_netting", "Negative Net Demand Boundary Violation", "BOUNDARY_VALUE", "P1_BLOCKER", 4, 4, 16, "Negative net demand violates solver non-negativity constraints.", netting_neg_sql, "APPROVED", None, 0, "pdh.author@enterprise.com", "pdh.lead@enterprise.com", now),
                    ("rule_bom_draft_05", "team_ip_supply", "dom_intersite_bom", "Null Item or Site Key Check", "REFERENTIAL_INTEGRITY", "P2_WARNING", 3, 3, 9, "Missing ITEM_ID or SITE_ID prevents downstream ERP join.", bom_draft_sql, "DRAFT", None, 0, "pdh.author@enterprise.com", None, now),
                    ("rule_ip_dem_01", "team_ip_demand", "dom_ip_demand_fcst", "Negative Consensus Net Demand Check", "BOUNDARY_VALUE", "P1_BLOCKER", 4, 5, 20, "Ensures consensus demand netting never goes negative before forecast engine consumption.", netting_neg_sql, "APPROVED", None, 1, "pdh.admin@enterprise.com", "pdh.lead@enterprise.com", now),
                    ("rule_ip_mat_01", "team_ip_material", "dom_ip_mat_bom", "Material BOM Circular Component Loop", "HIERARCHY_LOOP", "P1_BLOCKER", 4, 5, 20, "Detects circular parent-child component links in MRP explosion.", bom_loop_sql, "APPROVED", None, 0, "pdh.admin@enterprise.com", "pdh.lead@enterprise.com", now),
                    ("rule_if_sup_01", "team_if_supply", "dom_if_sup_cap", "Foundry Intersite Supply Loop Guard", "HIERARCHY_LOOP", "P1_BLOCKER", 5, 5, 25, "Prevents circular fab-to-fab wafer transfer loops in Foundry Solver.", bom_loop_sql, "APPROVED", None, 0, "pdh.admin@enterprise.com", "pdh.lead@enterprise.com", now),
                    ("rule_if_dem_01", "team_if_demand", "dom_if_dem_por", "Foundry POR Negative Demand Guard", "BOUNDARY_VALUE", "P1_BLOCKER", 4, 4, 16, "Blocks negative POR demand rows from reaching Foundry Demand Mart.", netting_neg_sql, "APPROVED", None, 0, "pdh.admin@enterprise.com", "pdh.lead@enterprise.com", now),
                    ("rule_if_mat_01", "team_if_material", "dom_if_mat_sub", "Foundry Duplicate Material Allocation Key", "DUPLICATE_KEY", "P1_BLOCKER", 4, 4, 16, "Duplicate (ITEM_ID, SITE_ID, PARENT_SITE_ID) allocation keys break Foundry MRP.", bom_dup_sql, "APPROVED", None, 1, "pdh.admin@enterprise.com", "pdh.lead@enterprise.com", now),
                ],
            )

            # Seed Rule History
            cur.executemany(
                "INSERT INTO RBT_TEST_RULE_HISTORY VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("hist_01", "rule_bom_loop_01", 1, "DRAFT", "APPROVED", bom_loop_sql, bom_loop_sql, "pdh.lead@enterprise.com", now),
                    ("hist_02", "rule_bom_frozen_02", 1, "DRAFT", "APPROVED", bom_frozen_sql, bom_frozen_sql, "pdh.lead@enterprise.com", now),
                ],
            )

            # Seed Master & Domain Data Rows (so live SQL dry-runs & gates return real rows)
            cur.executemany("INSERT INTO ITEM_MASTER VALUES (?, ?)", [("SKU-9921", "Wafer Die X9"), ("SKU-4410", "Substrate Pkg B2"), ("SKU-1001", "Controller IC")])
            cur.executemany("INSERT INTO SITE_MASTER VALUES (?, ?)", [("FAB-AZ", "US-WEST"), ("FAB-OR", "US-WEST"), ("ASM-MY", "APAC")])

            bom_rows = [
                # Failing loop rows for 2025_W42_POR
                ("SKU-9921", "FAB-AZ", "FAB-AZ", 120.0, 0, "2025_W42_POR", "2025-10-15T06:00:00Z", now, 0),
                ("SKU-4410", "FAB-OR", "FAB-OR", 85.0, 0, "2025_W42_POR", "2025-10-15T06:00:00Z", now, 0),
                # Duplicate key warning rows
                ("SKU-1001", "ASM-MY", "FAB-AZ", 50.0, 0, "2025_W42_POR", "2025-10-15T06:00:00Z", now, 0),
                ("SKU-1001", "ASM-MY", "FAB-AZ", 50.0, 0, "2025_W42_POR", "2025-10-15T06:00:00Z", now, 0),
                # Clean cycle 2025_W43_POR
                ("SKU-9921", "ASM-MY", "FAB-AZ", 200.0, 0, "2025_W43_POR", "2025-10-22T06:00:00Z", now, 0),
            ]
            cur.executemany("INSERT INTO INTERSITE_BOM VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", bom_rows)

            cur.executemany(
                "INSERT INTO WEEKLY_POR_NETTING VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("SKU-9921", "FAB-AZ", 500.0, 500.0, 0.0, 2, "2025_W42_POR", "2025-10-15T06:00:00Z", now),
                    ("SKU-4410", "ASM-MY", 300.0, 350.0, 50.0, 4, "2025_W42_POR", "2025-10-15T06:00:00Z", now),
                ],
            )

            cur.executemany(
                "INSERT INTO GL_JOURNAL_POSTINGS VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("JRN-1001", "CC-400", 12500.0, 12500.0, "2025-10", now),
                    ("JRN-1002", "CC-500", 8000.0, 8000.0, "2025-10", now),
                ],
            )

            cur.executemany(
                "INSERT INTO ACCOUNT_HIERARCHY VALUES (?, ?, ?, ?, ?)",
                [
                    ("ACC-01", None, "11-222-3333", "Acme Global", now),
                    ("ACC-02", "ACC-01", "44-555-6666", "Acme EMEA", now),
                ],
            )

            # Seed Pre-Existing BLOCKED_DOWNSTREAM Run (ready for CSV export & Break-Glass Override)
            cur.execute(
                """
                INSERT INTO RBT_EXECUTION_RUNS
                (RUN_ID, TEAM_ID, DOMAIN_ID, TRIGGERED_BY, EXECUTION_STATUS, PLANNING_CYCLE_ID, SNAPSHOT_TS,
                 GATE_DECISION, TOTAL_RULES_RUN, P1_FAILURES, P2_FAILURES, ENVIRONMENT, TARGET_SCHEMA_OVERRIDE,
                 COVERAGE_PCT, RELEASE_CONFIDENCE_SCORE, SCHEMA_DRIFT_STATUS, SCHEMA_DRIFT_DETAILS,
                 AI_TRIAGE_SUMMARY, OVERRIDE_BY, OVERRIDE_REASON, OVERRIDE_TS, RUN_TS, COMPLETED_TS)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "run_9f82c1a0",
                    "team_ip_supply",
                    "dom_intersite_bom",
                    "ORCHESTRATOR_API",
                    "COMPLETED",
                    "2025_W42_POR",
                    "2025-10-15T06:00:00Z",
                    "BLOCKED_DOWNSTREAM",
                    3,
                    1,
                    1,
                    "PROD",
                    None,
                    66.7,
                    48.3,
                    "PASS",
                    "[]",
                    "Downstream solver export blocked: 142 circular parent-child loops detected in Intersite BOM for cycle 2025_W42_POR, which would cause infinite netting traversal.",
                    None,
                    None,
                    None,
                    now,
                    now,
                ),
            )

            sample_keys = [
                {"ITEM_ID": f"SKU-{9921 + i}", "SITE_ID": "FAB-AZ", "PARENT_SITE_ID": "FAB-AZ", "PLANNING_CYCLE_ID": "2025_W42_POR"}
                for i in range(5)
            ]
            failure_ref = "RBT_GLOBAL_PLATFORM.FAILURES_TRANSIENT.FAIL_det_001"
            cur.executemany(
                "INSERT INTO RBT_EXECUTION_DETAILS VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("det_001", "run_9f82c1a0", "rule_bom_loop_01", "Intersite BOM Circular Hierarchy Loop", "P1_BLOCKER", "FAIL", 142, json.dumps(sample_keys), failure_ref, None, 1940),
                    ("det_002", "run_9f82c1a0", "rule_bom_frozen_02", "Frozen Horizon Modification Violation", "P1_BLOCKER", "PASS", 0, json.dumps([]), None, None, 620),
                    ("det_003", "run_9f82c1a0", "rule_bom_dup_03", "Duplicate Intersite BOM Natural Key", "P2_WARNING", "FAIL", 2, json.dumps([{"ITEM_ID": "SKU-1001", "SITE_ID": "ASM-MY", "PARENT_SITE_ID": "FAB-AZ", "DUP_CNT": 2}]), None, None, 410),
                ],
            )

            # Populate 25 sample transient failure rows for CSV export testing
            transient_rows = [
                (
                    failure_ref,
                    json.dumps({
                        "ITEM_ID": f"SKU-{9900 + i}",
                        "SITE_ID": "FAB-AZ" if i % 2 == 0 else "FAB-OR",
                        "PARENT_SITE_ID": "FAB-AZ" if i % 2 == 0 else "FAB-OR",
                        "PLANNING_CYCLE_ID": "2025_W42_POR",
                    }),
                )
                for i in range(1, 26)
            ]
            cur.executemany("INSERT INTO MOCK_TRANSIENT_FAILURES VALUES (?, ?)", transient_rows)
            self._conn.commit()

    # -----------------------------------------------------------------------
    # Generic Query / Execution Helpers
    # -----------------------------------------------------------------------

    def _mock_fetchall(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        assert self._conn is not None
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def _mock_execute(self, sql: str, params: Tuple[Any, ...] = ()) -> None:
        assert self._conn is not None
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            self._conn.commit()

    # -----------------------------------------------------------------------
    # Auth Lookups
    # -----------------------------------------------------------------------

    def get_member_role(self, team_id: str, user_email: str) -> Optional[UserRole]:
        if settings.SNOWFLAKE_MOCK_MODE:
            rows = self._mock_fetchall(
                "SELECT USER_ROLE FROM RBT_TEAM_MEMBERS WHERE TEAM_ID = ? AND LOWER(USER_EMAIL) = LOWER(?)",
                (team_id, user_email),
            )
            return rows[0]["USER_ROLE"] if rows else None

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT USER_ROLE FROM {self._meta_prefix}.RBT_TEAM_MEMBERS WHERE TEAM_ID = %s AND LOWER(USER_EMAIL) = LOWER(%s)",
                (team_id, user_email),
            )
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def get_team_by_api_key_hash(self, key_hash: str) -> Optional[str]:
        if settings.SNOWFLAKE_MOCK_MODE:
            rows = self._mock_fetchall(
                "SELECT TEAM_ID FROM RBT_TEAMS WHERE ORCHESTRATOR_API_KEY_HASH = ?",
                (key_hash,),
            )
            return rows[0]["TEAM_ID"] if rows else None

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT TEAM_ID FROM {self._meta_prefix}.RBT_TEAMS WHERE ORCHESTRATOR_API_KEY_HASH = %s",
                (key_hash,),
            )
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Teams CRUD
    # -----------------------------------------------------------------------

    def list_teams(self) -> List[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            return self._mock_fetchall("SELECT * FROM RBT_TEAMS ORDER BY CREATED_TS ASC")
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT TEAM_ID, TEAM_NAME, SNOWFLAKE_DATABASE, SNOWFLAKE_WAREHOUSE, DEFAULT_DOWNSTREAM_TARGET, ORCHESTRATOR_API_KEY_HASH, ALERT_WEBHOOK_URL, CREATED_TS FROM {self._meta_prefix}.RBT_TEAMS ORDER BY CREATED_TS ASC")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            rows = self._mock_fetchall("SELECT * FROM RBT_TEAMS WHERE TEAM_ID = ?", (team_id,))
            return rows[0] if rows else None
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM {self._meta_prefix}.RBT_TEAMS WHERE TEAM_ID = %s", (team_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        finally:
            conn.close()

    def create_team(self, data: Dict[str, Any]) -> Dict[str, Any]:
        team_id = _new_id("team")
        now = _utcnow_iso()
        if settings.SNOWFLAKE_MOCK_MODE:
            self._mock_execute(
                "INSERT INTO RBT_TEAMS VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    team_id,
                    data["team_name"],
                    data["snowflake_database"],
                    data["snowflake_warehouse"],
                    data.get("default_downstream_target"),
                    None,
                    data.get("alert_webhook_url"),
                    now,
                ),
            )
            return self.get_team(team_id)  # type: ignore[return-value]
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {self._meta_prefix}.RBT_TEAMS
                (TEAM_ID, TEAM_NAME, SNOWFLAKE_DATABASE, SNOWFLAKE_WAREHOUSE, DEFAULT_DOWNSTREAM_TARGET, ALERT_WEBHOOK_URL)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (team_id, data["team_name"], data["snowflake_database"], data["snowflake_warehouse"], data.get("default_downstream_target"), data.get("alert_webhook_url")),
            )
            return self.get_team(team_id)  # type: ignore[return-value]
        finally:
            conn.close()

    def update_team(self, team_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        col_map = {
            "team_name": "TEAM_NAME",
            "snowflake_database": "SNOWFLAKE_DATABASE",
            "snowflake_warehouse": "SNOWFLAKE_WAREHOUSE",
            "default_downstream_target": "DEFAULT_DOWNSTREAM_TARGET",
            "alert_webhook_url": "ALERT_WEBHOOK_URL",
        }
        items = [(col_map[k], updates[k]) for k in updates if k in col_map]
        if not items:
            return self.get_team(team_id)
        if settings.SNOWFLAKE_MOCK_MODE:
            set_clause = ", ".join(f"{col} = ?" for col, _ in items)
            vals = tuple(v for _, v in items) + (team_id,)
            self._mock_execute(f"UPDATE RBT_TEAMS SET {set_clause} WHERE TEAM_ID = ?", vals)
            return self.get_team(team_id)
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            set_clause = ", ".join(f"{col} = %s" for col, _ in items)
            vals = tuple(v for _, v in items) + (team_id,)
            cur.execute(f"UPDATE {self._meta_prefix}.RBT_TEAMS SET {set_clause} WHERE TEAM_ID = %s", vals)
            return self.get_team(team_id)
        finally:
            conn.close()

    def rotate_team_api_key(self, team_id: str, raw_key: str) -> None:
        key_hash = hash_api_key(raw_key)
        if settings.SNOWFLAKE_MOCK_MODE:
            self._mock_execute(
                "UPDATE RBT_TEAMS SET ORCHESTRATOR_API_KEY_HASH = ? WHERE TEAM_ID = ?",
                (key_hash, team_id),
            )
            return
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {self._meta_prefix}.RBT_TEAMS SET ORCHESTRATOR_API_KEY_HASH = %s WHERE TEAM_ID = %s",
                (key_hash, team_id),
            )
        finally:
            conn.close()

    def list_team_members(self, team_id: str) -> List[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            return self._mock_fetchall(
                "SELECT * FROM RBT_TEAM_MEMBERS WHERE TEAM_ID = ? ORDER BY CREATED_TS ASC",
                (team_id,),
            )
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM {self._meta_prefix}.RBT_TEAM_MEMBERS WHERE TEAM_ID = %s ORDER BY CREATED_TS ASC", (team_id,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def add_team_member(self, team_id: str, user_email: str, user_role: str) -> Dict[str, Any]:
        clean_email = user_email.strip().lower()
        now = _utcnow_iso()
        if settings.SNOWFLAKE_MOCK_MODE:
            existing = self._mock_fetchall(
                "SELECT * FROM RBT_TEAM_MEMBERS WHERE TEAM_ID = ? AND LOWER(USER_EMAIL) = LOWER(?)",
                (team_id, clean_email),
            )
            if existing:
                member_id = existing[0]["MEMBER_ID"]
                self._mock_execute(
                    "UPDATE RBT_TEAM_MEMBERS SET USER_ROLE = ? WHERE MEMBER_ID = ?",
                    (user_role, member_id),
                )
            else:
                member_id = _new_id("mem")
                self._mock_execute(
                    "INSERT INTO RBT_TEAM_MEMBERS VALUES (?, ?, ?, ?, ?)",
                    (member_id, team_id, clean_email, user_role, now),
                )
            return self._mock_fetchall("SELECT * FROM RBT_TEAM_MEMBERS WHERE MEMBER_ID = ?", (member_id,))[0]

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT MEMBER_ID FROM {self._meta_prefix}.RBT_TEAM_MEMBERS WHERE TEAM_ID = %s AND LOWER(USER_EMAIL) = LOWER(%s)",
                (team_id, clean_email),
            )
            row = cur.fetchone()
            if row:
                member_id = row[0]
                cur.execute(
                    f"UPDATE {self._meta_prefix}.RBT_TEAM_MEMBERS SET USER_ROLE = %s WHERE MEMBER_ID = %s",
                    (user_role, member_id),
                )
            else:
                member_id = _new_id("mem")
                cur.execute(
                    f"INSERT INTO {self._meta_prefix}.RBT_TEAM_MEMBERS (MEMBER_ID, TEAM_ID, USER_EMAIL, USER_ROLE) VALUES (%s, %s, %s, %s)",
                    (member_id, team_id, clean_email, user_role),
                )
            cur.execute(f"SELECT * FROM {self._meta_prefix}.RBT_TEAM_MEMBERS WHERE MEMBER_ID = %s", (member_id,))
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, cur.fetchone()))
        finally:
            conn.close()

    def remove_team_member(self, team_id: str, member_id: str) -> bool:
        if settings.SNOWFLAKE_MOCK_MODE:
            existing = self._mock_fetchall(
                "SELECT MEMBER_ID FROM RBT_TEAM_MEMBERS WHERE TEAM_ID = ? AND MEMBER_ID = ?",
                (team_id, member_id),
            )
            if not existing:
                return False
            self._mock_execute(
                "DELETE FROM RBT_TEAM_MEMBERS WHERE TEAM_ID = ? AND MEMBER_ID = ?",
                (team_id, member_id),
            )
            return True

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"DELETE FROM {self._meta_prefix}.RBT_TEAM_MEMBERS WHERE TEAM_ID = %s AND MEMBER_ID = %s",
                (team_id, member_id),
            )
            return cur.rowcount > 0
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Domains CRUD & 3-Step Native Snowflake Grounding
    # -----------------------------------------------------------------------

    def list_schemas(self, team_id: str) -> List[str]:
        team = self.get_team(team_id)
        if not team:
            raise HTTPException(status_code=404, detail=f"Team '{team_id}' not found")
        if settings.SNOWFLAKE_MOCK_MODE:
            return ["SUPPLY_CHAIN", "PDH_SUPPLY_PLANNING_SRC_BASE", "PUBLIC"]
        db_name = team["SNOWFLAKE_DATABASE"]
        conn = self._get_live_connection(warehouse=team["SNOWFLAKE_WAREHOUSE"])
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT SCHEMA_NAME
                FROM {db_name}.INFORMATION_SCHEMA.SCHEMATA
                WHERE SCHEMA_NAME != 'INFORMATION_SCHEMA'
                ORDER BY SCHEMA_NAME
                """
            )
            return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

    def list_domains(self, team_id: str) -> List[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            return self._mock_fetchall(
                "SELECT * FROM RBT_DOMAIN_REGISTRY WHERE TEAM_ID = ? AND IS_ACTIVE = 1 ORDER BY CREATED_TS DESC",
                (team_id,),
            )
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM {self._meta_prefix}.RBT_DOMAIN_REGISTRY WHERE TEAM_ID = %s AND IS_ACTIVE = TRUE ORDER BY CREATED_TS DESC", (team_id,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_domain(self, domain_id: str) -> Optional[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            rows = self._mock_fetchall("SELECT * FROM RBT_DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", (domain_id,))
            return rows[0] if rows else None
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM {self._meta_prefix}.RBT_DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        finally:
            conn.close()

    @staticmethod
    def _parse_type_dims(data_type: str, precision: Optional[int] = None, scale: Optional[int] = None) -> Tuple[str, Optional[int], Optional[int]]:
        dt = (data_type or "").strip().upper()
        m = re.match(r"^([A-Z0-9_]+)\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)$", dt)
        if m:
            base = m.group(1)
            p = int(m.group(2)) if precision is None else precision
            s = int(m.group(3)) if (m.group(3) is not None and scale is None) else scale
            return base, p, s
        return dt.split("(")[0].strip(), precision, scale

    @classmethod
    def compare_schema_contracts(
        cls,
        baseline_cols: List[Dict[str, Any]],
        current_cols: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Compare current target schema against BASELINE_SCHEMA_JSON for dropped or narrowed columns."""
        if not baseline_cols:
            return {"status": "SKIPPED", "drifts": []}
        if not current_cols:
            return {
                "status": "DRIFT_DETECTED",
                "drifts": [{"column_name": "*", "issue": "TARGET_TABLE_MISSING", "detail": "Target table or view returned 0 columns."}],
            }

        cur_map = {str(c["column_name"]).upper(): c for c in current_cols}
        drifts: List[Dict[str, Any]] = []
        numeric_types = {"NUMBER", "DECIMAL", "NUMERIC", "INT", "INTEGER", "BIGINT", "SMALLINT", "FLOAT", "DOUBLE", "REAL"}

        for base in baseline_cols:
            col_name = str(base["column_name"]).upper()
            if col_name not in cur_map:
                drifts.append(
                    {
                        "column_name": col_name,
                        "issue": "DROPPED_COLUMN",
                        "baseline_type": base.get("data_type"),
                        "current_type": None,
                        "detail": f"Column '{col_name}' ({base.get('data_type')}) was dropped from target table.",
                    }
                )
                continue

            cur = cur_map[col_name]
            b_type, b_prec, b_scale = cls._parse_type_dims(
                str(base.get("data_type") or ""),
                base.get("numeric_precision"),
                base.get("numeric_scale"),
            )
            c_type, c_prec, c_scale = cls._parse_type_dims(
                str(cur.get("data_type") or ""),
                cur.get("numeric_precision"),
                cur.get("numeric_scale"),
            )

            # Precision / scale narrowing
            if b_prec is not None and c_prec is not None and c_prec < b_prec:
                drifts.append(
                    {
                        "column_name": col_name,
                        "issue": "PRECISION_NARROWED",
                        "baseline_type": f"{b_type}({b_prec},{b_scale})" if b_scale is not None else f"{b_type}({b_prec})",
                        "current_type": f"{c_type}({c_prec},{c_scale})" if c_scale is not None else f"{c_type}({c_prec})",
                        "detail": f"Column '{col_name}' precision narrowed from {b_prec} to {c_prec}.",
                    }
                )
            elif b_scale is not None and c_scale is not None and c_scale < b_scale:
                drifts.append(
                    {
                        "column_name": col_name,
                        "issue": "SCALE_NARROWED",
                        "baseline_type": f"{b_type}({b_prec},{b_scale})",
                        "current_type": f"{c_type}({c_prec},{c_scale})",
                        "detail": f"Column '{col_name}' scale narrowed from {b_scale} to {c_scale}.",
                    }
                )
            elif b_type in numeric_types and c_type not in numeric_types:
                drifts.append(
                    {
                        "column_name": col_name,
                        "issue": "INCOMPATIBLE_TYPE_CHANGE",
                        "baseline_type": base.get("data_type"),
                        "current_type": cur.get("data_type"),
                        "detail": f"Column '{col_name}' type changed from {base.get('data_type')} to {cur.get('data_type')}.",
                    }
                )

        return {
            "status": "DRIFT_DETECTED" if drifts else "PASS",
            "drifts": drifts,
        }

    def _fetch_table_schema_snapshot(
        self,
        team: Dict[str, Any],
        target_schema: str,
        target_table: str,
    ) -> List[Dict[str, Any]]:
        db_name = team["SNOWFLAKE_DATABASE"]
        schema_parts = [p.strip().strip('"').upper() for p in target_schema.split(".") if p.strip()]
        if len(schema_parts) >= 3:
            db_name, norm_schema, norm_table = schema_parts[0], schema_parts[1], schema_parts[2]
        elif len(schema_parts) == 2:
            db_name, norm_schema = schema_parts[0], schema_parts[1]
            norm_table = target_table.strip().strip('"').upper()
        else:
            norm_schema = schema_parts[0] if schema_parts else target_schema.strip().strip('"').upper()
            norm_table = target_table.strip().strip('"').upper()

        table_parts = [p.strip().strip('"').upper() for p in norm_table.split(".") if p.strip()]
        if len(table_parts) >= 3:
            db_name, norm_schema, norm_table = table_parts[0], table_parts[1], table_parts[2]
        elif len(table_parts) == 2:
            norm_schema, norm_table = table_parts[0], table_parts[1]

        if settings.SNOWFLAKE_MOCK_MODE:
            pragma_rows = self._mock_fetchall(f"PRAGMA table_info({norm_table})")
            return [
                {
                    "column_name": pr["name"],
                    "data_type": pr["type"] or "TEXT",
                    "numeric_precision": None,
                    "numeric_scale": None,
                    "is_nullable": pr["notnull"] == 0,
                }
                for pr in pragma_rows
            ]

        conn = self._get_live_connection(warehouse=team["SNOWFLAKE_WAREHOUSE"])
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE
                FROM {db_name}.INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
                """,
                (norm_schema, norm_table),
            )
            return [
                {
                    "column_name": r[0],
                    "data_type": r[1],
                    "numeric_precision": int(r[2]) if r[2] is not None else None,
                    "numeric_scale": int(r[3]) if r[3] is not None else None,
                    "is_nullable": r[4] == "YES",
                }
                for r in cur.fetchall()
            ]
        except Exception:
            return []
        finally:
            conn.close()

    def create_domain(self, team_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        domain_id = _new_id("dom")
        now = _utcnow_iso()
        related_json = json.dumps(data.get("related_tables", []))
        team = self.get_team(team_id)
        baseline_cols: List[Dict[str, Any]] = []
        if team:
            baseline_cols = self._fetch_table_schema_snapshot(
                team, data["target_schema"], data["target_table"]
            )
        baseline_json = json.dumps(baseline_cols) if baseline_cols else None

        if settings.SNOWFLAKE_MOCK_MODE:
            self._mock_execute(
                """
                INSERT INTO RBT_DOMAIN_REGISTRY
                (DOMAIN_ID, TEAM_ID, DOMAIN_NAME, TARGET_SCHEMA, TARGET_TABLE, RELATED_TABLES,
                 TEMPORAL_CYCLE_COLUMN, SNAPSHOT_TS_COLUMN, MAX_HIERARCHY_DEPTH, DOWNSTREAM_CONSUMER,
                 BUSINESS_CONTEXT, CRITICALITY, BASELINE_SCHEMA_JSON, UPSTREAM_SOURCE_TABLE,
                 TRANSFORMATION_SQL, IS_ACTIVE, CREATED_TS)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    domain_id,
                    team_id,
                    data["domain_name"],
                    data["target_schema"],
                    data["target_table"],
                    related_json,
                    data.get("temporal_cycle_column"),
                    data.get("snapshot_ts_column"),
                    data.get("max_hierarchy_depth", 10),
                    data.get("downstream_consumer"),
                    data.get("business_context"),
                    data.get("criticality", "HIGH"),
                    baseline_json,
                    data.get("upstream_source_table"),
                    data.get("transformation_sql"),
                    1,
                    now,
                ),
            )
            return self.get_domain(domain_id)  # type: ignore[return-value]

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {self._meta_prefix}.RBT_DOMAIN_REGISTRY
                (DOMAIN_ID, TEAM_ID, DOMAIN_NAME, TARGET_SCHEMA, TARGET_TABLE, RELATED_TABLES,
                 TEMPORAL_CYCLE_COLUMN, SNAPSHOT_TS_COLUMN, MAX_HIERARCHY_DEPTH, DOWNSTREAM_CONSUMER,
                 BUSINESS_CONTEXT, CRITICALITY, BASELINE_SCHEMA_JSON, UPSTREAM_SOURCE_TABLE, TRANSFORMATION_SQL)
                SELECT %s, %s, %s, %s, %s, PARSE_JSON(%s), %s, %s, %s, %s, %s, %s, TRY_PARSE_JSON(%s), %s, %s
                """,
                (
                    domain_id,
                    team_id,
                    data["domain_name"],
                    data["target_schema"],
                    data["target_table"],
                    related_json,
                    data.get("temporal_cycle_column"),
                    data.get("snapshot_ts_column"),
                    data.get("max_hierarchy_depth", 10),
                    data.get("downstream_consumer"),
                    data.get("business_context"),
                    data.get("criticality", "HIGH"),
                    baseline_json,
                    data.get("upstream_source_table"),
                    data.get("transformation_sql"),
                ),
            )
            return self.get_domain(domain_id)  # type: ignore[return-value]
        finally:
            conn.close()

    def rebaseline_domain_schema(self, team: Dict[str, Any], domain: Dict[str, Any]) -> List[Dict[str, Any]]:
        snapshot = self._fetch_table_schema_snapshot(team, domain["TARGET_SCHEMA"], domain["TARGET_TABLE"])
        baseline_json = json.dumps(snapshot)
        if settings.SNOWFLAKE_MOCK_MODE:
            self._mock_execute(
                "UPDATE RBT_DOMAIN_REGISTRY SET BASELINE_SCHEMA_JSON = ? WHERE DOMAIN_ID = ?",
                (baseline_json, domain["DOMAIN_ID"]),
            )
            return snapshot

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {self._meta_prefix}.RBT_DOMAIN_REGISTRY SET BASELINE_SCHEMA_JSON = PARSE_JSON(%s) WHERE DOMAIN_ID = %s",
                (baseline_json, domain["DOMAIN_ID"]),
            )
            return snapshot
        finally:
            conn.close()

    def detect_schema_drift(
        self,
        team: Dict[str, Any],
        domain: Dict[str, Any],
        target_schema_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        raw_baseline = domain.get("BASELINE_SCHEMA_JSON")
        if isinstance(raw_baseline, str) and raw_baseline:
            try:
                baseline_cols = json.loads(raw_baseline)
            except Exception:
                baseline_cols = []
        elif isinstance(raw_baseline, list):
            baseline_cols = raw_baseline
        else:
            baseline_cols = []

        if not baseline_cols:
            return {"status": "SKIPPED", "drifts": []}

        effective_schema = target_schema_override or domain["TARGET_SCHEMA"]
        effective_table = domain["TARGET_TABLE"]
        if settings.SNOWFLAKE_MOCK_MODE and target_schema_override:
            # In mock mode, check if a schema-prefixed override table exists (e.g. UAT_INTERSITE_BOM)
            candidate = f"{target_schema_override.split('.')[-1]}_{effective_table}"
            if self._mock_fetchall(f"PRAGMA table_info({candidate})"):
                effective_table = candidate

        current_cols = self._fetch_table_schema_snapshot(team, effective_schema, effective_table)
        return self.compare_schema_contracts(baseline_cols, current_cols)

    def ground_domain_metadata(self, team: Dict[str, Any], domain: Dict[str, Any]) -> GroundingContext:
        """
        3-Step Native Snowflake Catalog Grounding:
        1. INFORMATION_SCHEMA.COLUMNS + COMMENT descriptions
        2. Physical Constraints (PK, FK, UNIQUE)
        3. Non-PII Statistical Profiles (COUNT, MIN, MAX, NULL ratio)
        """
        target_table = domain["TARGET_TABLE"]
        target_schema = domain["TARGET_SCHEMA"]
        db_name = team["SNOWFLAKE_DATABASE"]

        if settings.SNOWFLAKE_MOCK_MODE:
            # Introspect real SQLite PRAGMA if table exists, otherwise provide rich catalog metadata
            assert self._conn is not None
            cols_info: List[GroundingColumnInfo] = []
            pragma_rows = self._mock_fetchall(f"PRAGMA table_info({target_table})")
            if pragma_rows:
                for pr in pragma_rows:
                    cols_info.append(
                        GroundingColumnInfo(
                            table_name=target_table,
                            column_name=pr["name"],
                            data_type=pr["type"] or "TEXT",
                            is_nullable=(pr["notnull"] == 0),
                            comment=f"Native Snowflake column {pr['name']} on {target_schema}.{target_table}",
                        )
                    )
                row_count = self._mock_fetchall(f"SELECT COUNT(*) AS CNT FROM {target_table}")[0]["CNT"]
                col_stats: Dict[str, Dict[str, Any]] = {}
                for pr in pragma_rows[:6]:
                    c_name = pr["name"]
                    stat = self._mock_fetchall(
                        f"SELECT COUNT(*) - COUNT({c_name}) AS NULL_CNT, MIN({c_name}) AS MIN_VAL, MAX({c_name}) AS MAX_VAL FROM {target_table}"
                    )[0]
                    col_stats[c_name] = {
                        "null_ratio": round(stat["NULL_CNT"] / max(row_count, 1), 4),
                        "min": stat["MIN_VAL"],
                        "max": stat["MAX_VAL"],
                    }
            else:
                # Generic fallback for newly onboarded custom mock table names
                cols_info = [
                    GroundingColumnInfo(table_name=target_table, column_name="ID", data_type="VARCHAR(64)", is_nullable=False, comment="Primary business key"),
                    GroundingColumnInfo(table_name=target_table, column_name=domain.get("TEMPORAL_CYCLE_COLUMN") or "PLANNING_CYCLE_ID", data_type="VARCHAR(32)", is_nullable=False, comment="Temporal cycle identifier"),
                    GroundingColumnInfo(table_name=target_table, column_name="UPDATE_TS", data_type="TIMESTAMP_NTZ", is_nullable=True, comment="Last update timestamp"),
                ]
                row_count = 1500
                col_stats = {"ID": {"null_ratio": 0.0, "min": "1001", "max": "9999"}}

            constraints = [
                GroundingConstraintInfo(constraint_type="PRIMARY KEY", table_name=target_table, column_name=cols_info[0].column_name)
            ]
            if target_table == "INTERSITE_BOM":
                constraints.append(
                    GroundingConstraintInfo(
                        constraint_type="FOREIGN KEY",
                        table_name="INTERSITE_BOM",
                        column_name="ITEM_ID",
                        referenced_table="ITEM_MASTER",
                        referenced_column="ITEM_ID",
                    )
                )

            return GroundingContext(
                columns=cols_info,
                constraints=constraints,
                profiles=[GroundingStatProfile(table_name=target_table, row_count=row_count, column_stats=col_stats)],
            )

        # Live Snowflake 3-step grounding
        # Normalize accidental DB.SCHEMA or SCHEMA.TABLE inputs
        schema_parts = [p.strip().strip('"') for p in target_schema.split(".") if p.strip()]
        if len(schema_parts) >= 2:
            db_name = schema_parts[0].upper()
            target_schema = schema_parts[-1].upper()
        else:
            target_schema = target_schema.strip().strip('"').upper()

        table_parts = [p.strip().strip('"') for p in target_table.split(".") if p.strip()]
        if len(table_parts) >= 3:
            db_name, target_schema, target_table = table_parts[0].upper(), table_parts[1].upper(), table_parts[2].upper()
        elif len(table_parts) == 2:
            target_schema, target_table = table_parts[0].upper(), table_parts[1].upper()
        else:
            target_table = target_table.strip().strip('"').upper()

        conn = self._get_live_connection(warehouse=team["SNOWFLAKE_WAREHOUSE"])
        try:
            cur = conn.cursor()
            # Step 1: INFORMATION_SCHEMA.COLUMNS
            cur.execute(
                f"""
                SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COMMENT
                FROM {db_name}.INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
                """,
                (target_schema, target_table),
            )
            columns = [
                GroundingColumnInfo(
                    table_name=r[0],
                    column_name=r[1],
                    data_type=r[2],
                    is_nullable=(r[3] == "YES"),
                    comment=r[4],
                )
                for r in cur.fetchall()
            ]
            if not columns:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Table or view '{db_name}.{target_schema}.{target_table}' was not found "
                        f"or you do not have access to it. Verify the Schema and Table name."
                    ),
                )

            # Step 2: Physical Constraints
            constraints: List[GroundingConstraintInfo] = []
            for stmt, ctype in [
                (f"SHOW PRIMARY KEYS IN TABLE {db_name}.{target_schema}.{target_table}", "PRIMARY KEY"),
                (f"SHOW UNIQUE KEYS IN TABLE {db_name}.{target_schema}.{target_table}", "UNIQUE"),
            ]:
                try:
                    cur.execute(stmt)
                    for r in cur.fetchall():
                        constraints.append(
                            GroundingConstraintInfo(constraint_type=ctype, table_name=target_table, column_name=r[4])
                        )
                except Exception:
                    pass

            # Step 3: Non-PII Statistical Profile
            cur.execute(f"SELECT COUNT(*) FROM {db_name}.{target_schema}.{target_table}")
            row_count = int(cur.fetchone()[0])
            col_stats: Dict[str, Dict[str, Any]] = {}
            for col in columns[:8]:
                c_name = col.column_name
                cur.execute(
                    f"SELECT COUNT(*) - COUNT({c_name}), MIN({c_name})::STRING, MAX({c_name})::STRING FROM {db_name}.{target_schema}.{target_table}"
                )
                null_cnt, min_v, max_v = cur.fetchone()
                col_stats[c_name] = {
                    "null_ratio": round(null_cnt / max(row_count, 1), 4),
                    "min": min_v,
                    "max": max_v,
                }

            return GroundingContext(
                columns=columns,
                constraints=constraints,
                profiles=[GroundingStatProfile(table_name=target_table, row_count=row_count, column_stats=col_stats)],
            )
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Cortex AI Structured Output & Compile Check
    # -----------------------------------------------------------------------

    def invoke_cortex_structured(
        self,
        prompt: str,
        json_schema: Dict[str, Any],
        warehouse: Optional[str] = None,
        domain: Optional[Dict[str, Any]] = None,
        grounding: Optional[GroundingContext] = None,
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Call SNOWFLAKE.CORTEX.COMPLETE with Structured Output JSON Schema."""
        if settings.SNOWFLAKE_MOCK_MODE:
            return self._mock_cortex_response(prompt, json_schema, domain, grounding)

        chosen_model = model_name or settings.CORTEX_MODEL
        conn = self._get_live_connection(warehouse=warehouse)
        try:
            cur = conn.cursor()
            options = json.dumps({"response_format": {"type": "json", "schema": json_schema}})
            messages = json.dumps([{"role": "user", "content": prompt}])
            cur.execute(
                "SELECT SNOWFLAKE.CORTEX.COMPLETE(%s, PARSE_JSON(%s), PARSE_JSON(%s))",
                (chosen_model, messages, options),
            )
            raw = cur.fetchone()[0]
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            result = self._unwrap_cortex_payload(parsed)
            if not result and isinstance(parsed, dict):
                print(f"[CORTEX WARN] Unexpected structured output shape for model={chosen_model}: keys={list(parsed.keys())}")
            return result
        finally:
            conn.close()

    @staticmethod
    def _unwrap_cortex_payload(parsed: Any) -> Dict[str, Any]:
        """Extract JSON dict from Snowflake CORTEX.COMPLETE structured/choices envelopes."""
        def _parse_maybe_json(val: Any) -> Any:
            if not isinstance(val, str):
                return val
            txt = val.strip()
            if txt.startswith("```"):
                lines = txt.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                txt = "\n".join(lines).strip()
            return json.loads(txt)

        if not isinstance(parsed, dict):
            return {}
        if "structured_output" in parsed and isinstance(parsed["structured_output"], list) and parsed["structured_output"]:
            first = parsed["structured_output"][0]
            if isinstance(first, dict):
                inner = first.get("raw_message") or first.get("content") or first
                res = _parse_maybe_json(inner)
                return res if isinstance(res, dict) else {}
        if "choices" in parsed and isinstance(parsed["choices"], list) and parsed["choices"]:
            choice = parsed["choices"][0]
            if isinstance(choice, dict):
                content = choice.get("messages")
                if content is None and isinstance(choice.get("message"), dict):
                    content = choice["message"].get("content")
                res = _parse_maybe_json(content)
                return res if isinstance(res, dict) else {}
        return parsed

    def _mock_cortex_response(
        self,
        prompt: str,
        json_schema: Dict[str, Any],
        domain: Optional[Dict[str, Any]],
        grounding: Optional[GroundingContext],
    ) -> Dict[str, Any]:
        if json_schema == CORTEX_REMAP_SCHEMA:
            # Extract target columns from prompt or return clean remapped SQL
            cols = [c.column_name for c in (grounding.columns if grounding else [])]
            key_col = cols[0] if cols else "ITEM_ID"
            return {
                "remapped_sql": f"SELECT {key_col}, COUNT(*) AS DUP_CNT\nFROM __TARGET_DATASET__\nGROUP BY {key_col}\nHAVING COUNT(*) > 1",
                "mapping_notes": f"Remapped source template key column to target dataset column '{key_col}' using Snowflake INFORMATION_SCHEMA metadata.",
            }

        # Auto-debug self-correction simulation
        if "FIX THIS SQL ERROR" in prompt:
            cols = [c.column_name for c in (grounding.columns if grounding else [])]
            first_col = cols[0] if cols else "ITEM_ID"
            return {
                "generated_rules": [
                    {
                        "risk_name": "Auto-Corrected Key Integrity Check",
                        "risk_category": "REFERENTIAL_INTEGRITY",
                        "risk_priority": "P2_WARNING",
                        "likelihood_score": 3,
                        "impact_score": 4,
                        "business_impact_desc": "Auto-repaired SQL after Snowflake compile feedback.",
                        "validation_sql": f"SELECT {first_col} FROM __TARGET_DATASET__ WHERE {first_col} IS NULL",
                    }
                ]
            }

        cols = [c.column_name for c in (grounding.columns if grounding else [])]
        pk = cols[0] if cols else "ITEM_ID"
        max_depth = domain.get("MAX_HIERARCHY_DEPTH", 10) if domain else 10
        domain_name = domain.get("DOMAIN_NAME", "Dataset") if domain else "Dataset"

        rules = [
            {
                "risk_name": f"{domain_name} Duplicate Natural Key",
                "risk_category": "DUPLICATE_KEY",
                "risk_priority": "P1_BLOCKER",
                "likelihood_score": 4,
                "impact_score": 5,
                "business_impact_desc": f"Duplicate {pk} rows cause downstream aggregation inflation.",
                "validation_sql": f"SELECT {pk}, COUNT(*) AS CNT FROM __TARGET_DATASET__ GROUP BY {pk} HAVING COUNT(*) > 1",
            },
            {
                "risk_name": f"{domain_name} Null Key Completeness",
                "risk_category": "REFERENTIAL_INTEGRITY",
                "risk_priority": "P1_BLOCKER",
                "likelihood_score": 4,
                "impact_score": 4,
                "business_impact_desc": f"Null {pk} breaks downstream joins.",
                "validation_sql": f"SELECT * FROM __TARGET_DATASET__ WHERE {pk} IS NULL",
            },
            {
                "risk_name": f"{domain_name} Freshness SLA Check",
                "risk_category": "FRESHNESS_ANOMALY",
                "risk_priority": "P2_WARNING",
                "likelihood_score": 3,
                "impact_score": 4,
                "business_impact_desc": "Detects rows missing UPDATE_TS audit timestamp.",
                "validation_sql": f"SELECT {pk} FROM __TARGET_DATASET__ WHERE {'UPDATE_TS' if 'UPDATE_TS' in cols else pk} IS NULL",
            },
            {
                "risk_name": f"{domain_name} Boundary Non-Negativity Check",
                "risk_category": "BOUNDARY_VALUE",
                "risk_priority": "P2_WARNING",
                "likelihood_score": 3,
                "impact_score": 4,
                "business_impact_desc": "Flags unexpected negative quantities or amounts.",
                "validation_sql": (
                    f"SELECT {pk}, BOM_QTY FROM __TARGET_DATASET__ WHERE BOM_QTY < 0"
                    if "BOM_QTY" in cols
                    else f"SELECT {pk} FROM __TARGET_DATASET__ WHERE 1 = 0"
                ),
            },
            {
                "risk_name": f"{domain_name} Bounded Hierarchy Recursion Check",
                "risk_category": "HIERARCHY_LOOP",
                "risk_priority": "P1_BLOCKER",
                "likelihood_score": 4,
                "impact_score": 5,
                "business_impact_desc": f"Prevents circular parent-child loops (bounded at depth <= {max_depth}).",
                "validation_sql": (
                    f"WITH RECURSIVE tree AS (\n"
                    f"  SELECT ITEM_ID, SITE_ID, PARENT_SITE_ID, 1 AS depth FROM __TARGET_DATASET__ WHERE SITE_ID = PARENT_SITE_ID\n"
                    f"  UNION ALL\n"
                    f"  SELECT t.ITEM_ID, t.SITE_ID, t.PARENT_SITE_ID, tr.depth + 1 FROM __TARGET_DATASET__ t JOIN tree tr ON t.PARENT_SITE_ID = tr.SITE_ID WHERE tr.depth <= {max_depth}\n"
                    f")\nSELECT ITEM_ID, SITE_ID, PARENT_SITE_ID FROM tree"
                    if "PARENT_SITE_ID" in cols
                    else f"SELECT {pk} FROM __TARGET_DATASET__ WHERE 1 = 0"
                ),
            },
        ]

        upstream = domain.get("UPSTREAM_SOURCE_TABLE") if domain else None
        if upstream:
            rules.append(
                {
                    "risk_name": f"{domain_name} Staging-to-Publish Reconciliation",
                    "risk_category": "PIPELINE_RECONCILIATION",
                    "risk_priority": "P1_BLOCKER",
                    "likelihood_score": 4,
                    "impact_score": 5,
                    "business_impact_desc": f"Detects row-count or key-metric drift between upstream {upstream} and target {domain_name}.",
                    "validation_sql": f"SELECT {pk}, COUNT(*) AS TARGET_CNT FROM __TARGET_DATASET__ GROUP BY {pk} HAVING COUNT(*) < 0",
                }
            )

        if "TRANSFORMATION_SQL" in prompt or "PR_SQL_DIFF" in prompt:
            rules.append(
                {
                    "risk_name": f"{domain_name} Transformation Logic Edge-Case Guard",
                    "risk_category": "BOUNDARY_VALUE",
                    "risk_priority": "P2_WARNING",
                    "likelihood_score": 3,
                    "impact_score": 4,
                    "business_impact_desc": "Validates edge-case predicates and CASE/JOIN outputs introduced in pipeline transformation SQL / PR diff.",
                    "validation_sql": f"SELECT {pk} FROM __TARGET_DATASET__ WHERE {pk} IS NULL",
                }
            )

        return {"generated_rules": rules}

    def invoke_cortex_text(
        self,
        prompt: str,
        warehouse: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> str:
        """Generate plain-English triage summary via Cortex AI."""
        if settings.SNOWFLAKE_MOCK_MODE:
            return prompt.splitlines()[-1] if "\n" in prompt else prompt

        chosen_model = model_name or settings.CORTEX_MODEL
        conn = self._get_live_connection(warehouse=warehouse)
        try:
            cur = conn.cursor()
            cur.execute("SELECT SNOWFLAKE.CORTEX.COMPLETE(%s, %s)", (chosen_model, prompt))
            return str(cur.fetchone()[0])
        finally:
            conn.close()

    def compile_check_sql(
        self,
        sql: str,
        team: Dict[str, Any],
        domain: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        """Run EXPLAIN / LIMIT 1 compile check against Snowflake (or SQLite in mock mode)."""
        target_fqn = (
            domain["TARGET_TABLE"]
            if settings.SNOWFLAKE_MOCK_MODE
            else f"{team['SNOWFLAKE_DATABASE']}.{domain['TARGET_SCHEMA']}.{domain['TARGET_TABLE']}"
        )
        try:
            wrapped, params = wrap_with_target_dataset_cte(
                sql=sql,
                target_fqn=target_fqn,
                temporal_cycle_column=domain.get("TEMPORAL_CYCLE_COLUMN"),
                snapshot_ts_column=domain.get("SNAPSHOT_TS_COLUMN"),
                planning_cycle_id=None,
                snapshot_ts=None,
                max_hierarchy_depth=domain.get("MAX_HIERARCHY_DEPTH", 10),
            )
            if settings.SNOWFLAKE_MOCK_MODE:
                sqlite_sql = self._transpile_to_sqlite(wrapped)
                assert self._conn is not None
                with self._lock:
                    cur = self._conn.cursor()
                    # Check table existence; if custom mock table isn't created yet, treat as valid AST
                    pragma = cur.execute(f"PRAGMA table_info({domain['TARGET_TABLE']})").fetchall()
                    if pragma:
                        cur.execute(f"EXPLAIN QUERY PLAN {sqlite_sql}", params)
                return True, None

            conn = self._get_live_connection(warehouse=team["SNOWFLAKE_WAREHOUSE"])
            try:
                cur = conn.cursor()
                cur.execute(f"EXPLAIN USING TEXT {wrapped}", params)
                return True, None
            finally:
                conn.close()
        except Exception as exc:
            return False, str(exc)

    def _transpile_to_sqlite(self, snowflake_sql: str) -> str:
        # Convert %(param)s to :param for sqlite3 named binding
        sqlite_param_sql = snowflake_sql
        for key in ("planning_cycle_id", "snapshot_ts"):
            sqlite_param_sql = sqlite_param_sql.replace(f"%({key})s", f":{key}")
        try:
            transpiled = sqlglot.transpile(sqlite_param_sql, read="snowflake", write="sqlite")
            return transpiled[0] if transpiled else sqlite_param_sql
        except Exception:
            return sqlite_param_sql

    # -----------------------------------------------------------------------
    # Rules & History CRUD
    # -----------------------------------------------------------------------

    def list_rules(
        self,
        team_id: str,
        domain_id: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            clauses = ["r.TEAM_ID = ?"]
            params: List[Any] = [team_id]
            if domain_id:
                clauses.append("r.DOMAIN_ID = ?")
                params.append(domain_id)
            if status:
                clauses.append("r.STATUS = ?")
                params.append(status)
            if priority:
                clauses.append("r.RISK_PRIORITY = ?")
                params.append(priority)
            if category:
                clauses.append("r.RISK_CATEGORY = ?")
                params.append(category)
            sql = (
                "SELECT r.*, d.DOMAIN_NAME FROM RBT_TEST_RULES r "
                "LEFT JOIN RBT_DOMAIN_REGISTRY d ON r.DOMAIN_ID = d.DOMAIN_ID "
                f"WHERE {' AND '.join(clauses)} ORDER BY r.UPDATED_TS DESC"
            )
            return self._mock_fetchall(sql, tuple(params))

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            clauses = ["r.TEAM_ID = %s"]
            params = [team_id]
            if domain_id:
                clauses.append("r.DOMAIN_ID = %s")
                params.append(domain_id)
            if status:
                clauses.append("r.STATUS = %s")
                params.append(status)
            if priority:
                clauses.append("r.RISK_PRIORITY = %s")
                params.append(priority)
            if category:
                clauses.append("r.RISK_CATEGORY = %s")
                params.append(category)
            cur.execute(
                f"SELECT r.*, d.DOMAIN_NAME FROM {self._meta_prefix}.RBT_TEST_RULES r "
                f"LEFT JOIN {self._meta_prefix}.RBT_DOMAIN_REGISTRY d ON r.DOMAIN_ID = d.DOMAIN_ID "
                f"WHERE {' AND '.join(clauses)} ORDER BY r.UPDATED_TS DESC",
                tuple(params),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            rows = self._mock_fetchall(
                "SELECT r.*, d.DOMAIN_NAME FROM RBT_TEST_RULES r LEFT JOIN RBT_DOMAIN_REGISTRY d ON r.DOMAIN_ID = d.DOMAIN_ID WHERE r.RULE_ID = ?",
                (rule_id,),
            )
            return rows[0] if rows else None
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT r.*, d.DOMAIN_NAME FROM {self._meta_prefix}.RBT_TEST_RULES r LEFT JOIN {self._meta_prefix}.RBT_DOMAIN_REGISTRY d ON r.DOMAIN_ID = d.DOMAIN_ID WHERE r.RULE_ID = %s",
                (rule_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        finally:
            conn.close()

    def insert_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        rule_id = rule.get("rule_id") or _new_id("rule")
        now = _utcnow_iso()
        likelihood = int(rule.get("likelihood_score") or 3)
        impact = int(rule.get("impact_score") or 3)
        risk_score = likelihood * impact
        risk_priority = rule.get("risk_priority") or map_risk_score_to_priority(risk_score)
        if settings.SNOWFLAKE_MOCK_MODE:
            self._mock_execute(
                """
                INSERT INTO RBT_TEST_RULES
                (RULE_ID, TEAM_ID, DOMAIN_ID, RISK_NAME, RISK_CATEGORY, RISK_PRIORITY,
                 LIKELIHOOD_SCORE, IMPACT_SCORE, RISK_SCORE, BUSINESS_IMPACT_DESC, VALIDATION_SQL,
                 STATUS, LAST_COMPILE_ERROR, IS_SHARED_TEMPLATE, CREATED_BY, APPROVED_BY, UPDATED_TS)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    rule["team_id"],
                    rule["domain_id"],
                    rule["risk_name"],
                    rule.get("risk_category"),
                    risk_priority,
                    likelihood,
                    impact,
                    risk_score,
                    rule.get("business_impact_desc"),
                    rule["validation_sql"],
                    rule.get("status", "DRAFT"),
                    rule.get("last_compile_error"),
                    1 if rule.get("is_shared_template") else 0,
                    rule.get("created_by"),
                    rule.get("approved_by"),
                    now,
                ),
            )
            return self.get_rule(rule_id)  # type: ignore[return-value]

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {self._meta_prefix}.RBT_TEST_RULES
                (RULE_ID, TEAM_ID, DOMAIN_ID, RISK_NAME, RISK_CATEGORY, RISK_PRIORITY,
                 LIKELIHOOD_SCORE, IMPACT_SCORE, RISK_SCORE, BUSINESS_IMPACT_DESC,
                 VALIDATION_SQL, STATUS, LAST_COMPILE_ERROR, IS_SHARED_TEMPLATE, CREATED_BY, APPROVED_BY)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    rule_id,
                    rule["team_id"],
                    rule["domain_id"],
                    rule["risk_name"],
                    rule.get("risk_category"),
                    risk_priority,
                    likelihood,
                    impact,
                    risk_score,
                    rule.get("business_impact_desc"),
                    rule["validation_sql"],
                    rule.get("status", "DRAFT"),
                    rule.get("last_compile_error"),
                    bool(rule.get("is_shared_template", False)),
                    rule.get("created_by"),
                    rule.get("approved_by"),
                ),
            )
            return self.get_rule(rule_id)  # type: ignore[return-value]
        finally:
            conn.close()

    def update_rule_and_log_history(
        self,
        rule_id: str,
        updates: Dict[str, Any],
        modified_by: str,
    ) -> Dict[str, Any]:
        existing = self.get_rule(rule_id)
        assert existing is not None
        now = _utcnow_iso()

        updates = dict(updates)
        if "likelihood_score" in updates or "impact_score" in updates:
            new_l = int(updates.get("likelihood_score") or existing.get("LIKELIHOOD_SCORE") or 3)
            new_i = int(updates.get("impact_score") or existing.get("IMPACT_SCORE") or 3)
            new_score = new_l * new_i
            updates["likelihood_score"] = new_l
            updates["impact_score"] = new_i
            updates["risk_score"] = new_score
            if "risk_priority" not in updates or updates["risk_priority"] is None:
                updates["risk_priority"] = map_risk_score_to_priority(new_score)

        status_old = existing["STATUS"]
        status_new = updates.get("status", status_old)
        sql_old = existing["VALIDATION_SQL"]
        sql_new = updates.get("validation_sql", sql_old)

        if settings.SNOWFLAKE_MOCK_MODE:
            set_parts = ["UPDATED_TS = ?"]
            params: List[Any] = [now]
            col_map = {
                "risk_name": "RISK_NAME",
                "risk_category": "RISK_CATEGORY",
                "risk_priority": "RISK_PRIORITY",
                "likelihood_score": "LIKELIHOOD_SCORE",
                "impact_score": "IMPACT_SCORE",
                "risk_score": "RISK_SCORE",
                "business_impact_desc": "BUSINESS_IMPACT_DESC",
                "validation_sql": "VALIDATION_SQL",
                "status": "STATUS",
                "approved_by": "APPROVED_BY",
                "last_compile_error": "LAST_COMPILE_ERROR",
            }
            for k, col in col_map.items():
                if k in updates and updates[k] is not None:
                    set_parts.append(f"{col} = ?")
                    params.append(updates[k])
            if "is_shared_template" in updates and updates["is_shared_template"] is not None:
                set_parts.append("IS_SHARED_TEMPLATE = ?")
                params.append(1 if updates["is_shared_template"] else 0)
            params.append(rule_id)
            self._mock_execute(f"UPDATE RBT_TEST_RULES SET {', '.join(set_parts)} WHERE RULE_ID = ?", tuple(params))

            if status_old != status_new or sql_old != sql_new:
                ver_row = self._mock_fetchall(
                    "SELECT COALESCE(MAX(VERSION_NUM), 0) AS MAX_V FROM RBT_TEST_RULE_HISTORY WHERE RULE_ID = ?",
                    (rule_id,),
                )
                next_ver = int(ver_row[0]["MAX_V"]) + 1
                self._mock_execute(
                    "INSERT INTO RBT_TEST_RULE_HISTORY VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_new_id("hist"), rule_id, next_ver, status_old, status_new, sql_old, sql_new, modified_by, now),
                )
            return self.get_rule(rule_id)  # type: ignore[return-value]

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            set_parts = ["UPDATED_TS = CURRENT_TIMESTAMP()"]
            params_sf: List[Any] = []
            col_map = {
                "risk_name": "RISK_NAME",
                "risk_category": "RISK_CATEGORY",
                "risk_priority": "RISK_PRIORITY",
                "likelihood_score": "LIKELIHOOD_SCORE",
                "impact_score": "IMPACT_SCORE",
                "risk_score": "RISK_SCORE",
                "business_impact_desc": "BUSINESS_IMPACT_DESC",
                "validation_sql": "VALIDATION_SQL",
                "status": "STATUS",
                "approved_by": "APPROVED_BY",
                "last_compile_error": "LAST_COMPILE_ERROR",
                "is_shared_template": "IS_SHARED_TEMPLATE",
            }
            for k, col in col_map.items():
                if k in updates and updates[k] is not None:
                    set_parts.append(f"{col} = %s")
                    params_sf.append(updates[k])
            params_sf.append(rule_id)
            cur.execute(
                f"UPDATE {self._meta_prefix}.RBT_TEST_RULES SET {', '.join(set_parts)} WHERE RULE_ID = %s",
                tuple(params_sf),
            )
            if status_old != status_new or sql_old != sql_new:
                cur.execute(
                    f"SELECT COALESCE(MAX(VERSION_NUM), 0) FROM {self._meta_prefix}.RBT_TEST_RULE_HISTORY WHERE RULE_ID = %s",
                    (rule_id,),
                )
                next_ver = int(cur.fetchone()[0]) + 1
                cur.execute(
                    f"""
                    INSERT INTO {self._meta_prefix}.RBT_TEST_RULE_HISTORY
                    (HISTORY_ID, RULE_ID, VERSION_NUM, STATUS_OLD, STATUS_NEW, VALIDATION_SQL_OLD, VALIDATION_SQL_NEW, MODIFIED_BY)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (_new_id("hist"), rule_id, next_ver, status_old, status_new, sql_old, sql_new, modified_by),
                )
            return self.get_rule(rule_id)  # type: ignore[return-value]
        finally:
            conn.close()

    def list_rule_history(self, rule_id: str) -> List[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            return self._mock_fetchall(
                "SELECT * FROM RBT_TEST_RULE_HISTORY WHERE RULE_ID = ? ORDER BY VERSION_NUM DESC",
                (rule_id,),
            )
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT * FROM {self._meta_prefix}.RBT_TEST_RULE_HISTORY WHERE RULE_ID = %s ORDER BY VERSION_NUM DESC",
                (rule_id,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def list_marketplace_templates(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            clauses = ["r.IS_SHARED_TEMPLATE = 1", "r.STATUS = 'APPROVED'"]
            params: List[Any] = []
            if category:
                clauses.append("r.RISK_CATEGORY = ?")
                params.append(category)
            sql = (
                "SELECT r.*, t.TEAM_NAME AS SOURCE_TEAM_NAME, d.DOMAIN_NAME AS SOURCE_DOMAIN_NAME "
                "FROM RBT_TEST_RULES r "
                "JOIN RBT_TEAMS t ON r.TEAM_ID = t.TEAM_ID "
                "JOIN RBT_DOMAIN_REGISTRY d ON r.DOMAIN_ID = d.DOMAIN_ID "
                f"WHERE {' AND '.join(clauses)} ORDER BY r.UPDATED_TS DESC"
            )
            return self._mock_fetchall(sql, tuple(params))

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            clauses = ["r.IS_SHARED_TEMPLATE = TRUE", "r.STATUS = 'APPROVED'"]
            params = []
            if category:
                clauses.append("r.RISK_CATEGORY = %s")
                params.append(category)
            cur.execute(
                "SELECT r.*, t.TEAM_NAME AS SOURCE_TEAM_NAME, d.DOMAIN_NAME AS SOURCE_DOMAIN_NAME "
                f"FROM {self._meta_prefix}.RBT_TEST_RULES r "
                f"JOIN {self._meta_prefix}.RBT_TEAMS t ON r.TEAM_ID = t.TEAM_ID "
                f"JOIN {self._meta_prefix}.RBT_DOMAIN_REGISTRY d ON r.DOMAIN_ID = d.DOMAIN_ID "
                f"WHERE {' AND '.join(clauses)} ORDER BY r.UPDATED_TS DESC",
                tuple(params),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Rule SQL Execution (Dry-Run & Gate Execution)
    # -----------------------------------------------------------------------

    def _resolve_dataset_fqns(
        self,
        team: Dict[str, Any],
        domain: Dict[str, Any],
        target_schema_override: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Return (target_fqn, baseline_fqn) with sqlglot identifier sanitization."""
        if settings.SNOWFLAKE_MOCK_MODE:
            baseline_fqn = domain["TARGET_TABLE"]
            if not target_schema_override or not target_schema_override.strip():
                return baseline_fqn, baseline_fqn
            safe_override = sanitize_sql_identifier(target_schema_override, "target_schema_override")
            leaf = safe_override.split(".")[-1]
            for candidate in (f"{leaf}_{domain['TARGET_TABLE']}", leaf):
                if self._mock_fetchall(f"PRAGMA table_info({candidate})"):
                    return candidate, baseline_fqn
            return baseline_fqn, baseline_fqn

        baseline_fqn = f"{team['SNOWFLAKE_DATABASE']}.{domain['TARGET_SCHEMA']}.{domain['TARGET_TABLE']}"
        if not target_schema_override or not target_schema_override.strip():
            return baseline_fqn, baseline_fqn

        safe_override = sanitize_sql_identifier(target_schema_override, "target_schema_override")
        parts = safe_override.split(".")
        if len(parts) == 3:
            target_fqn = safe_override
        elif len(parts) == 2:
            target_fqn = f"{safe_override}.{domain['TARGET_TABLE']}"
        else:
            target_fqn = f"{team['SNOWFLAKE_DATABASE']}.{safe_override}.{domain['TARGET_TABLE']}"
        return target_fqn, baseline_fqn

    async def execute_rule_sql(
        self,
        sql: str,
        team: Dict[str, Any],
        domain: Dict[str, Any],
        planning_cycle_id: Optional[str] = None,
        snapshot_ts: Optional[str] = None,
        capture_transient_detail_id: Optional[str] = None,
        target_schema_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a validated rule SQL query and return count, top 5 samples, and optional transient table ref."""
        started = time.perf_counter()
        target_fqn, baseline_fqn = self._resolve_dataset_fqns(
            team, domain, target_schema_override=target_schema_override
        )

        wrapped_sql, params = wrap_with_target_dataset_cte(
            sql=sql,
            target_fqn=target_fqn,
            temporal_cycle_column=domain.get("TEMPORAL_CYCLE_COLUMN"),
            snapshot_ts_column=domain.get("SNAPSHOT_TS_COLUMN"),
            planning_cycle_id=planning_cycle_id,
            snapshot_ts=snapshot_ts,
            max_hierarchy_depth=domain.get("MAX_HIERARCHY_DEPTH", 10),
            baseline_fqn=baseline_fqn,
        )

        if settings.SNOWFLAKE_MOCK_MODE:
            sqlite_sql = self._transpile_to_sqlite(wrapped_sql)
            assert self._conn is not None
            with self._lock:
                cur = self._conn.cursor()
                cur.execute(sqlite_sql, params)
                rows = [dict(r) for r in cur.fetchall()]
            failed_count = len(rows)
            samples = rows[:5]
            failure_ref: Optional[str] = None
            if capture_transient_detail_id and failed_count > 5:
                failure_ref = f"RBT_GLOBAL_PLATFORM.FAILURES_TRANSIENT.FAIL_{capture_transient_detail_id}"
                with self._lock:
                    cur = self._conn.cursor()
                    cur.executemany(
                        "INSERT INTO MOCK_TRANSIENT_FAILURES VALUES (?, ?)",
                        [(failure_ref, json.dumps(r)) for r in rows[: settings.MAX_EXPORT_CSV_ROWS]],
                    )
                    self._conn.commit()
            elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
            return {
                "status": "FAIL" if failed_count > 0 else "PASS",
                "failed_row_count": failed_count,
                "sample_failing_keys": samples,
                "failure_table_ref": failure_ref,
                "executed_sql": wrapped_sql,
                "execution_ms": elapsed_ms,
                "error_message": None,
            }

        # Live Snowflake Query Execution (runs inside worker thread via asyncio.to_thread)
        def _run_live() -> Dict[str, Any]:
            with self._lock:
                conn = self._get_live_connection(warehouse=team["SNOWFLAKE_WAREHOUSE"])
                try:
                    cur = conn.cursor()
                    cur.execute(wrapped_sql, params or None)
                    cols = [d[0] for d in (cur.description or [])]
                    all_rows = cur.fetchmany(settings.MAX_EXPORT_CSV_ROWS)
                    dict_rows = [dict(zip(cols, r)) for r in all_rows]
                    # Get exact count if >= MAX_EXPORT_CSV_ROWS
                    if len(dict_rows) == settings.MAX_EXPORT_CSV_ROWS:
                        cur.execute(f"SELECT COUNT(*) FROM ({wrapped_sql})", params or None)
                        failed_count = int(cur.fetchone()[0])
                    else:
                        failed_count = len(dict_rows)

                    failure_ref_sf: Optional[str] = None
                    if capture_transient_detail_id and failed_count > 5:
                        failure_ref_sf = f"{self._meta_prefix}.RBT_FAIL_{capture_transient_detail_id}"
                        cur.execute(
                            f"CREATE OR REPLACE TRANSIENT TABLE {failure_ref_sf} DATA_RETENTION_TIME_IN_DAYS = 7 AS SELECT * FROM ({wrapped_sql}) LIMIT {int(settings.MAX_EXPORT_CSV_ROWS)}",
                            params or None,
                        )
                    safe_samples = json.loads(json.dumps(dict_rows[:5], default=str))
                    elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
                    return {
                        "status": "FAIL" if failed_count > 0 else "PASS",
                        "failed_row_count": failed_count,
                        "sample_failing_keys": safe_samples,
                        "failure_table_ref": failure_ref_sf,
                        "executed_sql": wrapped_sql,
                        "execution_ms": elapsed_ms,
                        "error_message": None,
                    }
                finally:
                    conn.close()

        return await asyncio.to_thread(_run_live)

    # -----------------------------------------------------------------------
    # Execution Runs & Details Persistence
    # -----------------------------------------------------------------------

    def create_execution_run(
        self,
        team_id: str,
        domain_id: str,
        triggered_by: str,
        planning_cycle_id: Optional[str],
        snapshot_ts: Optional[str],
        environment: str = "PROD",
        target_schema_override: Optional[str] = None,
    ) -> str:
        run_id = _new_id("run")
        now = _utcnow_iso()
        if settings.SNOWFLAKE_MOCK_MODE:
            self._mock_execute(
                """
                INSERT INTO RBT_EXECUTION_RUNS
                (RUN_ID, TEAM_ID, DOMAIN_ID, TRIGGERED_BY, EXECUTION_STATUS, PLANNING_CYCLE_ID, SNAPSHOT_TS,
                 GATE_DECISION, TOTAL_RULES_RUN, P1_FAILURES, P2_FAILURES, ENVIRONMENT, TARGET_SCHEMA_OVERRIDE,
                 COVERAGE_PCT, RELEASE_CONFIDENCE_SCORE, SCHEMA_DRIFT_STATUS, SCHEMA_DRIFT_DETAILS,
                 AI_TRIAGE_SUMMARY, OVERRIDE_BY, OVERRIDE_REASON, OVERRIDE_TS, RUN_TS, COMPLETED_TS)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, team_id, domain_id, triggered_by, "RUNNING", planning_cycle_id, snapshot_ts, "PENDING", 0, 0, 0, environment, target_schema_override, 0.0, 100.0, "PASS", "[]", None, None, None, None, now, None),
            )
            return run_id
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {self._meta_prefix}.RBT_EXECUTION_RUNS
                (RUN_ID, TEAM_ID, DOMAIN_ID, TRIGGERED_BY, EXECUTION_STATUS, PLANNING_CYCLE_ID, SNAPSHOT_TS,
                 GATE_DECISION, ENVIRONMENT, TARGET_SCHEMA_OVERRIDE)
                VALUES (%s, %s, %s, %s, 'RUNNING', %s, %s, 'PENDING', %s, %s)
                """,
                (run_id, team_id, domain_id, triggered_by, planning_cycle_id, snapshot_ts, environment, target_schema_override),
            )
            return run_id
        finally:
            conn.close()

    def finalize_execution_run(
        self,
        run_id: str,
        execution_status: str,
        gate_decision: str,
        total_rules_run: int,
        p1_failures: int,
        p2_failures: int,
        ai_triage_summary: Optional[str],
        details: List[Dict[str, Any]],
        coverage_pct: float = 0.0,
        release_confidence_score: float = 100.0,
        schema_drift_status: str = "PASS",
        schema_drift_details: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        now = _utcnow_iso()
        drift_json = json.dumps(schema_drift_details or [], default=str)
        if settings.SNOWFLAKE_MOCK_MODE:
            self._mock_execute(
                """
                UPDATE RBT_EXECUTION_RUNS
                SET EXECUTION_STATUS = ?, GATE_DECISION = ?, TOTAL_RULES_RUN = ?,
                    P1_FAILURES = ?, P2_FAILURES = ?, COVERAGE_PCT = ?, RELEASE_CONFIDENCE_SCORE = ?,
                    SCHEMA_DRIFT_STATUS = ?, SCHEMA_DRIFT_DETAILS = ?,
                    AI_TRIAGE_SUMMARY = ?, COMPLETED_TS = ?
                WHERE RUN_ID = ?
                """,
                (
                    execution_status,
                    gate_decision,
                    total_rules_run,
                    p1_failures,
                    p2_failures,
                    coverage_pct,
                    release_confidence_score,
                    schema_drift_status,
                    drift_json,
                    ai_triage_summary,
                    now,
                    run_id,
                ),
            )
            assert self._conn is not None
            with self._lock:
                cur = self._conn.cursor()
                cur.executemany(
                    "INSERT INTO RBT_EXECUTION_DETAILS VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            d["detail_id"],
                            run_id,
                            d["rule_id"],
                            d["risk_name"],
                            d["risk_priority"],
                            d["rule_run_status"],
                            d["failed_row_count"],
                            json.dumps(d["sample_failing_keys"], default=str),
                            d.get("failure_table_ref"),
                            d.get("error_message"),
                            d["execution_ms"],
                        )
                        for d in details
                    ],
                )
                self._conn.commit()
            return

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {self._meta_prefix}.RBT_EXECUTION_RUNS
                SET EXECUTION_STATUS = %s, GATE_DECISION = %s, TOTAL_RULES_RUN = %s,
                    P1_FAILURES = %s, P2_FAILURES = %s, COVERAGE_PCT = %s, RELEASE_CONFIDENCE_SCORE = %s,
                    SCHEMA_DRIFT_STATUS = %s, SCHEMA_DRIFT_DETAILS = PARSE_JSON(%s),
                    AI_TRIAGE_SUMMARY = %s, COMPLETED_TS = CURRENT_TIMESTAMP()
                WHERE RUN_ID = %s
                """,
                (
                    execution_status,
                    gate_decision,
                    total_rules_run,
                    p1_failures,
                    p2_failures,
                    coverage_pct,
                    release_confidence_score,
                    schema_drift_status,
                    drift_json,
                    ai_triage_summary,
                    run_id,
                ),
            )
            for d in details:
                cur.execute(
                    f"""
                    INSERT INTO {self._meta_prefix}.RBT_EXECUTION_DETAILS
                    (DETAIL_ID, RUN_ID, RULE_ID, RISK_NAME, RISK_PRIORITY, RULE_RUN_STATUS,
                     FAILED_ROW_COUNT, SAMPLE_FAILING_KEYS, FAILURE_TABLE_REF, ERROR_MESSAGE, EXECUTION_MS)
                    SELECT %s, %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), %s, %s, %s
                    """,
                    (
                        d["detail_id"],
                        run_id,
                        d["rule_id"],
                        d["risk_name"],
                        d["risk_priority"],
                        d["rule_run_status"],
                        d["failed_row_count"],
                        json.dumps(d["sample_failing_keys"]),
                        d.get("failure_table_ref"),
                        d.get("error_message"),
                        d["execution_ms"],
                    ),
                )
        finally:
            conn.close()

    def list_runs(self, team_id: str) -> List[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            return self._mock_fetchall(
                "SELECT r.*, d.DOMAIN_NAME FROM RBT_EXECUTION_RUNS r "
                "LEFT JOIN RBT_DOMAIN_REGISTRY d ON r.DOMAIN_ID = d.DOMAIN_ID "
                "WHERE r.TEAM_ID = ? ORDER BY r.RUN_TS DESC",
                (team_id,),
            )
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT r.*, d.DOMAIN_NAME FROM {self._meta_prefix}.RBT_EXECUTION_RUNS r "
                f"LEFT JOIN {self._meta_prefix}.RBT_DOMAIN_REGISTRY d ON r.DOMAIN_ID = d.DOMAIN_ID "
                "WHERE r.TEAM_ID = %s ORDER BY r.RUN_TS DESC",
                (team_id,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            rows = self._mock_fetchall(
                "SELECT r.*, d.DOMAIN_NAME FROM RBT_EXECUTION_RUNS r "
                "LEFT JOIN RBT_DOMAIN_REGISTRY d ON r.DOMAIN_ID = d.DOMAIN_ID "
                "WHERE r.RUN_ID = ?",
                (run_id,),
            )
            return rows[0] if rows else None
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT r.*, d.DOMAIN_NAME FROM {self._meta_prefix}.RBT_EXECUTION_RUNS r "
                f"LEFT JOIN {self._meta_prefix}.RBT_DOMAIN_REGISTRY d ON r.DOMAIN_ID = d.DOMAIN_ID "
                "WHERE r.RUN_ID = %s",
                (run_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        finally:
            conn.close()

    def get_run_details(self, run_id: str) -> List[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            return self._mock_fetchall("SELECT * FROM RBT_EXECUTION_DETAILS WHERE RUN_ID = ?", (run_id,))
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM {self._meta_prefix}.RBT_EXECUTION_DETAILS WHERE RUN_ID = %s", (run_id,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_run_detail(self, run_id: str, detail_id: str) -> Optional[Dict[str, Any]]:
        if settings.SNOWFLAKE_MOCK_MODE:
            rows = self._mock_fetchall(
                "SELECT * FROM RBT_EXECUTION_DETAILS WHERE RUN_ID = ? AND DETAIL_ID = ?",
                (run_id, detail_id),
            )
            return rows[0] if rows else None
        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT * FROM {self._meta_prefix}.RBT_EXECUTION_DETAILS WHERE RUN_ID = %s AND DETAIL_ID = %s",
                (run_id, detail_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        finally:
            conn.close()

    def fetch_failure_export_rows(self, detail: Dict[str, Any]) -> List[Dict[str, Any]]:
        ref = detail.get("FAILURE_TABLE_REF")
        if settings.SNOWFLAKE_MOCK_MODE:
            if ref:
                rows = self._mock_fetchall(
                    "SELECT ROW_JSON FROM MOCK_TRANSIENT_FAILURES WHERE TABLE_REF = ? LIMIT ?",
                    (ref, settings.MAX_EXPORT_CSV_ROWS),
                )
                if rows:
                    return [json.loads(r["ROW_JSON"]) for r in rows]
            samples = detail.get("SAMPLE_FAILING_KEYS")
            if isinstance(samples, str):
                return json.loads(samples)
            return samples or []

        if not ref:
            samples = detail.get("SAMPLE_FAILING_KEYS")
            return json.loads(samples) if isinstance(samples, str) else (samples or [])

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM {ref} LIMIT {int(settings.MAX_EXPORT_CSV_ROWS)}")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()

    def override_execution_run(self, run_id: str, override_by: str, override_reason: str) -> Dict[str, Any]:
        now = _utcnow_iso()
        if settings.SNOWFLAKE_MOCK_MODE:
            self._mock_execute(
                """
                UPDATE RBT_EXECUTION_RUNS
                SET GATE_DECISION = 'OVERRIDDEN_PASS', OVERRIDE_BY = ?, OVERRIDE_REASON = ?, OVERRIDE_TS = ?
                WHERE RUN_ID = ?
                """,
                (override_by, override_reason, now, run_id),
            )
            return self.get_run(run_id)  # type: ignore[return-value]

        conn = self._get_live_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {self._meta_prefix}.RBT_EXECUTION_RUNS
                SET GATE_DECISION = 'OVERRIDDEN_PASS', OVERRIDE_BY = %s, OVERRIDE_REASON = %s, OVERRIDE_TS = CURRENT_TIMESTAMP()
                WHERE RUN_ID = %s
                """,
                (override_by, override_reason, run_id),
            )
            return self.get_run(run_id)  # type: ignore[return-value]
        finally:
            conn.close()


class _NoCloseConnProxy:
    """Proxy wrapping a cached SSO Snowflake connection so callers' conn.close() is a no-op."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def close(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


sf_client = SnowflakePlatformClient()
