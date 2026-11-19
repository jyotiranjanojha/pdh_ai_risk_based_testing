"use client";

import React from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  DatabaseZap,
  LayoutDashboard,
  ShieldCheck,
  Snowflake,
  Sparkles,
  Store,
  TerminalSquare,
} from "lucide-react";

interface GuideStep {
  stepNumber: number;
  id: string;
  title: string;
  subtitle: string;
  routeHref: string;
  routeLabel: string;
  icon: React.ComponentType<{ className?: string }>;
  screenshot: string;
  screenshotAlt: string;
  whatYouDo: string[];
  tip: string;
}

const STEPS: GuideStep[] = [
  {
    stepNumber: 1,
    id: "step-1",
    title: "Sign In with Snowflake SSO & Choose Your Team",
    subtitle: "Connect your Intel account once and pick your Data Hub from the top bar.",
    routeHref: "/",
    routeLabel: "Go to Executive Dashboard",
    icon: LayoutDashboard,
    screenshot: "/screenshots/step1-dashboard.png",
    screenshotAlt: "Executive Dashboard showing Snowflake SSO button, Team switcher, and Cortex model dropdown",
    whatYouDo: [
      "Click the Snowflake SSO button in the top bar. A new browser tab will open for Intel Single Sign-On.",
      "Approve the sign-in in that tab and return here. The button will turn green (Snowflake Connected).",
      "Use the Team dropdown at the top to switch between the 6 Data Hubs (IP/IF Supply, Demand, and Material Planning).",
      "Pick your preferred AI model from the Cortex dropdown (default is claude-sonnet-4-5).",
    ],
    tip: "You only need to click Snowflake SSO once per server session. Every page automatically uses your active Team.",
  },
  {
    stepNumber: 2,
    id: "step-2",
    title: "Add a Table & Let AI Create Test Rules",
    subtitle: "Point the platform to any Snowflake table and let Cortex AI write quality checks for you.",
    routeHref: "/onboard",
    routeLabel: "Open AI Dataset Onboarding",
    icon: DatabaseZap,
    screenshot: "/screenshots/step2-onboard.png",
    screenshotAlt: "AI Dataset Onboarding page for inspecting Snowflake tables and generating Cortex AI rules",
    whatYouDo: [
      "Open AI Dataset Onboarding from the top navigation.",
      "Enter the Snowflake database, schema, and table name you want to test.",
      "Click Inspect & Profile to read the table columns and calculate a risk profile.",
      "Click Generate AI Test Cases. Snowflake Cortex will automatically create SQL rules for completeness, uniqueness, freshness, and business logic.",
      "Review the suggested rules and click Save to add them to your team.",
    ],
    tip: "Want a different AI style? Switch the Cortex model in the top bar before clicking Generate AI Test Cases.",
  },
  {
    stepNumber: 3,
    id: "step-3",
    title: "Review, Edit & Run Individual Rules",
    subtitle: "Fine-tune SQL checks in the built-in editor with automatic version history.",
    routeHref: "/rules",
    routeLabel: "Open Risk Matrix & SQL Studio",
    icon: TerminalSquare,
    screenshot: "/screenshots/step3-rules.png",
    screenshotAlt: "Risk Matrix and SQL Studio page showing rule list and SQL editor",
    whatYouDo: [
      "Open Risk Matrix & SQL Studio to see all rules for your selected Data Hub.",
      "Click any rule to view or edit its SQL query in the code editor.",
      "Click Save Changes if you modify a rule — the platform automatically saves a new version without losing old versions.",
      "Click Run on a single rule to test it live against Snowflake right away.",
    ],
    tip: "Rules that return 0 failing rows pass. Any returned rows are flagged so you can inspect exact bad records.",
  },
  {
    stepNumber: 4,
    id: "step-4",
    title: "Run Full Quality Gates & Read AI Diagnosis",
    subtitle: "Execute all active rules together and get plain-English AI root-cause hints when something fails.",
    routeHref: "/runs",
    routeLabel: "Open Gate Logs & Triage",
    icon: Activity,
    screenshot: "/screenshots/step4-runs.png",
    screenshotAlt: "Gate Logs and Triage page showing batch execution history and AI diagnosis",
    whatYouDo: [
      "Open Gate Logs & Triage from the navigation bar.",
      "Click Run Gate Suite to run all active rules for your team in one batch.",
      "Check the Pass Rate, Failed Row Counts, and Risk Score for the run.",
      "For any failed rule, expand the AI Diagnosis panel to read why it likely failed and how to fix it.",
    ],
    tip: "Use this page as your daily health check before publishing or promoting supply-chain data.",
  },
  {
    stepNumber: 5,
    id: "step-5",
    title: "Reuse Rules Across Teams",
    subtitle: "Share proven data quality checks across IP and IF Data Hubs in one click.",
    routeHref: "/marketplace",
    routeLabel: "Open Rule Marketplace",
    icon: Store,
    screenshot: "/screenshots/step5-marketplace.png",
    screenshotAlt: "Rule Marketplace page showing reusable rules across Data Hubs",
    whatYouDo: [
      "Open Rule Marketplace to browse rules published by any Data Hub.",
      "Search or filter by rule category (Completeness, Uniqueness, Freshness, Referential, Business Rule).",
      "Click Clone to Team to copy a rule directly into your active Data Hub.",
    ],
    tip: "Cloning creates an independent copy for your team so you can customize its SQL without affecting other teams.",
  },
];

export default function HelpPage() {
  return (
    <div className="space-y-8">
      {/* Header Banner */}
      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <div className="inline-flex items-center gap-2 rounded-full bg-blue-600/10 px-3 py-1 text-xs font-semibold text-blue-600 dark:bg-blue-500/20 dark:text-blue-400">
              <BookOpen className="h-3.5 w-3.5" />
              Step-by-Step User Guide
            </div>
            <h1 className="text-2xl font-bold tracking-tight">
              How to Use the Enterprise AI RBT Platform
            </h1>
            <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-300">
              Welcome! This guide walks you through every part of the application in simple steps—from
              signing in with Snowflake SSO to generating AI test rules, running quality gates, and
              sharing rules across Data Hubs.
            </p>
          </div>
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-blue-700"
          >
            Back to Dashboard
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>

        {/* Quick Jump Links */}
        <div className="mt-5 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          {STEPS.map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-medium transition hover:border-blue-400 hover:bg-blue-50/50 dark:border-slate-800 dark:bg-slate-800/50 dark:hover:border-blue-500/60"
            >
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-600 text-[11px] font-bold text-white">
                {s.stepNumber}
              </span>
              <span className="truncate">{s.title}</span>
            </a>
          ))}
        </div>
      </section>

      {/* Step Cards with Screenshots */}
      <div className="space-y-6">
        {STEPS.map((step) => {
          const Icon = step.icon;
          return (
            <section
              key={step.id}
              id={step.id}
              className="scroll-mt-24 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900"
            >
              <div className="grid gap-6 lg:grid-cols-12 lg:items-start">
                {/* Left: Instructions */}
                <div className="space-y-4 lg:col-span-5">
                  <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-blue-600 text-sm font-bold text-white">
                      {step.stepNumber}
                    </div>
                    <div>
                      <h2 className="flex items-center gap-2 text-lg font-bold">
                        <Icon className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                        {step.title}
                      </h2>
                      <p className="text-xs text-slate-500 dark:text-slate-400">{step.subtitle}</p>
                    </div>
                  </div>

                  <ol className="space-y-2.5 text-sm text-slate-700 dark:text-slate-300">
                    {step.whatYouDo.map((line, idx) => (
                      <li key={idx} className="flex items-start gap-2.5">
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                        <span>{line}</span>
                      </li>
                    ))}
                  </ol>

                  <div className="rounded-xl border border-blue-200 bg-blue-50/70 p-3 text-xs text-blue-900 dark:border-blue-900/60 dark:bg-blue-950/40 dark:text-blue-200">
                    <span className="font-semibold">Tip: </span>
                    {step.tip}
                  </div>

                  <Link
                    href={step.routeHref}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
                  >
                    {step.routeLabel}
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                </div>

                {/* Right: Real Application Screenshot */}
                <div className="lg:col-span-7">
                  <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-100 shadow-sm dark:border-slate-700 dark:bg-slate-950">
                    <div className="flex items-center gap-1.5 border-b border-slate-200 bg-slate-50 px-3 py-2 text-[11px] text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
                      <span className="h-2.5 w-2.5 rounded-full bg-rose-400" />
                      <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
                      <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
                      <span className="ml-2 font-mono">{step.routeHref}</span>
                    </div>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={step.screenshot}
                      alt={step.screenshotAlt}
                      className="w-full object-cover object-top"
                    />
                  </div>
                </div>
              </div>
            </section>
          );
        })}
      </div>

      {/* Quick Reference Footer */}
      <section className="grid gap-4 md:grid-cols-3">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-2 flex items-center gap-2 text-sm font-bold">
            <Snowflake className="h-4 w-4 text-sky-500" />
            Live Snowflake Mode
          </div>
          <p className="text-xs leading-relaxed text-slate-600 dark:text-slate-400">
            All metadata, SQL rules, and execution logs are stored in{" "}
            <span className="font-mono">IF_SUPPLY_CHAIN.PDH_SUPPLY_PLANNING_SRC_BASE</span>. Your
            data never leaves Snowflake.
          </p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-2 flex items-center gap-2 text-sm font-bold">
            <Sparkles className="h-4 w-4 text-purple-500" />
            AI Models (Cortex)
          </div>
          <p className="text-xs leading-relaxed text-slate-600 dark:text-slate-400">
            Use the Cortex dropdown in the top bar anytime to switch between{" "}
            <span className="font-mono">claude-sonnet-4-5</span>,{" "}
            <span className="font-mono">llama3.3-70b</span>,{" "}
            <span className="font-mono">llama3.1-70b</span>, and{" "}
            <span className="font-mono">mistral-large2</span>.
          </p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-2 flex items-center gap-2 text-sm font-bold">
            <ShieldCheck className="h-4 w-4 text-emerald-500" />
            Roles & Teams
          </div>
          <p className="text-xs leading-relaxed text-slate-600 dark:text-slate-400">
            <strong>ADMIN</strong> can create new teams (+ New Team) and manage settings.{" "}
            <strong>ENGINEER</strong> can onboard tables, edit rules, and run gates.{" "}
            <strong>VIEWER</strong> has read-only access.
          </p>
        </div>
      </section>
    </div>
  );
}
