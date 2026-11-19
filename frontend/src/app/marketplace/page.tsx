"use client";

import React, { useEffect, useState } from "react";
import { CopyCheck, Sparkles, Store, X } from "lucide-react";
import { useAppContext } from "@/components/AppContext";
import { PriorityBadge } from "@/components/RoleBadge";
import { Domain, MarketplaceTemplate, rbtApi } from "@/lib/api";

export default function MarketplacePage() {
  const { activeTeamId, activeTeam, authHeaders, cortexModel } = useAppContext();
  const [templates, setTemplates] = useState<MarketplaceTemplate[]>([]);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [category, setCategory] = useState("");
  const [cloneTarget, setCloneTarget] = useState<MarketplaceTemplate | null>(null);
  const [selectedDomainId, setSelectedDomainId] = useState("");
  const [cloning, setCloning] = useState(false);
  const [cloneMsg, setCloneMsg] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      const [mkt, doms] = await Promise.all([
        rbtApi.listMarketplace(authHeaders, category || undefined),
        rbtApi.listDomains(authHeaders, activeTeamId),
      ]);
      setTemplates(mkt);
      setDomains(doms);
      if (doms.length > 0) setSelectedDomainId(doms[0].domain_id);
    }
    if (activeTeamId) load();
  }, [activeTeamId, category, authHeaders.email, authHeaders.role]);

  const handleClone = async () => {
    if (!cloneTarget || !selectedDomainId) return;
    setCloning(true);
    setCloneMsg(null);
    try {
      const res = await rbtApi.cloneTemplate(authHeaders, {
        template_rule_id: cloneTarget.rule_id,
        target_team_id: activeTeamId,
        target_domain_id: selectedDomainId,
        cortex_model: cortexModel,
      });
      setCloneMsg(
        `Cloned as DRAFT (${res.cloned_rule.rule_id}). ${res.mapping_notes}`
      );
    } catch (err) {
      setCloneMsg(err instanceof Error ? err.message : "Clone failed");
    } finally {
      setCloning(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Cross-Team Rule Marketplace</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Browse battle-tested rules across enterprise hubs and clone them with Cortex AI column remapping.
          </p>
        </div>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium dark:border-slate-700 dark:bg-slate-800"
        >
          <option value="">All Risk Categories</option>
          <option value="HIERARCHY_LOOP">HIERARCHY_LOOP</option>
          <option value="DUPLICATE_KEY">DUPLICATE_KEY</option>
          <option value="REFERENTIAL_INTEGRITY">REFERENTIAL_INTEGRITY</option>
          <option value="BOUNDARY_VALUE">BOUNDARY_VALUE</option>
          <option value="FROZEN_HORIZON_VIOLATION">FROZEN_HORIZON_VIOLATION</option>
        </select>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {templates.map((tpl) => (
          <div
            key={tpl.rule_id}
            className="flex flex-col justify-between rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"
          >
            <div>
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Store className="h-4 w-4 text-purple-500" />
                  <h2 className="text-base font-bold">{tpl.risk_name}</h2>
                </div>
                <PriorityBadge priority={tpl.risk_priority} />
              </div>
              <div className="mt-1 text-xs text-slate-500">
                Published by <span className="font-semibold">{tpl.source_team_name}</span> · {tpl.source_domain_name}
              </div>
              <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">{tpl.business_impact_desc}</p>
              <pre className="mt-3 max-h-28 overflow-auto rounded-lg bg-slate-950 p-2.5 font-mono text-[11px] text-slate-200">
                {tpl.validation_sql}
              </pre>
            </div>

            <div className="mt-4 flex items-center justify-between border-t border-slate-200 pt-3 dark:border-slate-800">
              <span className="font-mono text-[11px] text-slate-500">{tpl.risk_category}</span>
              <button
                onClick={() => {
                  setCloneTarget(tpl);
                  setCloneMsg(null);
                }}
                className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700"
              >
                <Sparkles className="h-3.5 w-3.5" /> Clone &amp; AI Remap to {activeTeam?.team_name}
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Clone Modal */}
      {cloneTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-center justify-between border-b border-slate-200 pb-3 dark:border-slate-800">
              <h3 className="text-base font-bold">Clone &amp; AI Remap Columns</h3>
              <button onClick={() => setCloneTarget(null)} className="rounded p-1 hover:bg-slate-100 dark:hover:bg-slate-800">
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="mt-4 space-y-3 text-sm">
              <div className="text-xs text-slate-500">
                Template: <span className="font-semibold text-slate-200">{cloneTarget.risk_name}</span>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">
                  Target Dataset ({activeTeam?.team_name})
                </label>
                <select
                  value={selectedDomainId}
                  onChange={(e) => setSelectedDomainId(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-800"
                >
                  {domains.map((d) => (
                    <option key={d.domain_id} value={d.domain_id}>
                      {d.domain_name} ({d.target_schema}.{d.target_table})
                    </option>
                  ))}
                </select>
              </div>

              {cloneMsg && (
                <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-400">
                  <CopyCheck className="mb-1 inline h-4 w-4" /> {cloneMsg}
                </div>
              )}

              <button
                disabled={cloning || !selectedDomainId}
                onClick={handleClone}
                className="w-full rounded-lg bg-blue-600 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {cloning ? "Remapping & Compiling via Cortex AI..." : "Clone & Remap Columns"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
