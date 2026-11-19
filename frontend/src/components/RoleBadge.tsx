"use client";

import React from "react";
import { ShieldAlert, ShieldCheck, UserCog } from "lucide-react";
import { GateDecision, RiskPriority, RuleStatus, UserRole } from "@/lib/api";

export function RoleBadge({ role }: { role: UserRole }) {
  const styles: Record<UserRole, string> = {
    AUTHOR: "bg-sky-500/15 text-sky-400 border-sky-500/30",
    APPROVER: "bg-amber-500/15 text-amber-400 border-amber-500/30",
    ADMIN: "bg-purple-500/15 text-purple-400 border-purple-500/30",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${styles[role]}`}
    >
      <UserCog className="h-3 w-3" />
      {role}
    </span>
  );
}

export function PriorityBadge({ priority }: { priority: RiskPriority }) {
  const map: Record<RiskPriority, string> = {
    P1_BLOCKER: "bg-rose-500/15 text-rose-400 border-rose-500/30",
    P2_WARNING: "bg-amber-500/15 text-amber-400 border-amber-500/30",
    P3_INFO: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  };
  return (
    <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${map[priority]}`}>
      {priority}
    </span>
  );
}

export function StatusBadge({ status }: { status: RuleStatus }) {
  const map: Record<RuleStatus, string> = {
    APPROVED: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
    DRAFT: "bg-sky-500/15 text-sky-400 border-sky-500/30",
    DRAFT_NEEDS_REVIEW: "bg-rose-500/15 text-rose-400 border-rose-500/30",
    ARCHIVED: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  };
  return (
    <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${map[status]}`}>
      {status}
    </span>
  );
}

export function GateDecisionBadge({ decision }: { decision: GateDecision }) {
  const map: Record<GateDecision, string> = {
    PASS: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
    PASS_WITH_WARNINGS: "bg-amber-500/15 text-amber-400 border-amber-500/30",
    BLOCKED_DOWNSTREAM: "bg-rose-500/15 text-rose-400 border-rose-500/30",
    OVERRIDDEN_PASS: "bg-orange-500/15 text-orange-400 border-orange-500/30",
    PENDING: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  };
  const Icon = decision === "BLOCKED_DOWNSTREAM" ? ShieldAlert : ShieldCheck;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${map[decision]}`}>
      <Icon className="h-3.5 w-3.5" />
      {decision}
    </span>
  );
}
