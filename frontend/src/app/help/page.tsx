"use client";

import React from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  DatabaseZap,
  HelpCircle,
  LayoutDashboard,
  ShieldCheck,
  Snowflake,
  Sparkles,
  Store,
  TerminalSquare,
  Users,
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
    subtitle: "Connect your Intel account once, select your Data Hub, and check overall gate health.",
    routeHref: "/",
    routeLabel: "Open Executive Dashboard",
    icon: LayoutDashboard,
    screenshot: "/screenshots/step1-dashboard.png",
    screenshotAlt: "Executive Dashboard showing Snowflake SSO button, Team switcher, Cortex model dropdown, and Release Confidence Scorecard",
    whatYouDo: [
      "Click the Snowflake SSO button in the top-right bar. A browser tab opens for Intel Single Sign-On.",
      "Approve the sign-in and return to the app. The button turns green (Snowflake Connected).",
      "Use the Team dropdown in the top bar to pick your Data Hub (IP or IF Supply, Demand, or Material Planning).",
      "Pick your preferred AI model from the Cortex dropdown (default is claude-sonnet-4-5).",
      "Review the Release Confidence & Coverage Scorecard on the dashboard for your team's latest gate health, column coverage %, and schema contract status.",
    ],
    tip: "You only need to click Snowflake SSO once per server session. Every page automatically uses the Team and Cortex model selected in the top bar.",
  },
  {
    stepNumber: 2,
    id: "step-2",
    title: "Onboard a Table & Let AI Create Test Rules",
    subtitle: "Register any Snowflake table or view, snapshot its schema contract, and generate SQL rules automatically.",
    routeHref: "/onboard",
    routeLabel: "Open AI Dataset Onboarding",
    icon: DatabaseZap,
    screenshot: "/screenshots/step2-onboard.png",
    screenshotAlt: "AI Dataset Onboarding wizard showing schema dropdown, table input, and AI rule discovery",
    whatYouDo: [
      "Open AI Dataset Onboarding from the top navigation bar.",
      "Enter a friendly Dataset / Domain Name, pick the Target Schema from the dropdown, and type the Target Table / View name.",
      "Optional: Fill in Temporal Cycle Column (e.g., PLANNING_CYCLE_ID) or Snapshot TS Column so gates can filter by planning cycle.",
      "Optional (v2.1): Enter an Upstream Source / Staging Table to auto-generate staging-to-publish reconciliation rules, or expand Pipeline Transformation SQL / Git PR Diff to generate targeted edge-case checks for your SQL changes.",
      "Click Onboard & Discover Rules. The platform snapshots your baseline schema contract, profiles columns, and uses Cortex AI to generate scored SQL rules.",
    ],
    tip: "Only enter the table or view name in Target Table / View (do not repeat the database or schema prefix).",
  },
  {
    stepNumber: 3,
    id: "step-3",
    title: "Score, Edit, Dry-Run & Approve Rules",
    subtitle: "Adjust Likelihood × Impact risk scores, test SQL safely against PROD/UAT/DEV, and approve rules for gates.",
    routeHref: "/rules",
    routeLabel: "Open Risk Matrix & SQL Studio",
    icon: TerminalSquare,
    screenshot: "/screenshots/step3-rules.png",
    screenshotAlt: "Risk Matrix and SQL Studio showing rule list, quantitative risk score, SQL editor, and dry-run controls",
    whatYouDo: [
      "Open Risk Matrix & SQL Studio to view the Risk Heatmap (Critical 16–25, Elevated 8–15, Low 1–7) and filter rules by dataset, status, priority, or category.",
      "Click any rule to inspect its SQL. Adjust Likelihood (1–5) and Impact (1–5) to recalculate its Risk Score and auto-map P1/P2/P3 priority.",
      "Edit the SQL in the built-in editor (every rule uses __TARGET_DATASET__, and REGRESSION_DIFF rules can also compare against __BASELINE_DATASET__). Click Save SQL to save a new version in History.",
      "Choose an Environment (PROD, UAT, or DEV), optionally enter a Target Schema Override and planning_cycle_id, and click Dry Run. 0 failing rows = PASS.",
      "When a rule is ready for automated quality gates, an APPROVER or ADMIN clicks Approve & Publish. To also publish the rule to other teams, click Share Template.",
      "Use Re-Discover Risks in the top-right to re-run Cortex AI with a new Git PR SQL Diff anytime your pipeline changes.",
    ],
    tip: "Every rule is checked by the sqlglot AST guardrail—only read-only SELECT/WITH queries are allowed, and recursive BOM queries must include a depth limit.",
  },
  {
    stepNumber: 4,
    id: "step-4",
    title: "Run Quality Gates, Check Schema Drift & Triage Failures",
    subtitle: "Execute all approved rules in one batch, verify Step-0 schema contracts, and read plain-English AI diagnosis.",
    routeHref: "/runs",
    routeLabel: "Open Gate Logs & Triage",
    icon: Activity,
    screenshot: "/screenshots/step4-runs.png",
    screenshotAlt: "Gate Logs and Triage page showing run history, confidence scorecard, AI triage summary, and CSV export",
    whatYouDo: [
      "Open Gate Logs & Triage and click Trigger Pre-Flight Gate.",
      "Select your Dataset, Execution Mode (SYNC or ASYNC), Environment (PROD, UAT, or DEV), optional Target Schema Override (e.g., PDH_UAT_DB.PR_1042), and Planning Cycle ID, then click Run Gate Validation.",
      "Review the Step-0 Schema Contract pill: before any SQL runs, the platform checks for dropped columns or narrowed precision (e.g., NUMBER(18,4) → NUMBER(18,0)) and blocks downstream if drift is detected.",
      "If a schema change is intentional, an APPROVER or ADMIN can click Re-Baseline to update the saved schema contract.",
      "Check the Release Confidence Score, Column Test Coverage, and Cortex AI Root-Cause Summary. For failing rules, inspect sample rows or click Download Failing Keys CSV.",
      "For an emergency release on a blocked run, an APPROVER or ADMIN can click Break-Glass Override and enter a 20+ character audit justification.",
    ],
    tip: "External pipelines (Airflow, Control-M, CI/CD) can trigger the exact same gate automatically via POST /api/v1/execution/run-gate using your team's X-API-Key.",
  },
  {
    stepNumber: 5,
    id: "step-5",
    title: "Share & Reuse Rules Across Teams",
    subtitle: "Publish proven checks from your Data Hub and clone them to any other team with automatic column remapping.",
    routeHref: "/marketplace",
    routeLabel: "Open Rule Marketplace",
    icon: Store,
    screenshot: "/screenshots/step5-marketplace.png",
    screenshotAlt: "Rule Marketplace showing shared rules across Data Hubs and Clone to My Team action",
    whatYouDo: [
      "To share one of your team's rules: go to Risk Matrix & SQL Studio (/rules), select an APPROVED rule, and click Share Template.",
      "To reuse another team's rule: open Rule Marketplace and filter by Risk Category.",
      "Click Clone to My Team, choose your target Team and Dataset, and click Clone.",
      "Cortex AI automatically remaps the source SQL column names to match your target table's schema and compiles the new draft rule.",
    ],
    tip: "Cloning creates an independent copy for your team—you can edit, dry-run, and approve it without affecting the original team's rule.",
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
              Self-Service Reference Guide · PDH v2.1
            </div>
            <h1 className="text-2xl font-bold tracking-tight">
              How to Use the Enterprise AI RBT Platform
            </h1>
            <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-300">
              This guide walks you through every screen in plain English—from signing in with
              Snowflake SSO to onboarding datasets, scoring risks, validating across DEV/UAT/PROD,
              running automated quality gates, and sharing rules across Data Hubs.
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

      {/* Reference Cards: Roles, Concepts, FAQ */}
      <section className="grid gap-4 md:grid-cols-3">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-2 flex items-center gap-2 text-sm font-bold">
            <Users className="h-4 w-4 text-blue-500" />
            User Roles &amp; Permissions
          </div>
          <ul className="space-y-2 text-xs text-slate-600 dark:text-slate-300">
            <li>
              <span className="font-mono font-semibold text-slate-900 dark:text-white">AUTHOR:</span>{" "}
              Onboard datasets, run AI discovery, edit &amp; dry-run SQL rules, trigger gates.
            </li>
            <li>
              <span className="font-mono font-semibold text-emerald-600 dark:text-emerald-400">APPROVER:</span>{" "}
              Everything in AUTHOR + approve/archive rules, share templates, <strong>Re-Baseline</strong> schema contracts, and execute <strong>Break-Glass Overrides</strong>.
            </li>
            <li>
              <span className="font-mono font-semibold text-purple-600 dark:text-purple-400">ADMIN:</span>{" "}
              Everything in APPROVER + create/edit teams, manage members, and rotate <code className="font-mono">X-API-Key</code>.
            </li>
          </ul>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-2 flex items-center gap-2 text-sm font-bold">
            <ShieldCheck className="h-4 w-4 text-emerald-500" />
            Key Terms &amp; Guardrails
          </div>
          <ul className="space-y-2 text-xs text-slate-600 dark:text-slate-300">
            <li>
              <strong>0 Rows = PASS:</strong> Every validation query returns only failing rows. Zero rows means the check passed.
            </li>
            <li>
              <strong>Likelihood × Impact (1–25):</strong> Scores 16–25 auto-map to <code className="font-mono">P1_BLOCKER</code>, 8–15 to <code className="font-mono">P2_WARNING</code>, and 1–7 to <code className="font-mono">P3_INFO</code>.
            </li>
            <li>
              <strong>__TARGET_DATASET__ vs __BASELINE_DATASET__:</strong> Rules query <code className="font-mono">__TARGET_DATASET__</code> (routed to your UAT/PR override if set) and can compare against <code className="font-mono">__BASELINE_DATASET__</code> (PROD).
            </li>
          </ul>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-2 flex items-center gap-2 text-sm font-bold">
            <HelpCircle className="h-4 w-4 text-amber-500" />
            Common Questions (FAQ)
          </div>
          <ul className="space-y-2 text-xs text-slate-600 dark:text-slate-300">
            <li>
              <strong>Why isn&apos;t my approved rule in Marketplace?</strong> Approving enables gate execution; click <strong>Share Template</strong> on the approved rule in SQL Studio to publish it to Marketplace.
            </li>
            <li>
              <strong>Why did Step-0 block my gate?</strong> A column was dropped or narrowed (e.g., <code className="font-mono">NUMBER(18,4)</code> → <code className="font-mono">NUMBER(18,0)</code>) vs. the saved baseline. If intentional, click <strong>Re-Baseline</strong>.
            </li>
            <li>
              <strong>Does data leave Snowflake?</strong> No. All profiling, Cortex AI, and SQL checks run inside Snowflake.
            </li>
          </ul>
        </div>
      </section>

      {/* Footer Note */}
      <section className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
        <div className="flex items-center gap-2">
          <Snowflake className="h-4 w-4 text-sky-500" />
          <span>
            Zero-Data-Egress Snowflake + Cortex AI Control Plane · Supports Live SSO &amp; Local Mock Mode
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-purple-500" />
          <span>PDH v2.1</span>
        </div>
      </section>
    </div>
  );
}
