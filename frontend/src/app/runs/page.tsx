"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertOctagon,
  Download,
  PlayCircle,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  X,
} from "lucide-react";
import { useAppContext } from "@/components/AppContext";
import { GateDecisionBadge, PriorityBadge } from "@/components/RoleBadge";
import { Domain, ExecutionRun, rbtApi } from "@/lib/api";

export default function RunsTriagePage() {
  const { activeTeamId, authHeaders, role, cortexModel } = useAppContext();
  const [runs, setRuns] = useState<ExecutionRun[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [selectedRun, setSelectedRun] = useState<ExecutionRun | null>(null);

  // Trigger Gate Modal
  const [triggerOpen, setTriggerOpen] = useState(false);
  const [trigDomainId, setTrigDomainId] = useState("");
  const [trigMode, setTrigMode] = useState<"SYNC" | "ASYNC">("SYNC");
  const [trigCycle, setTrigCycle] = useState("2025_W42_POR");
  const [triggering, setTriggering] = useState(false);

  // Break-Glass Override Modal
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [overrideErr, setOverrideErr] = useState<string | null>(null);

  const canOverride = role === "APPROVER" || role === "ADMIN";

  const loadData = useCallback(async () => {
    if (!activeTeamId) return;
    const [rList, dList] = await Promise.all([
      rbtApi.listRuns(authHeaders, activeTeamId),
      rbtApi.listDomains(authHeaders, activeTeamId),
    ]);
    setRuns(rList);
    setDomains(dList);
    if (dList.length > 0 && !trigDomainId) setTrigDomainId(dList[0].domain_id);
    if (rList.length > 0) {
      setSelectedRun((prev) => {
        if (prev) {
          const updated = rList.find((x) => x.run_id === prev.run_id);
          if (updated) return updated;
        }
        return rList[0];
      });
    } else {
      setSelectedRun(null);
    }
  }, [activeTeamId, authHeaders.email, authHeaders.role]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Poll if selected run is RUNNING
  useEffect(() => {
    if (!selectedRun || selectedRun.execution_status !== "RUNNING") return;
    const timer = setInterval(async () => {
      const updated = await rbtApi.getRun(authHeaders, selectedRun.run_id);
      setSelectedRun(updated);
      if (updated.execution_status !== "RUNNING") {
        loadData();
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [selectedRun?.run_id, selectedRun?.execution_status]);

  const handleTriggerGate = async (e: React.FormEvent) => {
    e.preventDefault();
    setTriggering(true);
    try {
      const res = await rbtApi.runGate(authHeaders, {
        team_id: activeTeamId,
        domain_id: trigDomainId,
        triggered_by: "MANUAL_UI",
        execution_mode: trigMode,
        planning_cycle_id: trigCycle || undefined,
        cortex_model: cortexModel,
      });
      setTriggerOpen(false);
      await loadData();
      setSelectedRun(res);
    } finally {
      setTriggering(false);
    }
  };

  const handleOverride = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRun) return;
    setOverrideErr(null);
    try {
      const updated = await rbtApi.overrideRun(authHeaders, selectedRun.run_id, overrideReason);
      setSelectedRun(updated);
      setOverrideOpen(false);
      setOverrideReason("");
      await loadData();
    } catch (err) {
      setOverrideErr(err instanceof Error ? err.message : "Override failed");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Execution Gate Logs &amp; Triage</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Trigger SYNC/ASYNC pre-flight gates, inspect Cortex AI root-cause triage, download failing keys CSV, or
            execute emergency Break-Glass overrides.
          </p>
        </div>
        <button
          onClick={() => setTriggerOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-700"
        >
          <PlayCircle className="h-4 w-4" /> Trigger Pre-Flight Gate
        </button>
      </div>

      <div className="grid gap-6 lg:grid-cols-12">
        {/* Run History List */}
        <div className="space-y-2.5 lg:col-span-4">
          {runs.map((r) => (
            <button
              key={r.run_id}
              onClick={() => setSelectedRun(r)}
              className={`w-full rounded-xl border p-3.5 text-left transition ${
                selectedRun?.run_id === r.run_id
                  ? "border-blue-500 bg-blue-500/5 shadow-sm"
                  : "border-slate-200 bg-white hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs font-bold">{r.run_id}</span>
                <GateDecisionBadge decision={r.gate_decision} />
              </div>
              <div className="mt-1.5 text-xs font-medium">{r.domain_name || r.domain_id}</div>
              <div className="mt-1 flex items-center justify-between text-[11px] text-slate-500">
                <span>Cycle: {r.planning_cycle_id || "ALL"}</span>
                <span>{r.triggered_by}</span>
              </div>
            </button>
          ))}
        </div>

        {/* Selected Run Details */}
        <div className="lg:col-span-8">
          {selectedRun ? (
            <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-4 dark:border-slate-800">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="font-mono text-lg font-bold">{selectedRun.run_id}</h2>
                    <GateDecisionBadge decision={selectedRun.gate_decision} />
                    {selectedRun.execution_status === "RUNNING" && (
                      <span className="inline-flex items-center gap-1 text-xs text-blue-500">
                        <RefreshCw className="h-3.5 w-3.5 animate-spin" /> Polling...
                      </span>
                    )}
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    Dataset: {selectedRun.domain_name || selectedRun.domain_id} · Cycle:{" "}
                    <span className="font-mono">{selectedRun.planning_cycle_id || "ALL"}</span> · Duration:{" "}
                    {selectedRun.metrics.duration_ms} ms
                  </div>
                </div>

                {selectedRun.gate_decision === "BLOCKED_DOWNSTREAM" && (
                  <button
                    disabled={!canOverride}
                    onClick={() => setOverrideOpen(true)}
                    title={!canOverride ? "Requires APPROVER or ADMIN role" : "Emergency Break-Glass Override"}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-orange-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-orange-700 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <ShieldAlert className="h-4 w-4" /> Break-Glass Override
                  </button>
                )}
              </div>

              {/* Cortex AI Triage Banner */}
              {selectedRun.ai_triage_summary && (
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3.5 text-xs">
                  <div className="mb-1 flex items-center gap-1.5 font-semibold text-amber-400">
                    <Sparkles className="h-4 w-4" /> Cortex AI Root-Cause &amp; Downstream Impact Triage
                  </div>
                  <p className="text-slate-700 dark:text-slate-200">{selectedRun.ai_triage_summary}</p>
                </div>
              )}

              {/* Override Audit Banner */}
              {selectedRun.override_by && (
                <div className="rounded-lg border border-orange-500/30 bg-orange-500/10 p-3.5 text-xs">
                  <div className="flex items-center gap-1.5 font-semibold text-orange-400">
                    <AlertOctagon className="h-4 w-4" /> Break-Glass Override Logged by {selectedRun.override_by}
                  </div>
                  <p className="mt-1 text-slate-700 dark:text-slate-200">{selectedRun.override_reason}</p>
                </div>
              )}

              {/* Rule Results */}
              <div className="space-y-3">
                {selectedRun.rule_results.map((res) => (
                  <div key={res.detail_id} className="rounded-lg border border-slate-200 p-3.5 dark:border-slate-800">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold">{res.risk_name}</span>
                        <PriorityBadge priority={res.risk_priority} />
                      </div>
                      <div className="flex items-center gap-2 text-xs">
                        <span
                          className={`font-semibold ${
                            res.rule_run_status === "PASS" ? "text-emerald-500" : "text-rose-500"
                          }`}
                        >
                          {res.rule_run_status} ({res.failed_row_count} rows)
                        </span>
                        {res.failed_row_count > 0 && (
                          <button
                            onClick={() => rbtApi.downloadCsv(authHeaders, selectedRun.run_id, res.detail_id)}
                            className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
                          >
                            <Download className="h-3.5 w-3.5" /> Download Failing Keys CSV
                          </button>
                        )}
                      </div>
                    </div>

                    {res.sample_failing_keys.length > 0 && (
                      <pre className="mt-2.5 max-h-36 overflow-auto rounded bg-slate-950 p-2.5 font-mono text-[11px] text-slate-200">
                        {JSON.stringify(res.sample_failing_keys, null, 2)}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-slate-300 p-12 text-center text-sm text-slate-500 dark:border-slate-700">
              No execution runs found for this team.
            </div>
          )}
        </div>
      </div>

      {/* Trigger Gate Modal */}
      {triggerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <form
            onSubmit={handleTriggerGate}
            className="w-full max-w-md space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-800 dark:bg-slate-900"
          >
            <div className="flex items-center justify-between border-b border-slate-200 pb-3 dark:border-slate-800">
              <h3 className="text-base font-bold">Trigger Pre-Flight Gate</h3>
              <button type="button" onClick={() => setTriggerOpen(false)}>
                <X className="h-5 w-5" />
              </button>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Dataset</label>
              <select
                value={trigDomainId}
                onChange={(e) => setTrigDomainId(e.target.value)}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-800"
              >
                {domains.map((d) => (
                  <option key={d.domain_id} value={d.domain_id}>
                    {d.domain_name}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Execution Mode</label>
                <select
                  value={trigMode}
                  onChange={(e) => setTrigMode(e.target.value as "SYNC" | "ASYNC")}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-800"
                >
                  <option value="SYNC">SYNC (200 / 422)</option>
                  <option value="ASYNC">ASYNC (202 + Poll)</option>
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Planning Cycle ID</label>
                <input
                  value={trigCycle}
                  onChange={(e) => setTrigCycle(e.target.value)}
                  placeholder="2025_W42_POR"
                  className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 font-mono text-xs dark:border-slate-700"
                />
              </div>
            </div>
            <button
              type="submit"
              disabled={triggering}
              className="w-full rounded-lg bg-blue-600 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {triggering ? "Executing Gate..." : "Run Gate Validation"}
            </button>
          </form>
        </div>
      )}

      {/* Break-Glass Override Modal */}
      {overrideOpen && selectedRun && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <form
            onSubmit={handleOverride}
            className="w-full max-w-md space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-800 dark:bg-slate-900"
          >
            <div className="flex items-center justify-between border-b border-slate-200 pb-3 dark:border-slate-800">
              <h3 className="text-base font-bold text-orange-500">Emergency Break-Glass Override</h3>
              <button type="button" onClick={() => setOverrideOpen(false)}>
                <X className="h-5 w-5" />
              </button>
            </div>
            <p className="text-xs text-slate-500">
              Transitions <span className="font-mono font-semibold">{selectedRun.run_id}</span> from{" "}
              <code>BLOCKED_DOWNSTREAM</code> to <code>OVERRIDDEN_PASS</code> without disabling the underlying P1 rule.
            </p>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">
                Mandatory Audit Justification (min 20 characters)
              </label>
              <textarea
                required
                minLength={20}
                rows={3}
                value={overrideReason}
                onChange={(e) => setOverrideReason(e.target.value)}
                placeholder="Approved by PDH Release Lead: Fab-AZ loopback rows quarantined in downstream filter..."
                className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 text-xs dark:border-slate-700"
              />
            </div>
            {overrideErr && (
              <div className="rounded bg-rose-500/10 p-2 text-xs text-rose-400">{overrideErr}</div>
            )}
            <button
              type="submit"
              className="w-full rounded-lg bg-orange-600 py-2 text-xs font-semibold text-white hover:bg-orange-700"
            >
              Confirm Break-Glass Override
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
