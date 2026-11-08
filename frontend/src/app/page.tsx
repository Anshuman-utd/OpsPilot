"use client";

import { useState } from "react";
import ApiStatusIndicator from "@/components/ApiStatusIndicator";
import DemoServicesOverview from "@/components/DemoServicesOverview";
import IncidentsDashboard from "@/components/IncidentsDashboard";

export default function Home() {
  const [activeTab, setActiveTab] = useState<"incidents" | "services" | "system">("incidents");

  return (
    <div className="flex flex-col min-h-screen bg-slate-950 text-slate-100">
      {/* Top Application Shell Header */}
      <header className="border-b border-slate-800/80 bg-slate-950/90 backdrop-blur sticky top-0 z-40">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          {/* Logo & Platform Name */}
          <div className="flex items-center space-x-3">
            <div className="h-8 w-8 rounded-lg bg-indigo-600 flex items-center justify-center font-mono font-bold text-white shadow-md shadow-indigo-600/30">
              OP
            </div>
            <div>
              <span className="font-bold tracking-tight text-white text-base">OpsPilot</span>
              <span className="ml-2 rounded bg-indigo-950 border border-indigo-700/60 px-2 py-0.5 text-[10px] font-mono text-indigo-300">
                Phase 3 &bull; Incident Engine
              </span>
            </div>
          </div>

          {/* Nav Tabs */}
          <nav className="flex items-center space-x-1 font-mono text-xs bg-slate-900 p-1 rounded-lg border border-slate-800">
            <button
              onClick={() => setActiveTab("incidents")}
              className={`px-3 py-1.5 rounded-md transition-colors ${
                activeTab === "incidents"
                  ? "bg-indigo-600 text-white font-semibold shadow-sm"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              Incidents
            </button>
            <button
              onClick={() => setActiveTab("services")}
              className={`px-3 py-1.5 rounded-md transition-colors ${
                activeTab === "services"
                  ? "bg-indigo-600 text-white font-semibold shadow-sm"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              Microservices
            </button>
            <button
              onClick={() => setActiveTab("system")}
              className={`px-3 py-1.5 rounded-md transition-colors ${
                activeTab === "system"
                  ? "bg-indigo-600 text-white font-semibold shadow-sm"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              Platform Health
            </button>
          </nav>

          {/* Quick External Links */}
          <div className="hidden md:flex items-center space-x-3 text-xs font-mono">
            <a
              href="http://localhost:3001"
              target="_blank"
              rel="noreferrer"
              className="text-slate-400 hover:text-white transition-colors"
            >
              Grafana &rarr;
            </a>
            <span className="text-slate-800">|</span>
            <a
              href="http://localhost:8000/docs"
              target="_blank"
              rel="noreferrer"
              className="text-slate-400 hover:text-white transition-colors"
            >
              API Docs &rarr;
            </a>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="flex-1 max-w-6xl mx-auto px-6 py-8 w-full space-y-8">
        {activeTab === "incidents" && (
          <div className="space-y-6">
            <div>
              <h1 className="text-2xl font-bold text-white tracking-tight">
                Incident Management & Detection
              </h1>
              <p className="text-slate-400 text-xs font-mono mt-1">
                Deterministic Prometheus threshold evaluation with automated deduplication, audit trails, and operator lifecycle controls.
              </p>
            </div>
            <IncidentsDashboard />
          </div>
        )}

        {activeTab === "services" && (
          <div className="space-y-6">
            <div>
              <h1 className="text-2xl font-bold text-white tracking-tight">
                Microservices & Telemetry Stack
              </h1>
              <p className="text-slate-400 text-xs font-mono mt-1">
                Simulated e-commerce microservices with OpenTelemetry distributed tracing and controlled fault injection.
              </p>
            </div>
            <DemoServicesOverview />
          </div>
        )}

        {activeTab === "system" && (
          <div className="space-y-8">
            <div>
              <h1 className="text-2xl font-bold text-white tracking-tight">
                Platform & Infrastructure Status
              </h1>
              <p className="text-slate-400 text-xs font-mono mt-1">
                Health verification for PostgreSQL, Redis, Celery, and background workers.
              </p>
            </div>

            <section className="space-y-3">
              <h2 className="text-xs font-mono uppercase tracking-wider text-slate-400">
                Core Backing Services
              </h2>
              <ApiStatusIndicator />
            </section>

            {/* Engineering Roadmap */}
            <section className="space-y-4">
              <h2 className="text-xs font-mono uppercase tracking-wider text-slate-400">
                Engineering Roadmap
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="rounded-xl border border-emerald-900/60 bg-slate-900/40 p-5 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-mono font-semibold text-emerald-400">Phase 1</span>
                    <span className="rounded bg-emerald-950 border border-emerald-700/60 px-2 py-0.5 text-[10px] font-mono text-emerald-300">
                      Completed
                    </span>
                  </div>
                  <h3 className="font-semibold text-white text-sm">Foundation & Core Platform</h3>
                  <p className="text-xs text-slate-400 leading-relaxed">
                    FastAPI backend, PostgreSQL 16, Redis 7, Celery, Alembic migrations, and Next.js frontend.
                  </p>
                </div>

                <div className="rounded-xl border border-emerald-900/60 bg-slate-900/40 p-5 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-mono font-semibold text-emerald-400">Phase 2</span>
                    <span className="rounded bg-emerald-950 border border-emerald-700/60 px-2 py-0.5 text-[10px] font-mono text-emerald-300">
                      Completed
                    </span>
                  </div>
                  <h3 className="font-semibold text-white text-sm">Microservices & Observability</h3>
                  <p className="text-xs text-slate-400 leading-relaxed">
                    API Gateway, Order & Payment microservices, Prometheus, Loki, Tempo, OpenTelemetry, and 6 fault scenarios.
                  </p>
                </div>

                <div className="rounded-xl border border-indigo-900/80 bg-slate-900/40 p-5 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-mono font-semibold text-indigo-400">Phase 3</span>
                    <span className="rounded bg-indigo-950 border border-indigo-700/60 px-2 py-0.5 text-[10px] font-mono text-indigo-300">
                      Active
                    </span>
                  </div>
                  <h3 className="font-semibold text-white text-sm">Incident Management & Detection</h3>
                  <p className="text-xs text-slate-400 leading-relaxed">
                    Deterministic threshold engine, deduplication, state machine lifecycle, and interactive SRE dashboard.
                  </p>
                </div>
              </div>
            </section>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-900 bg-slate-950 py-6 text-center text-xs font-mono text-slate-500">
        OpsPilot &copy; 2026 &mdash; Autonomous Site Reliability Engineering Platform
      </footer>
    </div>
  );
}
