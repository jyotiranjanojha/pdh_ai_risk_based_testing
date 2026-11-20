"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Check, Copy, KeyRound, Plus, Settings, Trash2, UserPlus, Users, X } from "lucide-react";
import { rbtApi, TeamMember, UserRole } from "@/lib/api";
import { useAppContext } from "./AppContext";
import { RoleBadge } from "./RoleBadge";

export function TeamSettingsModal({
  open,
  onClose,
  defaultTab = "settings",
}: {
  open: boolean;
  onClose: () => void;
  defaultTab?: "settings" | "members" | "new";
}) {
  const { activeTeam, authHeaders, role, refreshTeams, setActiveTeamId } = useAppContext();
  const isAdmin = role === "ADMIN";

  const [tab, setTab] = useState<"settings" | "members" | "new">(defaultTab);
  const [rotatedKey, setRotatedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // User Role Mapping state (ADMIN only)
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [memberEmail, setMemberEmail] = useState("");
  const [memberRole, setMemberRole] = useState<UserRole>("AUTHOR");

  // New team form (ADMIN only)
  const [teamName, setTeamName] = useState("");
  const [sfDb, setSfDb] = useState("NEW_HUB_DB");
  const [sfWh, setSfWh] = useState("NEW_RBT_WH");
  const [downstream, setDownstream] = useState("ERP_CORE");
  const [webhookUrl, setWebhookUrl] = useState("");

  const loadMembers = useCallback(async () => {
    if (!activeTeam) return;
    setMembersLoading(true);
    try {
      const list = await rbtApi.listTeamMembers(authHeaders, activeTeam.team_id);
      setMembers(list);
    } catch {
      setMembers([]);
    } finally {
      setMembersLoading(false);
    }
  }, [activeTeam?.team_id, authHeaders.email, authHeaders.role]);

  useEffect(() => {
    if (open) {
      setTab(!isAdmin && defaultTab !== "settings" ? "settings" : defaultTab);
      setErrorMsg(null);
      setStatusMsg(null);
      if (isAdmin) {
        loadMembers();
      }
    }
  }, [open, defaultTab, isAdmin, loadMembers]);

  if (!open) return null;

  const handleRotateKey = async () => {
    if (!activeTeam || !isAdmin) return;
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
    if (!isAdmin) return;
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

  const handleMapUserRole = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeTeam || !isAdmin) return;
    setErrorMsg(null);
    setStatusMsg(null);
    try {
      const saved = await rbtApi.upsertTeamMember(authHeaders, activeTeam.team_id, {
        user_email: memberEmail.trim(),
        user_role: memberRole,
      });
      setMemberEmail("");
      setStatusMsg(`Mapped ${saved.user_email} to ${saved.user_role} in ${activeTeam.team_name}.`);
      await loadMembers();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to map user role");
    }
  };

  const handleUpdateExistingRole = async (m: TeamMember, newRole: UserRole) => {
    if (!activeTeam || !isAdmin) return;
    setErrorMsg(null);
    setStatusMsg(null);
    try {
      await rbtApi.upsertTeamMember(authHeaders, activeTeam.team_id, {
        user_email: m.user_email,
        user_role: newRole,
      });
      setStatusMsg(`Updated ${m.user_email} to ${newRole}.`);
      await loadMembers();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to update role");
    }
  };

  const handleRemoveMember = async (m: TeamMember) => {
    if (!activeTeam || !isAdmin) return;
    setErrorMsg(null);
    setStatusMsg(null);
    try {
      await rbtApi.removeTeamMember(authHeaders, activeTeam.team_id, m.member_id);
      setStatusMsg(`Removed ${m.user_email} from ${activeTeam.team_name}.`);
      await loadMembers();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to remove member");
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
      <div className="w-full max-w-2xl rounded-xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center justify-between border-b border-slate-200 pb-3 dark:border-slate-800">
          <div className="flex items-center gap-2">
            <Settings className="h-5 w-5 text-blue-500" />
            <h2 className="text-lg font-semibold">Team Administration &amp; RBAC Settings</h2>
          </div>
          <button onClick={onClose} className="rounded p-1 hover:bg-slate-100 dark:hover:bg-slate-800">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="mt-4 flex flex-wrap gap-2 border-b border-slate-200 pb-2 dark:border-slate-800">
          <button
            onClick={() => setTab("settings")}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              tab === "settings" ? "bg-blue-600 text-white" : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            }`}
          >
            Active Team Settings
          </button>
          {isAdmin && (
            <button
              onClick={() => {
                setTab("members");
                loadMembers();
              }}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium ${
                tab === "members" ? "bg-blue-600 text-white" : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
              }`}
            >
              <Users className="h-4 w-4" /> User &amp; Role Mapping
            </button>
          )}
          {isAdmin && (
            <button
              onClick={() => setTab("new")}
              className={`inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-sm font-medium ${
                tab === "new" ? "bg-blue-600 text-white" : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
              }`}
            >
              <Plus className="h-4 w-4" /> New Team Workspace
            </button>
          )}
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
                  Current role is {role}. Only ADMIN can create workspaces, map user roles, or rotate API keys.
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

        {tab === "members" && isAdmin && activeTeam && (
          <div className="mt-4 space-y-4 text-sm">
            <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 p-3 text-xs text-slate-600 dark:text-slate-300">
              <strong>RBAC Hierarchy:</strong> <span className="font-mono">ADMIN</span> includes{" "}
              <span className="font-mono">APPROVER</span> + <span className="font-mono">AUTHOR</span> privileges.{" "}
              <span className="font-mono">APPROVER</span> includes <span className="font-mono">AUTHOR</span> privileges
              (cannot create workspaces or map user roles).
            </div>

            <form onSubmit={handleMapUserRole} className="flex flex-wrap items-end gap-2">
              <div className="min-w-[220px] flex-1">
                <label className="mb-1 block text-xs font-medium text-slate-500">User Email</label>
                <input
                  type="email"
                  required
                  value={memberEmail}
                  onChange={(e) => setMemberEmail(e.target.value)}
                  placeholder="user.name@intel.com"
                  className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-1.5 text-xs dark:border-slate-700"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">Assigned Role</label>
                <select
                  value={memberRole}
                  onChange={(e) => setMemberRole(e.target.value as UserRole)}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold dark:border-slate-700 dark:bg-slate-800"
                >
                  <option value="AUTHOR">AUTHOR</option>
                  <option value="APPROVER">APPROVER</option>
                  <option value="ADMIN">ADMIN</option>
                </select>
              </div>
              <button
                type="submit"
                className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-blue-700"
              >
                <UserPlus className="h-3.5 w-3.5" /> Map User Role
              </button>
            </form>

            <div className="max-h-60 space-y-2 overflow-y-auto rounded-lg border border-slate-200 p-3 dark:border-slate-800">
              {membersLoading && <p className="text-xs text-slate-500">Loading team members...</p>}
              {!membersLoading && members.length === 0 && (
                <p className="text-xs text-slate-500">No users mapped to {activeTeam.team_name} yet.</p>
              )}
              {members.map((m) => (
                <div
                  key={m.member_id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-slate-800"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-medium">{m.user_email}</span>
                    <RoleBadge role={m.user_role} />
                  </div>
                  <div className="flex items-center gap-2">
                    <select
                      aria-label={`Change role for ${m.user_email}`}
                      value={m.user_role}
                      onChange={(e) => handleUpdateExistingRole(m, e.target.value as UserRole)}
                      className="rounded border border-slate-300 bg-white px-2 py-1 text-[11px] font-semibold dark:border-slate-700 dark:bg-slate-800"
                    >
                      <option value="AUTHOR">AUTHOR</option>
                      <option value="APPROVER">APPROVER</option>
                      <option value="ADMIN">ADMIN</option>
                    </select>
                    <button
                      type="button"
                      onClick={() => handleRemoveMember(m)}
                      title="Remove user mapping"
                      className="rounded p-1 text-rose-500 hover:bg-rose-500/10"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === "new" && isAdmin && (
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
              className="w-full rounded-lg bg-blue-600 py-2 font-semibold text-white hover:bg-blue-700"
            >
              Create Team Workspace
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
