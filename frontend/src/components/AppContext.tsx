"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { AuthContextHeaders, rbtApi, Team, UserRole } from "@/lib/api";

interface AppContextValue {
  teams: Team[];
  activeTeamId: string;
  activeTeam: Team | undefined;
  setActiveTeamId: (id: string) => void;
  role: UserRole;
  setRole: (role: UserRole) => void;
  userEmail: string;
  authHeaders: AuthContextHeaders;
  cortexModels: string[];
  cortexModel: string;
  setCortexModel: (model: string) => void;
  darkMode: boolean;
  toggleDarkMode: () => void;
  refreshTeams: () => Promise<void>;
  loadingTeams: boolean;
  apiError: string | null;
}

const ROLE_EMAILS: Record<UserRole, string> = {
  AUTHOR: "pdh.author@enterprise.com",
  APPROVER: "pdh.lead@enterprise.com",
  ADMIN: "pdh.admin@enterprise.com",
};

const AppContext = createContext<AppContextValue | undefined>(undefined);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [teams, setTeams] = useState<Team[]>([]);
  const [activeTeamId, setActiveTeamId] = useState<string>("team_ip_supply");
  const [role, setRole] = useState<UserRole>("APPROVER");
  const [cortexModels, setCortexModels] = useState<string[]>([
    "claude-sonnet-4-5",
    "llama3.3-70b",
    "llama3.1-70b",
    "mistral-large2",
  ]);
  const [cortexModel, setCortexModel] = useState<string>("claude-sonnet-4-5");
  const [darkMode, setDarkMode] = useState<boolean>(false);
  const [loadingTeams, setLoadingTeams] = useState<boolean>(true);
  const [apiError, setApiError] = useState<string | null>(null);

  const userEmail = ROLE_EMAILS[role];

  const authHeaders: AuthContextHeaders = {
    email: userEmail,
    role,
    teamId: activeTeamId,
  };

  useEffect(() => {
    const root = document.documentElement;
    if (darkMode) root.classList.add("dark");
    else root.classList.remove("dark");
  }, [darkMode]);

  const refreshTeams = useCallback(async () => {
    setLoadingTeams(true);
    setApiError(null);
    try {
      const list = await rbtApi.listTeams({ email: userEmail, role });
      setTeams(list);
      if (list.length > 0 && !list.some((t) => t.team_id === activeTeamId)) {
        setActiveTeamId(list[0].team_id);
      }
    } catch (err) {
      setApiError(err instanceof Error ? err.message : "Failed to connect to backend API");
    } finally {
      setLoadingTeams(false);
    }
  }, [userEmail, role, activeTeamId]);

  useEffect(() => {
    rbtApi
      .getSsoStatus()
      .then((s) => {
        if (s.connected || s.mock_mode) {
          refreshTeams();
        } else {
          setLoadingTeams(false);
        }
      })
      .catch(() => {
        refreshTeams();
      });
  }, [refreshTeams]);

  useEffect(() => {
    rbtApi
      .listCortexModels()
      .then((res) => {
        if (res.models?.length) setCortexModels(res.models);
        if (res.default_model) setCortexModel(res.default_model);
      })
      .catch(() => {});
  }, []);

  const activeTeam = teams.find((t) => t.team_id === activeTeamId);

  return (
    <AppContext.Provider
      value={{
        teams,
        activeTeamId,
        activeTeam,
        setActiveTeamId,
        role,
        setRole,
        userEmail,
        authHeaders,
        cortexModels,
        cortexModel,
        setCortexModel,
        darkMode,
        toggleDarkMode: () => setDarkMode((d) => !d),
        refreshTeams,
        loadingTeams,
        apiError,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useAppContext() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppContext must be used inside AppProvider");
  return ctx;
}
