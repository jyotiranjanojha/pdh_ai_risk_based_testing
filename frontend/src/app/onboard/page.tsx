"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, DatabaseZap, Loader2, Sparkles } from "lucide-react";
import { useAppContext } from "@/components/AppContext";
import { PriorityBadge, StatusBadge } from "@/components/RoleBadge";
import { AiDiscoverResponse, rbtApi } from "@/lib/api";

export default function OnboardPage() {
  const { activeTeamId, activeTeam, authHeaders, cortexModel } = useAppContext();

  const [domainName, setDomainName] = useState("");
  const [schemas, setSchemas] = useState<string[]>([]);
  const [schemasLoading, setSchemasLoading] = useState(false);
  const [targetSchema, setTargetSchema] = useState("");
  const [targetTable, setTargetTable] = useState("");
  const [relatedTables, setRelatedTables] = useState("");
  const [temporalCycleCol, setTemporalCycleCol] = useState("");
  const [snapshotTsCol, setSnapshotTsCol] = useState("");
  const [maxDepth, setMaxDepth] = useState(10);
  const [downstreamConsumer, setDownstreamConsumer] = useState("PLANNING_SOLVER");
  const [criticality, setCriticality] = useState<"CRITICAL" | "HIGH" | "MEDIUM">("CRITICAL");
  const [businessContext, setBusinessContext] = useState(
    "Supply planning dataset quality and integrity checks."
  );

  const [step, setStep] = useState<number>(0); // 0=idle, 1=registering, 2=grounding, 3=cortex, 4=done
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [result, setResult] = useState<AiDiscoverResponse | null>(null);

  useEffect(() => {
    if (!activeTeamId) return;
    let cancelled = false;
    setSchemasLoading(true);
    rbtApi
      .listSchemas(authHeaders, activeTeamId)
      .then((list) => {
        if (cancelled) return;
        setSchemas(list);
        setTargetSchema((prev) => (prev && list.includes(prev) ? prev : list[0] || ""));
      })
      .catch(() => {
        if (!cancelled) {
          setSchemas([]);
        }
      })
      .finally(() => {
        if (!cancelled) setSchemasLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeTeamId, activeTeam, authHeaders]);

  const handleOnboardAndDiscover = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setResult(null);
    try {
      setStep(1);
      const domain = await rbtApi.createDomain(authHeaders, activeTeamId, {
        domain_name: domainName,
        target_schema: targetSchema,
        target_table: targetTable,
        related_tables: relatedTables
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        temporal_cycle_column: temporalCycleCol || undefined,
        snapshot_ts_column: snapshotTsCol || undefined,
        max_hierarchy_depth: Number(maxDepth),
        downstream_consumer: downstreamConsumer || undefined,
        business_context: businessContext || undefined,
        criticality,
      });

      setStep(2);
      const discovery = await rbtApi.aiDiscover(authHeaders, domain.domain_id, cortexModel);
      setStep(4);
      setResult(discovery);
    } catch (err) {
      setStep(0);
      setErrorMsg(err instanceof Error ? err.message : "Onboarding failed");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">AI Dataset Onboarding Wizard</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Register any Snowflake table/view for{" "}
          <span className="font-semibold text-blue-500">{activeTeam?.team_name}</span> and ground Cortex AI using
          native catalog metadata.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-5">
        <form
          onSubmit={handleOnboardAndDiscover}
          className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900 lg:col-span-3"
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Dataset / Domain Name *</label>
              <input
                required
                value={domainName}
                onChange={(e) => setDomainName(e.target.value)}
                placeholder="e.g., Intersite BOM W44"
                className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 text-sm dark:border-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Criticality</label>
              <select
                value={criticality}
                onChange={(e) => setCriticality(e.target.value as "CRITICAL" | "HIGH" | "MEDIUM")}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800"
              >
                <option value="CRITICAL">CRITICAL</option>
                <option value="HIGH">HIGH</option>
                <option value="MEDIUM">MEDIUM</option>
              </select>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">
                Target Schema ({activeTeam?.snowflake_database || "Snowflake DB"}) *
              </label>
              <select
                required
                value={targetSchema}
                onChange={(e) => setTargetSchema(e.target.value)}
                disabled={schemasLoading}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-xs dark:border-slate-700 dark:bg-slate-800"
              >
                {schemasLoading && <option value="">Loading schemas...</option>}
                {!schemasLoading && schemas.length === 0 && (
                  <option value="">Connect Snowflake SSO to load schemas</option>
                )}
                {schemas.map((sch) => (
                  <option key={sch} value={sch}>
                    {sch}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Target Table / View *</label>
              <input
                required
                value={targetTable}
                onChange={(e) => setTargetTable(e.target.value)}
                placeholder="e.g., RBT_TEST_RULES"
                className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 font-mono text-xs dark:border-slate-700"
              />
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Temporal Cycle Column</label>
              <input
                value={temporalCycleCol}
                onChange={(e) => setTemporalCycleCol(e.target.value)}
                placeholder="PLANNING_CYCLE_ID"
                className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 font-mono text-xs dark:border-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Snapshot TS Column</label>
              <input
                value={snapshotTsCol}
                onChange={(e) => setSnapshotTsCol(e.target.value)}
                placeholder="SNAPSHOT_TS"
                className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 font-mono text-xs dark:border-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Max Hierarchy Depth</label>
              <input
                type="number"
                min={1}
                max={100}
                value={maxDepth}
                onChange={(e) => setMaxDepth(Number(e.target.value))}
                className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 font-mono text-xs dark:border-slate-700"
              />
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Downstream Consumer</label>
              <input
                value={downstreamConsumer}
                onChange={(e) => setDownstreamConsumer(e.target.value)}
                className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 text-sm dark:border-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Related Parent Tables (CSV)</label>
              <input
                value={relatedTables}
                onChange={(e) => setRelatedTables(e.target.value)}
                className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 font-mono text-xs dark:border-slate-700"
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">Plain-English Business Context</label>
            <textarea
              rows={3}
              value={businessContext}
              onChange={(e) => setBusinessContext(e.target.value)}
              className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 text-sm dark:border-slate-700"
            />
          </div>

          {errorMsg && (
            <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-400">
              {errorMsg}
            </div>
          )}

          <button
            type="submit"
            disabled={step > 0 && step < 4}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {step > 0 && step < 4 ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> Running Native Grounding &amp; Cortex AI...
              </>
            ) : (
              <>
                <Sparkles className="h-4 w-4" /> Discover Risks with Cortex AI
              </>
            )}
          </button>
        </form>

        {/* 3-Step Grounding Progress & Output */}
        <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900 lg:col-span-2">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <DatabaseZap className="h-4 w-4 text-blue-500" />
            3-Step Snowflake Catalog Grounding
          </div>

          <div className="space-y-3 text-xs">
            {[
              { id: 1, label: "Step 1: INFORMATION_SCHEMA.COLUMNS & Comments" },
              { id: 2, label: "Step 2: Physical PK / FK / Unique Constraints" },
              { id: 3, label: "Step 3: Non-PII Statistical Bounds & Cortex Structured Output (MAX_RETRIES=3)" },
            ].map((s) => {
              const done = step === 4 || step > s.id;
              const active = step === s.id;
              return (
                <div
                  key={s.id}
                  className={`flex items-center gap-2.5 rounded-lg border p-3 ${
                    done
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                      : active
                      ? "border-blue-500/40 bg-blue-500/10 text-blue-400"
                      : "border-slate-200 text-slate-500 dark:border-slate-800"
                  }`}
                >
                  {done ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0" />
                  ) : active ? (
                    <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                  ) : (
                    <div className="h-4 w-4 shrink-0 rounded-full border border-current" />
                  )}
                  <span>{s.label}</span>
                </div>
              );
            })}
          </div>

          {result && (
            <div className="space-y-3 border-t border-slate-200 pt-4 dark:border-slate-800">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-emerald-400">
                  Generated {result.generated_count} Rules ({result.draft_count} DRAFT, {result.needs_review_count} REVIEW)
                </span>
                <Link href="/rules" className="font-semibold text-blue-500 hover:underline">
                  Open SQL Studio →
                </Link>
              </div>
              <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
                {result.rules.map((r) => (
                  <div key={r.rule_id} className="rounded-lg border border-slate-200 p-2.5 text-xs dark:border-slate-800">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold">{r.risk_name}</span>
                      <PriorityBadge priority={r.risk_priority} />
                    </div>
                    <div className="mt-1 flex items-center justify-between text-[11px] text-slate-500">
                      <span className="font-mono">{r.risk_category}</span>
                      <StatusBadge status={r.status} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
