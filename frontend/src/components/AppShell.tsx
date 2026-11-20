"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  DatabaseZap,
  HelpCircle,
  LayoutDashboard,
  Loader2,
  LogIn,
  Moon,
  Plus,
  Settings,
  Snowflake,
  Store,
  Sun,
  TerminalSquare,
} from "lucide-react";
import { rbtApi, UserRole } from "@/lib/api";
import { useAppContext } from "./AppContext";
import { RoleBadge } from "./RoleBadge";
import { TeamSettingsModal } from "./TeamSettingsModal";

const NAV_ITEMS = [
  { href: "/", label: "Executive Dashboard", icon: LayoutDashboard },
  { href: "/onboard", label: "AI Dataset Onboarding", icon: DatabaseZap },
  { href: "/rules", label: "Risk Matrix & SQL Studio", icon: TerminalSquare },
  { href: "/marketplace", label: "Rule Marketplace", icon: Store },
  { href: "/runs", label: "Gate Logs & Triage", icon: Activity },
  { href: "/help", label: "Help & Guide", icon: HelpCircle },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const {
    teams,
    activeTeamId,
    setActiveTeamId,
    role,
    setRole,
    userEmail,
    cortexModels,
    cortexModel,
    setCortexModel,
    darkMode,
    toggleDarkMode,
    refreshTeams,
    apiError,
  } = useAppContext();

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<"settings" | "new">("settings");
  const [ssoState, setSsoState] = useState<{
    connected: boolean;
    mock_mode: boolean;
  }>({ connected: false, mock_mode: false });
  const [ssoLoading, setSsoLoading] = useState(false);

  React.useEffect(() => {
    rbtApi
      .getSsoStatus()
      .then((s) => setSsoState({ connected: s.connected, mock_mode: s.mock_mode }))
      .catch(() => {});
  }, []);

  const handleSnowflakeSso = async () => {
    setSsoLoading(true);
    try {
      const res = await rbtApi.startSsoLogin();
      if (res.connected) {
        setSsoState({ connected: true, mock_mode: res.mock_mode });
        await refreshTeams();
        setSsoLoading(false);
        return;
      }
      if (res.sso_url) {
        window.open(res.sso_url, "_blank", "noopener,noreferrer");
      }
      const timer = setInterval(async () => {
        try {
          const st = await rbtApi.getSsoStatus();
          if (st.connected) {
            clearInterval(timer);
            setSsoState({ connected: true, mock_mode: st.mock_mode });
            setSsoLoading(false);
            await refreshTeams();
          }
        } catch {
          // keep polling
        }
      }, 1500);
      setTimeout(() => {
        clearInterval(timer);
        setSsoLoading(false);
      }, 90000);
    } catch {
      setSsoLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 dark:bg-[#0b1120] dark:text-slate-100">
      {/* Persistent Top Bar */}
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-[#0f172a]/90">
        <div className="flex w-full flex-wrap items-center justify-between gap-3 px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 items-center justify-center rounded-lg bg-[#0068B5] px-2.5 shadow-sm">
              <svg
                viewBox="0 0 92 32"
                className="h-6 w-auto"
                aria-label="Intel"
                role="img"
              >
                <rect x="4" y="4" width="5" height="5" rx="0.8" fill="#00C7FD" />
                <text
                  x="3"
                  y="26"
                  fill="#FFFFFF"
                  fontFamily="Arial, Helvetica, sans-serif"
                  fontWeight="700"
                  fontSize="22"
                  letterSpacing="-0.5"
                >
                  intel.
                </text>
              </svg>
            </div>
            <div>
              <div className="text-sm font-bold tracking-tight">Enterprise AI RBT Platform</div>
              <div className="text-[11px] text-slate-500 dark:text-slate-400">
                Snowflake Zero-Egress Control Plane · PDH v2.1
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {/* Team Switcher */}
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-medium text-slate-500">Team:</span>
              <select
                aria-label="Team Switcher"
                value={activeTeamId}
                onChange={(e) => setActiveTeamId(e.target.value)}
                className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold dark:border-slate-700 dark:bg-slate-800"
              >
                {teams.map((t) => (
                  <option key={t.team_id} value={t.team_id}>
                    {t.team_name} ({t.snowflake_warehouse})
                  </option>
                ))}
              </select>
              {role === "ADMIN" && (
                <button
                  onClick={() => {
                    setSettingsTab("new");
                    setSettingsOpen(true);
                  }}
                  title="Create New Team Workspace (ADMIN)"
                  className="inline-flex items-center gap-1 rounded-lg border border-blue-500/40 bg-blue-500/10 px-2.5 py-1.5 text-xs font-semibold text-blue-500 hover:bg-blue-500/20"
                >
                  <Plus className="h-3.5 w-3.5" /> New Team
                </button>
              )}
            </div>

            {/* Cortex Model Dropdown */}
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-medium text-slate-500">Cortex:</span>
              <select
                aria-label="Cortex Model"
                value={cortexModel}
                onChange={(e) => setCortexModel(e.target.value)}
                className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 font-mono text-[11px] font-semibold dark:border-slate-700 dark:bg-slate-800"
              >
                {cortexModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>

            {/* Demo RBAC Role Switcher */}
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-100 px-2 py-1 dark:border-slate-700 dark:bg-slate-800/80">
              <span className="text-[11px] font-medium text-slate-500">Demo Role:</span>
              {(["AUTHOR", "APPROVER", "ADMIN"] as UserRole[]).map((r) => (
                <button
                  key={r}
                  onClick={() => setRole(r)}
                  className={`rounded px-2 py-0.5 text-[11px] font-semibold transition ${
                    role === r
                      ? "bg-blue-600 text-white shadow-sm"
                      : "text-slate-600 hover:bg-slate-200 dark:text-slate-300 dark:hover:bg-slate-700"
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>

            <div className="hidden items-center gap-2 md:flex">
              <RoleBadge role={role} />
              <span className="text-xs text-slate-500">{userEmail}</span>
            </div>

            {/* Snowflake SSO Status / Sign-On Icon Button */}
            <button
              onClick={handleSnowflakeSso}
              disabled={ssoLoading}
              title={
                ssoState.mock_mode
                  ? "Running in Local Mock Mode"
                  : ssoState.connected
                  ? "Snowflake SSO Connected"
                  : "Click to sign in to Snowflake via Intel SSO"
              }
              className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition ${
                ssoState.mock_mode
                  ? "border-slate-300 bg-slate-100 text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                  : ssoState.connected
                  ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-400"
                  : "border-sky-500/50 bg-sky-600 text-white hover:bg-sky-700"
              }`}
            >
              {ssoLoading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : ssoState.connected ? (
                <Snowflake className="h-3.5 w-3.5" />
              ) : (
                <LogIn className="h-3.5 w-3.5" />
              )}
              {ssoLoading
                ? "Waiting SSO..."
                : ssoState.mock_mode
                ? "Mock Mode"
                : ssoState.connected
                ? "Snowflake Live"
                : "Snowflake SSO"}
            </button>

            <button
              onClick={() => {
                setSettingsTab("settings");
                setSettingsOpen(true);
              }}
              title="Team Settings & API Keys"
              className="rounded-lg border border-slate-300 p-1.5 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
            >
              <Settings className="h-4 w-4" />
            </button>

            <button
              onClick={toggleDarkMode}
              title="Toggle Theme"
              className="rounded-lg border border-slate-300 p-1.5 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
            >
              {darkMode ? <Sun className="h-4 w-4 text-amber-400" /> : <Moon className="h-4 w-4" />}
            </button>
          </div>
        </div>

        {/* Route Navigation */}
        <nav className="flex w-full gap-1 overflow-x-auto px-6 pb-2">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                  active
                    ? "bg-blue-600/10 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400"
                    : "text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </header>

      <main className="w-full px-6 py-6">
        {apiError && (
          <div className="mb-4 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-400">
            Backend connection notice: {apiError}. Ensure FastAPI is running on port 8000.
          </div>
        )}
        {children}
      </main>

      <TeamSettingsModal
        open={settingsOpen}
        defaultTab={settingsTab}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
  );
}
