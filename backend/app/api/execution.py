import asyncio
import csv
import io
import json
import time
import uuid
from typing import Any, Dict, List
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.auth.dependencies import get_service_or_user, require_role
from app.config import settings
from app.db.snowflake_client import sf_client
from app.schemas.models import (
    BreakGlassOverrideRequest,
    CurrentUser,
    GateDecision,
    RuleResultOut,
    RunGateRequest,
    RunGateResponse,
    RunMetricsOut,
)

router = APIRouter(prefix="/api/v1", tags=["Execution Gate & Triage"])

_BACKGROUND_TASKS: set = set()


def _parse_sample_keys(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except Exception:
            return []
    return []


def _build_run_response(run: Dict[str, Any], details: List[Dict[str, Any]]) -> RunGateResponse:
    gate_decision: GateDecision = run.get("GATE_DECISION") or "PENDING"
    allow_downstream = gate_decision in ("PASS", "PASS_WITH_WARNINGS", "OVERRIDDEN_PASS")
    rule_results: List[RuleResultOut] = []
    total_ms = 0
    for d in details:
        exec_ms = int(d.get("EXECUTION_MS") or 0)
        total_ms += exec_ms
        failed_cnt = int(d.get("FAILED_ROW_COUNT") or 0)
        export_url = (
            f"/api/v1/runs/{run['RUN_ID']}/details/{d['DETAIL_ID']}/export-csv"
            if failed_cnt > 0
            else None
        )
        rule_results.append(
            RuleResultOut(
                detail_id=d["DETAIL_ID"],
                rule_id=d["RULE_ID"],
                risk_name=d.get("RISK_NAME") or "",
                risk_priority=d["RISK_PRIORITY"],
                rule_run_status=d["RULE_RUN_STATUS"],
                failed_row_count=failed_cnt,
                sample_failing_keys=_parse_sample_keys(d.get("SAMPLE_FAILING_KEYS")),
                export_csv_url=export_url,
                error_message=d.get("ERROR_MESSAGE"),
                execution_ms=exec_ms,
            )
        )

    return RunGateResponse(
        run_id=run["RUN_ID"],
        team_id=run["TEAM_ID"],
        domain_id=run["DOMAIN_ID"],
        domain_name=run.get("DOMAIN_NAME"),
        triggered_by=run.get("TRIGGERED_BY"),
        execution_status=run["EXECUTION_STATUS"],
        gate_decision=gate_decision,
        allow_downstream=allow_downstream,
        planning_cycle_id=run.get("PLANNING_CYCLE_ID"),
        snapshot_ts=run.get("SNAPSHOT_TS"),
        poll_url=f"/api/v1/runs/{run['RUN_ID']}",
        metrics=RunMetricsOut(
            total_rules_run=int(run.get("TOTAL_RULES_RUN") or len(details)),
            p1_failures=int(run.get("P1_FAILURES") or 0),
            p2_failures=int(run.get("P2_FAILURES") or 0),
            duration_ms=total_ms,
        ),
        ai_triage_summary=run.get("AI_TRIAGE_SUMMARY"),
        override_by=run.get("OVERRIDE_BY"),
        override_reason=run.get("OVERRIDE_REASON"),
        override_ts=run.get("OVERRIDE_TS"),
        run_ts=run.get("RUN_TS"),
        completed_ts=run.get("COMPLETED_TS"),
        rule_results=rule_results,
    )


async def _dispatch_webhook_alert(webhook_url: str, payload: Dict[str, Any]) -> None:
    if settings.SNOWFLAKE_MOCK_MODE or not webhook_url or "hooks.slack.com/services/T000" in webhook_url:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(webhook_url, json=payload)
    except Exception:
        pass


async def _execute_gate_run(
    run_id: str,
    team: Dict[str, Any],
    domain: Dict[str, Any],
    approved_rules: List[Dict[str, Any]],
    planning_cycle_id: str | None,
    snapshot_ts: str | None,
    cortex_model: str | None = None,
) -> None:
    semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_QUERIES)

    async def _run_single(rule: Dict[str, Any]) -> Dict[str, Any]:
        detail_id = f"det_{uuid.uuid4().hex[:8]}"
        t0 = time.perf_counter()
        async with semaphore:
            try:
                res = await asyncio.wait_for(
                    sf_client.execute_rule_sql(
                        sql=rule["VALIDATION_SQL"],
                        team=team,
                        domain=domain,
                        planning_cycle_id=planning_cycle_id,
                        snapshot_ts=snapshot_ts,
                        capture_transient_detail_id=detail_id,
                    ),
                    timeout=float(settings.STATEMENT_TIMEOUT_IN_SECONDS),
                )
                return {
                    "detail_id": detail_id,
                    "rule_id": rule["RULE_ID"],
                    "risk_name": rule["RISK_NAME"],
                    "risk_priority": rule["RISK_PRIORITY"],
                    "rule_run_status": res["status"],
                    "failed_row_count": res["failed_row_count"],
                    "sample_failing_keys": res["sample_failing_keys"],
                    "failure_table_ref": res.get("failure_table_ref"),
                    "error_message": None,
                    "execution_ms": res["execution_ms"],
                }
            except asyncio.TimeoutError:
                return {
                    "detail_id": detail_id,
                    "rule_id": rule["RULE_ID"],
                    "risk_name": rule["RISK_NAME"],
                    "risk_priority": rule["RISK_PRIORITY"],
                    "rule_run_status": "TIMEOUT",
                    "failed_row_count": 0,
                    "sample_failing_keys": [],
                    "failure_table_ref": None,
                    "error_message": f"Exceeded STATEMENT_TIMEOUT_IN_SECONDS={settings.STATEMENT_TIMEOUT_IN_SECONDS}",
                    "execution_ms": int((time.perf_counter() - t0) * 1000),
                }
            except Exception as exc:
                return {
                    "detail_id": detail_id,
                    "rule_id": rule["RULE_ID"],
                    "risk_name": rule["RISK_NAME"],
                    "risk_priority": rule["RISK_PRIORITY"],
                    "rule_run_status": "ERROR",
                    "failed_row_count": 0,
                    "sample_failing_keys": [],
                    "failure_table_ref": None,
                    "error_message": str(exc),
                    "execution_ms": int((time.perf_counter() - t0) * 1000),
                }

    details = await asyncio.gather(*[_run_single(r) for r in approved_rules])

    p1_failures = sum(
        1
        for d in details
        if d["risk_priority"] == "P1_BLOCKER" and d["rule_run_status"] in ("FAIL", "ERROR", "TIMEOUT")
    )
    p2_failures = sum(
        1
        for d in details
        if d["risk_priority"] == "P2_WARNING" and d["rule_run_status"] in ("FAIL", "ERROR", "TIMEOUT")
    )

    if p1_failures > 0:
        gate_decision = "BLOCKED_DOWNSTREAM"
    elif p2_failures > 0:
        gate_decision = "PASS_WITH_WARNINGS"
    else:
        gate_decision = "PASS"

    ai_triage_summary: str | None = None
    if gate_decision in ("BLOCKED_DOWNSTREAM", "PASS_WITH_WARNINGS"):
        failed_names = [
            f"{d['risk_name']} ({d['failed_row_count']} rows)"
            for d in details
            if d["rule_run_status"] != "PASS"
        ]
        triage_prompt = (
            f"Summarize the downstream impact for {domain['DOMAIN_NAME']} "
            f"(consumer: {domain.get('DOWNSTREAM_CONSUMER')}, cycle: {planning_cycle_id or 'ALL'}):\n"
            f"Gate Decision: {gate_decision}. Failing checks: {', '.join(failed_names)}."
        )
        ai_triage_summary = sf_client.invoke_cortex_text(
            triage_prompt,
            warehouse=team["SNOWFLAKE_WAREHOUSE"],
            model_name=cortex_model,
        )

    sf_client.finalize_execution_run(
        run_id=run_id,
        execution_status="COMPLETED",
        gate_decision=gate_decision,
        total_rules_run=len(details),
        p1_failures=p1_failures,
        p2_failures=p2_failures,
        ai_triage_summary=ai_triage_summary,
        details=details,
    )

    if gate_decision in ("BLOCKED_DOWNSTREAM", "PASS_WITH_WARNINGS") and team.get("ALERT_WEBHOOK_URL"):
        await _dispatch_webhook_alert(
            team["ALERT_WEBHOOK_URL"],
            {
                "run_id": run_id,
                "team_id": team["TEAM_ID"],
                "domain_id": domain["DOMAIN_ID"],
                "gate_decision": gate_decision,
                "p1_failures": p1_failures,
                "p2_failures": p2_failures,
                "ai_triage_summary": ai_triage_summary,
            },
        )


@router.post("/execution/run-gate", response_model=RunGateResponse)
async def run_preflight_gate(
    payload: RunGateRequest,
    user: CurrentUser = Depends(get_service_or_user),
):
    if user.auth_method == "API_KEY" and user.team_id != payload.team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key does not belong to the requested team_id.",
        )

    team = sf_client.get_team(payload.team_id)
    domain = sf_client.get_domain(payload.domain_id)
    if not team or not domain or domain["TEAM_ID"] != payload.team_id:
        raise HTTPException(status_code=404, detail="Team or domain not found.")

    approved_rules = sf_client.list_rules(
        team_id=payload.team_id,
        domain_id=payload.domain_id,
        status="APPROVED",
    )

    run_id = sf_client.create_execution_run(
        team_id=payload.team_id,
        domain_id=payload.domain_id,
        triggered_by=payload.triggered_by,
        planning_cycle_id=payload.planning_cycle_id,
        snapshot_ts=payload.snapshot_ts,
    )

    if payload.execution_mode == "ASYNC":
        task = asyncio.create_task(
            _execute_gate_run(
                run_id=run_id,
                team=team,
                domain=domain,
                approved_rules=approved_rules,
                planning_cycle_id=payload.planning_cycle_id,
                snapshot_ts=payload.snapshot_ts,
                cortex_model=payload.cortex_model,
            )
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

        run_row = sf_client.get_run(run_id)
        resp_model = _build_run_response(run_row, [])  # type: ignore[arg-type]
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=resp_model.model_dump(mode="json"))

    # SYNC mode
    try:
        await asyncio.wait_for(
            _execute_gate_run(
                run_id=run_id,
                team=team,
                domain=domain,
                approved_rules=approved_rules,
                planning_cycle_id=payload.planning_cycle_id,
                snapshot_ts=payload.snapshot_ts,
                cortex_model=payload.cortex_model,
            ),
            timeout=float(settings.SYNC_HTTP_TIMEOUT_SECONDS),
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"SYNC gate execution exceeded {settings.SYNC_HTTP_TIMEOUT_SECONDS}s; poll /api/v1/runs/{run_id}.",
        ) from exc

    run_row = sf_client.get_run(run_id)
    details = sf_client.get_run_details(run_id)
    resp_model = _build_run_response(run_row, details)  # type: ignore[arg-type]
    http_code = (
        status.HTTP_422_UNPROCESSABLE_ENTITY
        if resp_model.gate_decision == "BLOCKED_DOWNSTREAM"
        else status.HTTP_200_OK
    )
    return JSONResponse(status_code=http_code, content=resp_model.model_dump(mode="json"))


@router.get("/teams/{team_id}/runs", response_model=List[RunGateResponse])
async def list_team_runs(
    team_id: str,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    runs = sf_client.list_runs(team_id)
    return [_build_run_response(r, sf_client.get_run_details(r["RUN_ID"])) for r in runs]


@router.get("/runs/{run_id}", response_model=RunGateResponse)
async def get_run_status(
    run_id: str,
    user: CurrentUser = Depends(get_service_or_user),
):
    run = sf_client.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Execution run '{run_id}' not found.")
    if user.auth_method == "API_KEY" and user.team_id != run["TEAM_ID"]:
        raise HTTPException(status_code=403, detail="API Key does not match run team_id.")
    details = sf_client.get_run_details(run_id)
    return _build_run_response(run, details)


@router.get("/runs/{run_id}/details/{detail_id}/export-csv")
async def export_failing_keys_csv(
    run_id: str,
    detail_id: str,
    user: CurrentUser = Depends(require_role("AUTHOR")),
):
    detail = sf_client.get_run_detail(run_id, detail_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Execution detail not found.")

    rows = sf_client.fetch_failure_export_rows(detail)
    output = io.StringIO()
    if rows:
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    else:
        output.write("MESSAGE\nNo failing rows recorded\n")

    output.seek(0)
    filename = f"rbt_failures_{run_id}_{detail_id}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/runs/{run_id}/override", response_model=RunGateResponse)
async def break_glass_override(
    run_id: str,
    payload: BreakGlassOverrideRequest,
    user: CurrentUser = Depends(require_role("APPROVER")),
):
    run = sf_client.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Execution run '{run_id}' not found.")
    if run.get("GATE_DECISION") != "BLOCKED_DOWNSTREAM":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Break-glass override only applies to BLOCKED_DOWNSTREAM runs (current: {run.get('GATE_DECISION')}).",
        )

    updated = sf_client.override_execution_run(
        run_id=run_id,
        override_by=user.email,
        override_reason=payload.override_reason,
    )
    details = sf_client.get_run_details(run_id)
    return _build_run_response(updated, details)
