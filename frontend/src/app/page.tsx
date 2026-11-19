"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Database,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { useAppContext } from "@/components/AppContext";
import { GateDecisionBadge, PriorityBadge } from "@/components/RoleBadge";
import { Domain, ExecutionRun, rbtApi, Rule } from "@/lib/api";

export default function ExecutiveDashboardPage() {
  const { activeTeamId, activeTeam, authHeaders } = useAppContext();
  const [domains, setDomains] = useState<Domain[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [runs, setRuns] = useState<ExecutionRun[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const [d, r, x] = await Promise.all([
          rbtApi.listDomains(authHeaders, activeTeamId),
          rbtApi.listRules(authHeaders, activeTeamId),
          rbtApi.listRuns(authHeaders, activeTeamId),
        ]);
        if (!cancelled) {
          setDomains(d);
          setRules(r);
          setRuns(x);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    if (activeTeamId) load();
    return () => {
      cancelled = true;
    };
  }, [activeTeamId, authHeaders.role, authHeaders.email]);

  const approvedRules = rules.filter((r) => r.status === "APPROVED");
  const blockedRuns = runs.filter((r) => r.gate_decision === "BLOCKED_DOWNSTREAM");
  const passedRuns = runs.filter((r) =>
    ["PASS", "PASS_WITH_WARNINGS", "OVERRIDDEN_PASS"].includes(r.gate_decision)
  );
  const p1FailureTotal = runs.reduce((acc, r) => acc + r.metrics.p1_failures, 0);
  const p2FailureTotal = runs.reduce((acc, r) => acc + r.metrics.p2_failures, 0);

  const categoryCounts = rules.reduce<Record<string, number>>((acc, r) => {
    const key = r.risk_category || "UNCATEGORIZED";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {activeTeam?.team_name || "Team"} — Executive Gate Health
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Zero-egress Snowflake warehouse{" "}
            <span className="font-mono text-blue-500">{activeTeam?.snowflake_warehouse}</span> · Downstream:{" "}
            <span className="font-mono">{activeTeam?.default_downstream_target || "N/A"}</span>
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            href="/onboard"
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3.5 py-2 text-xs font-semibold hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
          >
            Onboard Dataset <ArrowRight className="h-3.5 w-3.5" />
          </Link>
          <Link
            href="/runs"
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-blue-700"
          >
            Run Pre-Flight Gate <Activity className="h-3.5 w-3.5" />
          </Link>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Onboarded Datasets</span>
            <Database className="h-4 w-4 text-blue-500" />
          </div>
          <div className="mt-2 text-2xl font-bold">{loading ? "—" : domains.length}</div>
          <div className="mt-1 text-xs text-slate-500">{approvedRules.length} approved gate rules</div>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Gates Passed / Overridden</span>
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          </div>
          <div className="mt-2 text-2xl font-bold text-emerald-500">{loading ? "—" : passedRuns.length}</div>
          <div className="mt-1 text-xs text-slate-500">Out of {runs.length} total gate runs</div>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Blocked Downstream</span>
            <ShieldAlert className="h-4 w-4 text-rose-500" />
          </div>
          <div className="mt-2 text-2xl font-bold text-rose-500">{loading ? "—" : blockedRuns.length}</div>
          <div className="mt-1 text-xs text-slate-500">Fail-closed P1 protection active</div>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>P1 / P2 Failure Trend</span>
            <AlertTriangle className="h-4 w-4 text-amber-500" />
          </div>
          <div className="mt-2 text-2xl font-bold">
            <span className="text-rose-500">{p1FailureTotal}</span>{" "}
            <span className="text-sm font-normal text-slate-500">P1</span> /{" "}
            <span className="text-amber-500">{p2FailureTotal}</span>{" "}
            <span className="text-sm font-normal text-slate-500">P2</span>
          </div>
          <div className="mt-1 text-xs text-slate-500">Across recent planning cycles</div>
        </div>
      </div>

      {/* Main Grid */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Recent Gate Executions */}
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900 lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Recent Pre-Flight Gate Runs</h2>
            <Link href="/runs" className="text-xs font-medium text-blue-500 hover:underline">
              View all &amp; Triage →
            </Link>
          </div>
          {runs.length === 0 ? (
            <p className="text-sm text-slate-500">No execution runs recorded for this team yet.</p>
          ) : (
            <div className="space-y-3">
              {runs.slice(0, 5).map((run) => (
                <div
                  key={run.run_id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 p-3 dark:border-slate-800"
                >
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-semibold">{run.run_id}</span>
                      <span className="text-xs font-medium">{run.domain_name || run.domain_id}</span>
                      {run.planning_cycle_id && (
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] dark:bg-slate-800">
                          {run.planning_cycle_id}
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                      {run.metrics.total_rules_run} rules · {run.metrics.p1_failures} P1 · {run.metrics.p2_failures} P2
                    </div>
                  </div>
                  <GateDecisionBadge decision={run.gate_decision} />
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Active Rules by Risk Category */}
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Rules by Risk Category</h2>
            <Link href="/rules" className="text-xs font-medium text-blue-500 hover:underline">
              SQL Studio →
            </Link>
          </div>
          <div className="space-y-2.5">
            {Object.entries(categoryCounts).map(([cat, cnt]) => (
              <div key={cat} className="flex items-center justify-between text-xs">
                <span className="font-mono text-slate-600 dark:text-slate-300">{cat}</span>
                <span className="rounded-full bg-blue-500/15 px-2.5 py-0.5 font-semibold text-blue-500">
                  {cnt}
                </span>
              </div>
            ))}
            {Object.keys(categoryCounts).length === 0 && (
              <p className="text-xs text-slate-500">No rules yet. Onboard a dataset to generate rules.</p>
            )}
          </div>

          <div className="mt-6 border-t border-slate-200 pt-4 dark:border-slate-800">
            <div className="mb-2 text-xs font-semibold text-slate-500">Onboarded Datasets</div>
            <div className="space-y-2">
              {domains.map((d) => (
                <div key={d.domain_id} className="flex items-center justify-between text-xs">
                  <div>
                    <div className="font-medium">{d.domain_name}</div>
                    <div className="font-mono text-[11px] text-slate-500">
                      {d.target_schema}.{d.target_table}
                    </div>
                  </div>
                  <PriorityBadge
                    priority={d.criticality === "CRITICAL" ? "P1_BLOCKER" : "P2_WARNING"}
                  />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
