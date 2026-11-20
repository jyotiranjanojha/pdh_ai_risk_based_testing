"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Archive,
  CheckCircle2,
  History,
  Play,
  Save,
  Share2,
  Sparkles,
  X,
} from "lucide-react";
import { useAppContext } from "@/components/AppContext";
import { PriorityBadge, StatusBadge } from "@/components/RoleBadge";
import { SqlEditor } from "@/components/SqlEditor";
import {
  Domain,
  DryRunResponse,
  EnvironmentType,
  rbtApi,
  Rule,
  RuleHistory,
  RuleStatus,
} from "@/lib/api";

export default function RulesStudioPage() {
  const { activeTeamId, authHeaders, role, cortexModel } = useAppContext();

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
  const [dryEnv, setDryEnv] = useState<EnvironmentType>("PROD");
  const [drySchemaOverride, setDrySchemaOverride] = useState("");
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRows, setHistoryRows] = useState<RuleHistory[]>([]);
  const [feedback, setFeedback] = useState<{ type: "ok" | "err"; msg: string } | null>(null);

  // Re-Discover Risks Modal (Scope 3)
  const [rediscoverOpen, setRediscoverOpen] = useState(false);
  const [rediscoverDomainId, setRediscoverDomainId] = useState("");
  const [prDiffDraft, setPrDiffDraft] = useState("");
  const [rediscovering, setRediscovering] = useState(false);

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
    if (dList.length > 0 && !rediscoverDomainId) {
      setRediscoverDomainId(dList[0].domain_id);
    }
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
        environment: dryEnv,
        target_schema_override: drySchemaOverride.trim() || undefined,
      });
      setDryRunResult(res);
    } catch (err) {
      setFeedback({ type: "err", msg: err instanceof Error ? err.message : "Dry-run failed" });
    }
  };

  const handleRiskScoreChange = async (likelihood: number, impact: number) => {
    if (!selectedRule) return;
    setFeedback(null);
    try {
      const updated = await rbtApi.updateRule(authHeaders, selectedRule.rule_id, {
        likelihood_score: likelihood,
        impact_score: impact,
      });
      setSelectedRule(updated);
      setFeedback({
        type: "ok",
        msg: `Updated Risk Score to ${updated.likelihood_score}×${updated.impact_score}=${updated.risk_score} (${updated.risk_priority}).`,
      });
      await loadRules();
    } catch (err) {
      setFeedback({ type: "err", msg: err instanceof Error ? err.message : "Failed to update risk score" });
    }
  };

  const handleRediscover = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!rediscoverDomainId) return;
    setRediscovering(true);
    setFeedback(null);
    try {
      const res = await rbtApi.aiDiscover(
        authHeaders,
        rediscoverDomainId,
        cortexModel,
        prDiffDraft.trim() || undefined
      );
      setRediscoverOpen(false);
      setPrDiffDraft("");
      setFeedback({
        type: "ok",
        msg: `Cortex AI discovered ${res.generated_count} rules (${res.draft_count} compiled, ${res.needs_review_count} need review).`,
      });
      await loadRules();
    } catch (err) {
      setFeedback({ type: "err", msg: err instanceof Error ? err.message : "Re-discovery failed" });
    } finally {
      setRediscovering(false);
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
            <option value="SCHEMA_CONTRACT_DRIFT">SCHEMA_CONTRACT_DRIFT</option>
            <option value="PIPELINE_RECONCILIATION">PIPELINE_RECONCILIATION</option>
            <option value="REGRESSION_DIFF">REGRESSION_DIFF</option>
          </select>
          <button
            type="button"
            onClick={() => setRediscoverOpen(true)}
            className="inline-flex items-center gap-1 rounded-lg bg-purple-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-purple-700"
          >
            <Sparkles className="h-3.5 w-3.5" /> Re-Discover Risks
          </button>
        </div>
      </div>

      {/* Quantitative Risk Heatmap Strip (Scope 1) */}
      <div className="grid grid-cols-3 gap-3 rounded-xl border border-slate-200 bg-white p-3 text-xs shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="rounded-lg bg-rose-500/10 px-3 py-2">
          <div className="font-semibold text-rose-500">Critical Risk (Score 16–25 · P1)</div>
          <div className="mt-0.5 text-lg font-bold">{rules.filter((r) => (r.risk_score || 9) >= 16).length} rules</div>
        </div>
        <div className="rounded-lg bg-amber-500/10 px-3 py-2">
          <div className="font-semibold text-amber-500">Elevated Risk (Score 8–15 · P2)</div>
          <div className="mt-0.5 text-lg font-bold">{rules.filter((r) => (r.risk_score || 9) >= 8 && (r.risk_score || 9) < 16).length} rules</div>
        </div>
        <div className="rounded-lg bg-slate-500/10 px-3 py-2">
          <div className="font-semibold text-slate-500">Low Risk (Score 1–7 · P3)</div>
          <div className="mt-0.5 text-lg font-bold">{rules.filter((r) => (r.risk_score || 9) < 8).length} rules</div>
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
                <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono font-semibold text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                  L{r.likelihood_score ?? 3}×I{r.impact_score ?? 3}={r.risk_score ?? 9}
                </span>
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

              {/* Likelihood x Impact Quantitative Risk Controls (Scope 1) */}
              <div className="flex flex-wrap items-center gap-4 rounded-lg border border-slate-200 bg-slate-50 p-2.5 text-xs dark:border-slate-800 dark:bg-slate-950">
                <span className="font-semibold">Quantitative Risk:</span>
                <label className="flex items-center gap-1.5">
                  <span className="text-slate-500">Likelihood</span>
                  <select
                    value={selectedRule.likelihood_score ?? 3}
                    onChange={(e) => handleRiskScoreChange(Number(e.target.value), selectedRule.impact_score ?? 3)}
                    className="rounded border border-slate-300 bg-white px-2 py-0.5 font-mono dark:border-slate-700 dark:bg-slate-800"
                  >
                    {[1, 2, 3, 4, 5].map((v) => (
                      <option key={v} value={v}>{v}</option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-1.5">
                  <span className="text-slate-500">Impact</span>
                  <select
                    value={selectedRule.impact_score ?? 3}
                    onChange={(e) => handleRiskScoreChange(selectedRule.likelihood_score ?? 3, Number(e.target.value))}
                    className="rounded border border-slate-300 bg-white px-2 py-0.5 font-mono dark:border-slate-700 dark:bg-slate-800"
                  >
                    {[1, 2, 3, 4, 5].map((v) => (
                      <option key={v} value={v}>{v}</option>
                    ))}
                  </select>
                </label>
                <span className="rounded-full bg-blue-500/15 px-2.5 py-0.5 font-mono font-bold text-blue-500">
                  Score: {selectedRule.risk_score ?? 9}/25 ({selectedRule.risk_priority})
                </span>
              </div>

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
                  <select
                    value={dryEnv}
                    onChange={(e) => setDryEnv(e.target.value as EnvironmentType)}
                    title="Target Environment"
                    className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 font-mono text-xs dark:border-slate-700 dark:bg-slate-800"
                  >
                    <option value="PROD">PROD</option>
                    <option value="UAT">UAT</option>
                    <option value="DEV">DEV</option>
                  </select>
                  <input
                    value={drySchemaOverride}
                    onChange={(e) => setDrySchemaOverride(e.target.value)}
                    placeholder="schema override (optional)"
                    className="w-40 rounded-lg border border-slate-300 bg-transparent px-2.5 py-1.5 font-mono text-xs dark:border-slate-700"
                  />
                  <input
                    value={cycleInput}
                    onChange={(e) => setCycleInput(e.target.value)}
                    placeholder="planning_cycle_id"
                    className="w-32 rounded-lg border border-slate-300 bg-transparent px-2.5 py-1.5 font-mono text-xs dark:border-slate-700"
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

      {/* Re-Discover Risks Modal (Scope 3) */}
      {rediscoverOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <form
            onSubmit={handleRediscover}
            className="w-full max-w-lg space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-800 dark:bg-slate-900"
          >
            <div className="flex items-center justify-between border-b border-slate-200 pb-3 dark:border-slate-800">
              <h3 className="text-base font-bold">Re-Discover Risks (PR Diff &amp; Lineage Aware)</h3>
              <button type="button" onClick={() => setRediscoverOpen(false)}>
                <X className="h-5 w-5" />
              </button>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Dataset</label>
              <select
                value={rediscoverDomainId}
                onChange={(e) => setRediscoverDomainId(e.target.value)}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-800"
              >
                {domains.map((d) => (
                  <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">
                Pipeline Transformation SQL / Git PR Diff (Optional)
              </label>
              <textarea
                rows={5}
                value={prDiffDraft}
                onChange={(e) => setPrDiffDraft(e.target.value)}
                placeholder="+ WHERE BOM_QTY > 0&#10;+ CASE WHEN SITE_ID = 'FAB-AZ' THEN ... END"
                className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 font-mono text-xs dark:border-slate-700"
              />
            </div>
            <button
              type="submit"
              disabled={rediscovering}
              className="w-full rounded-lg bg-purple-600 py-2 text-xs font-semibold text-white hover:bg-purple-700 disabled:opacity-50"
            >
              {rediscovering ? "Running Cortex AI Discovery..." : "Run AI Risk Discovery"}
            </button>
          </form>
        </div>
      )}

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
