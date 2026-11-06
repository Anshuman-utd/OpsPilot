import ApiStatusIndicator from "@/components/ApiStatusIndicator";
import DemoServicesOverview from "@/components/DemoServicesOverview";

export default function Home() {
  return (
    <div className="flex flex-col min-h-screen">
      {/* Top Navigation */}
      <header className="border-b border-slate-800 bg-slate-950/80 backdrop-blur sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="h-8 w-8 rounded-lg bg-indigo-600 flex items-center justify-center font-mono font-bold text-white shadow-lg shadow-indigo-500/20">
              OP
            </div>
            <div>
              <span className="font-bold tracking-tight text-white text-lg">OpsPilot</span>
              <span className="ml-2 rounded bg-indigo-950/80 border border-indigo-700/60 px-2 py-0.5 text-[10px] font-mono text-indigo-300">
                Phase 1 Foundation
              </span>
            </div>
          </div>

          <div className="flex items-center space-x-4 text-xs font-mono">
            <a
              href="http://localhost:8000/docs"
              target="_blank"
              rel="noreferrer"
              className="text-slate-400 hover:text-white transition-colors"
            >
              API Docs (Swagger) &rarr;
            </a>
            <span className="text-slate-700">|</span>
            <span className="text-emerald-400 flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse"></span>
              Environment Ready
            </span>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-6xl mx-auto px-6 py-10 w-full space-y-10">
        {/* Hero Section */}
        <div className="space-y-4">
          <div className="inline-flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900 px-3 py-1 text-xs text-slate-300 font-mono">
            <span className="h-1.5 w-1.5 rounded-full bg-indigo-400"></span>
            SRE Platform Architecture
          </div>
          <h1 className="text-3xl md:text-5xl font-extrabold tracking-tight text-white max-w-3xl leading-tight">
            Autonomous Incident Investigation & Root Cause Analysis
          </h1>
          <p className="text-slate-400 text-base md:text-lg max-w-2xl leading-relaxed">
            OpsPilot shifts incident response from reactive alert fatigue to autonomous multi-agent
            triaging. Correlating metrics, logs, and distributed traces into evidence-backed
            hypotheses with strict human-in-the-loop remediation safety.
          </p>
        </div>

        {/* Live Backend & Infrastructure Status Indicator */}
        <section className="space-y-4">
          <h2 className="text-xs font-mono uppercase tracking-wider text-slate-400">
            Platform Health & Core Infrastructure
          </h2>
          <ApiStatusIndicator />
          <DemoServicesOverview />
        </section>

        {/* Project Roadmap & Architecture Stages */}
        <section className="space-y-4">
          <h2 className="text-xs font-mono uppercase tracking-wider text-slate-400">
            Engineering Milestones
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="rounded-xl border border-emerald-900/60 bg-slate-900/40 p-5 space-y-3 relative overflow-hidden">
              <div className="absolute top-0 right-0 h-16 w-16 bg-emerald-500/10 rounded-bl-full pointer-events-none" />
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-semibold text-emerald-400">Phase 1</span>
                <span className="rounded bg-emerald-950 border border-emerald-700/60 px-2 py-0.5 text-[10px] font-mono text-emerald-300">
                  Active
                </span>
              </div>
              <h3 className="font-semibold text-white">Project Foundation</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                FastAPI async backend, Next.js App Router, PostgreSQL 16 & Redis 7 Docker Compose,
                Alembic migrations, and automated test suite.
              </p>
            </div>

            <div className="rounded-xl border border-indigo-900/60 bg-slate-900/40 p-5 space-y-3 relative overflow-hidden">
              <div className="absolute top-0 right-0 h-16 w-16 bg-indigo-500/10 rounded-bl-full pointer-events-none" />
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-semibold text-indigo-400">Phase 2</span>
                <span className="rounded bg-indigo-950 border border-indigo-700/60 px-2 py-0.5 text-[10px] font-mono text-indigo-300">
                  Active
                </span>
              </div>
              <h3 className="font-semibold text-white">Microservices & Telemetry</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Distributed demo microservices (Gateway, Order, Payment), Prometheus metrics, Loki
                logs, Tempo traces, and controlled fault injection.
              </p>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/20 p-5 space-y-3 opacity-80">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-semibold text-slate-400">Phases 3 - 5</span>
                <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-slate-400">
                  Planned
                </span>
              </div>
              <h3 className="font-semibold text-slate-200">LangGraph Agents & HITL</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Multi-agent RCA investigation graph, evidence synthesis, safe remediation planner,
                and verification agent.
              </p>
            </div>
          </div>
        </section>

        {/* Quick Developer Reference */}
        <section className="space-y-3">
          <h2 className="text-xs font-mono uppercase tracking-wider text-slate-400">
            Local Development Commands
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 font-mono text-xs">
            <div className="rounded-lg border border-slate-800 bg-slate-950 p-4 space-y-2">
              <span className="text-slate-400 font-semibold">{"// Start Infrastructure"}</span>
              <pre className="text-emerald-400 overflow-x-auto">docker-compose up -d</pre>
              <p className="text-slate-400 text-[11px]">Starts PostgreSQL 16 on :5432 and Redis 7 on :6379.</p>
            </div>

            <div className="rounded-lg border border-slate-800 bg-slate-950 p-4 space-y-2">
              <span className="text-slate-400 font-semibold">{"// Start Backend API"}</span>
              <pre className="text-emerald-400 overflow-x-auto">cd backend && uv run uvicorn app.main:app --reload</pre>
              <p className="text-slate-400 text-[11px]">Serves FastAPI with Swagger UI at /docs.</p>
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-900 bg-slate-950 py-6 text-center text-xs font-mono text-slate-500">
        OpsPilot &copy; 2026 &mdash; Production Foundation for Autonomous SRE
      </footer>
    </div>
  );
}
