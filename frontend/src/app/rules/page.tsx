"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Archive,
  CheckCircle2,
  History,
  Play,
  Save,
  Share2,
  X,
} from "lucide-react";
import { useAppContext } from "@/components/AppContext";
import { PriorityBadge, StatusBadge } from "@/components/RoleBadge";
import { SqlEditor } from "@/components/SqlEditor";
import {
  Domain,
  DryRunResponse,
  rbtApi,
  Rule,
  RuleHistory,
  RuleStatus,
} from "@/lib/api";

export default function RulesStudioPage() {
  const { activeTeamId, authHeaders, role } = useAppContext();

  const [domains, setDomains] = useState<Domain[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [selectedRule, setSelectedRule] = useState<Rule | null>(null);
  const [sqlDraft, setSqlDraft] = useState("");

  // Filters
  const [domainFilter, setDomainFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");

  // Dry-run & History
  const [cycleInput, setCycleInput] = useState("2025_W42_POR");
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRows, setHistoryRows] = useState<RuleHistory[]>([]);
  const [feedback, setFeedback] = useState<{ type: "ok" | "err"; msg: string } | null>(null);

  const canApprove = role === "APPROVER" || role === "ADMIN";

  const loadRules = useCallback(async () => {
    if (!activeTeamId) return;
    const [dList, rList] = await Promise.all([
      rbtApi.listDomains(authHeaders, activeTeamId),
      rbtApi.listRules(authHeaders, activeTeamId, {
        domain_id: domainFilter || undefined,
        status: statusFilter || undefined,
        priority: priorityFilter || undefined,
        category: categoryFilter || undefined,
      }),
    ]);
    setDomains(dList);
    setRules(rList);
    if (rList.length > 0 && (!selectedRule || !rList.some((r) => r.rule_id === selectedRule.rule_id))) {
      setSelectedRule(rList[0]);
      setSqlDraft(rList[0].validation_sql);
    }
  }, [activeTeamId, authHeaders.email, authHeaders.role, domainFilter, statusFilter, priorityFilter, categoryFilter]);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  const selectRule = (rule: Rule) => {
    setSelectedRule(rule);
    setSqlDraft(rule.validation_sql);
    setDryRunResult(null);
    setFeedback(null);
  };

  const handleSaveSql = async () => {
    if (!selectedRule) return;
    setFeedback(null);
    try {
      const updated = await rbtApi.updateRule(authHeaders, selectedRule.rule_id, {
        validation_sql: sqlDraft,
      });
      setSelectedRule(updated);
      setFeedback({ type: "ok", msg: "SQL saved & validated via sqlglot AST. Audit history logged." });
      await loadRules();
    } catch (err) {
      setFeedback({ type: "err", msg: err instanceof Error ? err.message : "Save failed" });
    }
  };

  const handleStatusChange = async (newStatus: RuleStatus) => {
    if (!selectedRule) return;
    setFeedback(null);
    try {
      const updated = await rbtApi.updateRule(authHeaders, selectedRule.rule_id, {
        status: newStatus,
      });
      setSelectedRule(updated);
      setFeedback({ type: "ok", msg: `Rule status transitioned to ${newStatus}.` });
      await loadRules();
    } catch (err) {
      setFeedback({ type: "err", msg: err instanceof Error ? err.message : "Status update failed" });
    }
  };

  const handleToggleShare = async () => {
    if (!selectedRule) return;
    setFeedback(null);
    try {
      const updated = await rbtApi.updateRule(authHeaders, selectedRule.rule_id, {
        is_shared_template: !selectedRule.is_shared_template,
      });
      setSelectedRule(updated);
      setFeedback({
        type: "ok",
        msg: updated.is_shared_template
          ? "Published to Cross-Team Rule Marketplace."
          : "Removed from Marketplace.",
      });
      await loadRules();
    } catch (err) {
      setFeedback({ type: "err", msg: err instanceof Error ? err.message : "Failed to toggle sharing" });
    }
  };

  const handleDryRun = async () => {
    if (!selectedRule) return;
    setFeedback(null);
    try {
      const res = await rbtApi.dryRunRule(authHeaders, selectedRule.rule_id, {
        validation_sql: sqlDraft,
        planning_cycle_id: cycleInput || undefined,
      });
      setDryRunResult(res);
    } catch (err) {
      setFeedback({ type: "err", msg: err instanceof Error ? err.message : "Dry-run failed" });
    }
  };

  const openHistory = async () => {
    if (!selectedRule) return;
    const rows = await rbtApi.getRuleHistory(authHeaders, selectedRule.rule_id);
    setHistoryRows(rows);
    setHistoryOpen(true);
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Risk Matrix &amp; SQL Studio</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Edit SQL with <code className="text-xs">sqlglot</code> AST protection, dry-run with cycle filters, and
            approve rules for automated pre-flight gates.
          </p>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-2 text-xs">
          <select
            value={domainFilter}
            onChange={(e) => setDomainFilter(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-800"
          >
            <option value="">All Datasets</option>
            {domains.map((d) => (
              <option key={d.domain_id} value={d.domain_id}>
                {d.domain_name}
              </option>
            ))}
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-800"
          >
            <option value="">All Statuses</option>
            <option value="DRAFT">DRAFT</option>
            <option value="DRAFT_NEEDS_REVIEW">DRAFT_NEEDS_REVIEW</option>
            <option value="APPROVED">APPROVED</option>
            <option value="ARCHIVED">ARCHIVED</option>
          </select>
          <select
            value={priorityFilter}
            onChange={(e) => setPriorityFilter(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-800"
          >
            <option value="">All Priorities</option>
            <option value="P1_BLOCKER">P1_BLOCKER</option>
            <option value="P2_WARNING">P2_WARNING</option>
            <option value="P3_INFO">P3_INFO</option>
          </select>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-800"
          >
            <option value="">All Categories</option>
            <option value="HIERARCHY_LOOP">HIERARCHY_LOOP</option>
            <option value="FROZEN_HORIZON_VIOLATION">FROZEN_HORIZON_VIOLATION</option>
            <option value="DUPLICATE_KEY">DUPLICATE_KEY</option>
            <option value="REFERENTIAL_INTEGRITY">REFERENTIAL_INTEGRITY</option>
            <option value="BOUNDARY_VALUE">BOUNDARY_VALUE</option>
            <option value="FRESHNESS_ANOMALY">FRESHNESS_ANOMALY</option>
          </select>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-12">
        {/* Rule List */}
        <div className="space-y-2.5 lg:col-span-5">
          {rules.map((r) => (
            <button
              key={r.rule_id}
              onClick={() => selectRule(r)}
              className={`w-full rounded-xl border p-3.5 text-left transition ${
                selectedRule?.rule_id === r.rule_id
                  ? "border-blue-500 bg-blue-500/5 shadow-sm"
                  : "border-slate-200 bg-white hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-700"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="text-sm font-semibold">{r.risk_name}</div>
                <PriorityBadge priority={r.risk_priority} />
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                <StatusBadge status={r.status} />
                <span className="font-mono">{r.domain_name || r.domain_id}</span>
                {r.is_shared_template && (
                  <span className="rounded bg-purple-500/15 px-1.5 py-0.5 text-purple-400">SHARED</span>
                )}
              </div>
            </button>
          ))}
          {rules.length === 0 && (
            <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500 dark:border-slate-700">
              No rules match the selected filters.
            </div>
          )}
        </div>

        {/* SQL Studio */}
        <div className="lg:col-span-7">
          {selectedRule ? (
            <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-lg font-bold">{selectedRule.risk_name}</h2>
                    <StatusBadge status={selectedRule.status} />
                  </div>
                  <p className="mt-1 text-xs text-slate-500">{selectedRule.business_impact_desc}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={openHistory}
                    className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
                  >
                    <History className="h-3.5 w-3.5" /> History
                  </button>
                  {selectedRule.status === "APPROVED" && (
                    <button
                      onClick={handleToggleShare}
                      className="inline-flex items-center gap-1 rounded-lg border border-purple-500/40 bg-purple-500/10 px-2.5 py-1.5 text-xs font-medium text-purple-400 hover:bg-purple-500/20"
                    >
                      <Share2 className="h-3.5 w-3.5" />
                      {selectedRule.is_shared_template ? "Unshare" : "Share Template"}
                    </button>
                  )}
                </div>
              </div>

              {selectedRule.last_compile_error && (
                <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-2.5 text-xs text-rose-400">
                  Last Compile Error: {selectedRule.last_compile_error}
                </div>
              )}

              <div>
                <div className="mb-1.5 flex items-center justify-between text-xs text-slate-500">
                  <span>Validation SQL (0 rows returned = PASS)</span>
                  <span className="font-mono">Alias: __TARGET_DATASET__</span>
                </div>
                <SqlEditor value={sqlDraft} onChange={setSqlDraft} height="200px" />
              </div>

              {feedback && (
                <div
                  className={`rounded-lg border p-2.5 text-xs ${
                    feedback.type === "ok"
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                      : "border-rose-500/30 bg-rose-500/10 text-rose-400"
                  }`}
                >
                  {feedback.msg}
                </div>
              )}

              {/* Actions Bar */}
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-3 dark:border-slate-800">
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    value={cycleInput}
                    onChange={(e) => setCycleInput(e.target.value)}
                    placeholder="planning_cycle_id"
                    className="w-36 rounded-lg border border-slate-300 bg-transparent px-2.5 py-1.5 font-mono text-xs dark:border-slate-700"
                  />
                  <button
                    onClick={handleDryRun}
                    className="inline-flex items-center gap-1 rounded-lg bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700 dark:bg-slate-700 dark:hover:bg-slate-600"
                  >
                    <Play className="h-3.5 w-3.5" /> Dry Run
                  </button>
                  <button
                    onClick={handleSaveSql}
                    className="inline-flex items-center gap-1 rounded-lg border border-blue-500/40 bg-blue-500/10 px-3 py-1.5 text-xs font-semibold text-blue-500 hover:bg-blue-500/20"
                  >
                    <Save className="h-3.5 w-3.5" /> Save SQL
                  </button>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    disabled={!canApprove}
                    onClick={() => handleStatusChange("ARCHIVED")}
                    title={!canApprove ? "Requires APPROVER or ADMIN role" : "Archive Rule"}
                    className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-800"
                  >
                    <Archive className="h-3.5 w-3.5" /> Archive
                  </button>
                  <button
                    disabled={!canApprove}
                    onClick={() => handleStatusChange("APPROVED")}
                    title={!canApprove ? "Requires APPROVER or ADMIN role" : "Approve for Gate Execution"}
                    className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" /> Approve &amp; Publish
                  </button>
                </div>
              </div>

              {!canApprove && (
                <p className="text-[11px] text-amber-500">
                  Separation of Duties: Current role is {role}. Switch Demo Role to APPROVER or ADMIN to approve rules.
                </p>
              )}

              {/* Dry-Run Result */}
              {dryRunResult && (
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3.5 text-xs dark:border-slate-800 dark:bg-slate-950">
                  <div className="flex items-center justify-between font-semibold">
                    <span>
                      Dry-Run Result:{" "}
                      {dryRunResult.valid_ast && dryRunResult.compiled ? (
                        dryRunResult.failed_row_count === 0 ? (
                          <span className="text-emerald-500">PASS (0 failing rows)</span>
                        ) : (
                          <span className="text-rose-500">{dryRunResult.failed_row_count} failing rows</span>
                        )
                      ) : (
                        <span className="text-rose-500">REJECTED / ERROR</span>
                      )}
                    </span>
                    <span className="text-slate-500">{dryRunResult.execution_ms} ms</span>
                  </div>
                  {dryRunResult.error_message && (
                    <pre className="mt-2 whitespace-pre-wrap text-rose-400">{dryRunResult.error_message}</pre>
                  )}
                  {dryRunResult.sample_failing_keys.length > 0 && (
                    <pre className="mt-2 max-h-40 overflow-auto rounded bg-slate-900 p-2 font-mono text-[11px] text-slate-200">
                      {JSON.stringify(dryRunResult.sample_failing_keys, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-slate-300 p-12 text-center text-sm text-slate-500 dark:border-slate-700">
              Select a rule from the matrix to inspect or edit SQL.
            </div>
          )}
        </div>
      </div>

      {/* History Drawer */}
      {historyOpen && selectedRule && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm">
          <div className="h-full w-full max-w-lg overflow-y-auto bg-white p-6 shadow-2xl dark:bg-slate-900">
            <div className="flex items-center justify-between border-b border-slate-200 pb-3 dark:border-slate-800">
              <h3 className="text-base font-bold">Audit History — {selectedRule.risk_name}</h3>
              <button onClick={() => setHistoryOpen(false)} className="rounded p-1 hover:bg-slate-100 dark:hover:bg-slate-800">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="mt-4 space-y-4">
              {historyRows.map((h) => (
                <div key={h.history_id} className="rounded-lg border border-slate-200 p-3 text-xs dark:border-slate-800">
                  <div className="flex items-center justify-between font-semibold">
                    <span>Version #{h.version_num}</span>
                    <span className="text-slate-500">{h.modified_by}</span>
                  </div>
                  <div className="mt-1 text-slate-500">
                    Status: <span className="font-mono">{h.status_old || "—"}</span> →{" "}
                    <span className="font-mono text-emerald-500">{h.status_new}</span>
                  </div>
                  {h.validation_sql_new && (
                    <pre className="mt-2 overflow-x-auto rounded bg-slate-950 p-2 font-mono text-[11px] text-slate-200">
                      {h.validation_sql_new}
                    </pre>
                  )}
                </div>
              ))}
              {historyRows.length === 0 && (
                <p className="text-xs text-slate-500">No audit history entries recorded yet.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
