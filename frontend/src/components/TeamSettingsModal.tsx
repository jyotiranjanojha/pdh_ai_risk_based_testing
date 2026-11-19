"use client";

import React, { useEffect, useState } from "react";
import { Check, Copy, KeyRound, Plus, Settings, X } from "lucide-react";
import { rbtApi } from "@/lib/api";
import { useAppContext } from "./AppContext";

export function TeamSettingsModal({
  open,
  onClose,
  defaultTab = "settings",
}: {
  open: boolean;
  onClose: () => void;
  defaultTab?: "settings" | "new";
}) {
  const { activeTeam, authHeaders, role, refreshTeams, setActiveTeamId } = useAppContext();
  const [tab, setTab] = useState<"settings" | "new">(defaultTab);

  useEffect(() => {
    if (open) {
      setTab(defaultTab);
      setErrorMsg(null);
      setStatusMsg(null);
    }
  }, [open, defaultTab]);
  const [rotatedKey, setRotatedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // New team form
  const [teamName, setTeamName] = useState("");
  const [sfDb, setSfDb] = useState("NEW_HUB_DB");
  const [sfWh, setSfWh] = useState("NEW_RBT_WH");
  const [downstream, setDownstream] = useState("ERP_CORE");
  const [webhookUrl, setWebhookUrl] = useState("");

  if (!open) return null;

  const isAdmin = role === "ADMIN";

  const handleRotateKey = async () => {
    if (!activeTeam) return;
    setErrorMsg(null);
    setStatusMsg(null);
    try {
      const res = await rbtApi.rotateApiKey(authHeaders, activeTeam.team_id);
      setRotatedKey(res.api_key);
      setStatusMsg("New orchestrator X-API-Key generated. Copy it now—it will not be shown again.");
      await refreshTeams();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to rotate API key");
    }
  };

  const handleCreateTeam = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setStatusMsg(null);
    try {
      const created = await rbtApi.createTeam(authHeaders, {
        team_name: teamName,
        snowflake_database: sfDb,
        snowflake_warehouse: sfWh,
        default_downstream_target: downstream,
        alert_webhook_url: webhookUrl || undefined,
      });
      await refreshTeams();
      setActiveTeamId(created.team_id);
      setTeamName("");
      setTab("settings");
      setStatusMsg(`Created workspace "${created.team_name}".`);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to create team");
    }
  };

  const copyKey = () => {
    if (!rotatedKey) return;
    navigator.clipboard.writeText(rotatedKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-xl rounded-xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-slate-200 pb-3 dark:border-slate-800">
          <div className="flex items-center gap-2">
            <Settings className="h-5 w-5 text-blue-500" />
            <h2 className="text-lg font-semibold">Team Workspace &amp; Orchestrator API Keys</h2>
          </div>
          <button onClick={onClose} className="rounded p-1 hover:bg-slate-100 dark:hover:bg-slate-800">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="mt-4 flex gap-2 border-b border-slate-200 pb-2 dark:border-slate-800">
          <button
            onClick={() => setTab("settings")}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              tab === "settings" ? "bg-blue-600 text-white" : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            }`}
          >
            Active Team Settings
          </button>
          <button
            onClick={() => setTab("new")}
            className={`inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-sm font-medium ${
              tab === "new" ? "bg-blue-600 text-white" : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            }`}
          >
            <Plus className="h-4 w-4" /> New Team Workspace
          </button>
        </div>

        {errorMsg && (
          <div className="mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-400">
            {errorMsg}
          </div>
        )}
        {statusMsg && (
          <div className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-400">
            {statusMsg}
          </div>
        )}

        {tab === "settings" && activeTeam && (
          <div className="mt-4 space-y-4 text-sm">
            <div className="grid grid-cols-2 gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
              <div>
                <div className="text-xs text-slate-500">Team Name</div>
                <div className="font-semibold">{activeTeam.team_name}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Downstream Target</div>
                <div className="font-mono text-xs">{activeTeam.default_downstream_target || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Snowflake Database</div>
                <div className="font-mono text-xs">{activeTeam.snowflake_database}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Isolated Compute Warehouse</div>
                <div className="font-mono text-xs text-blue-500">{activeTeam.snowflake_warehouse}</div>
              </div>
            </div>

            <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-medium">Orchestrator X-API-Key</div>
                  <div className="text-xs text-slate-500">
                    Used by Airflow / Control-M / CI-CD for <code>POST /api/v1/execution/run-gate</code>
                  </div>
                </div>
                <button
                  disabled={!isAdmin}
                  onClick={handleRotateKey}
                  title={!isAdmin ? "Requires ADMIN role" : "Generate or rotate API Key"}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <KeyRound className="h-3.5 w-3.5" />
                  {activeTeam.has_api_key ? "Rotate API Key" : "Generate API Key"}
                </button>
              </div>
              {!isAdmin && (
                <p className="mt-2 text-xs text-amber-500">
                  Switch Demo Role to ADMIN to rotate orchestrator keys or create teams.
                </p>
              )}
              {rotatedKey && (
                <div className="mt-3 flex items-center justify-between rounded border border-emerald-500/40 bg-slate-950 p-2.5 font-mono text-xs text-emerald-400">
                  <span className="truncate">{rotatedKey}</span>
                  <button onClick={copyKey} className="ml-2 inline-flex items-center gap-1 text-slate-300 hover:text-white">
                    {copied ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
                    {copied ? "Copied" : "Copy"}
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {tab === "new" && (
          <form onSubmit={handleCreateTeam} className="mt-4 space-y-3 text-sm">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Team Name</label>
              <input
                required
                value={teamName}
                onChange={(e) => setTeamName(e.target.value)}
                placeholder="e.g., Supply Chain Analytics"
                className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Snowflake Database</label>
                <input
                  required
                  value={sfDb}
                  onChange={(e) => setSfDb(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 font-mono text-xs dark:border-slate-700"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Snowflake Warehouse</label>
                <input
                  required
                  value={sfWh}
                  onChange={(e) => setSfWh(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 font-mono text-xs dark:border-slate-700"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Default Downstream Target</label>
                <input
                  value={downstream}
                  onChange={(e) => setDownstream(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Alert Webhook URL</label>
                <input
                  value={webhookUrl}
                  onChange={(e) => setWebhookUrl(e.target.value)}
                  placeholder="https://hooks.slack.com/..."
                  className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-2 dark:border-slate-700"
                />
              </div>
            </div>
            <button
              type="submit"
              disabled={!isAdmin}
              className="w-full rounded-lg bg-blue-600 py-2 font-semibold text-white hover:bg-blue-700 disabled:opacity-40"
            >
              {isAdmin ? "Create Team Workspace" : "Requires ADMIN Role"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
