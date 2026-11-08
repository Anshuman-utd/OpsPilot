"use client";

import { useEffect, useState, useCallback } from "react";

export interface IncidentEvent {
  id: string;
  incident_id: string;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  message: string;
  actor: string;
  payload?: Record<string, unknown>;
  created_at: string;
}

export interface Incident {
  id: string;
  title: string;
  description: string | null;
  service_name: string;
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO";
  status: "OPEN" | "ACKNOWLEDGED" | "INVESTIGATING" | "RESOLVED";
  incident_type: string;
  source: string;
  fingerprint: string;
  triggered_value: number | null;
  threshold_value: number | null;
  occurrence_count: number;
  triggered_at: string;
  last_seen_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface IncidentDetail extends Incident {
  evidence?: Record<string, unknown>;
  metadata_json?: Record<string, unknown>;
  events: IncidentEvent[];
}

export interface IncidentStats {
  total_incidents: number;
  open_incidents: number;
  critical_incidents: number;
  high_incidents: number;
  acknowledged_incidents: number;
  investigating_incidents: number;
  resolved_incidents: number;
  by_service: Record<string, number>;
  by_severity: Record<string, number>;
}

export interface DetectionRunResult {
  status: string;
  rules_evaluated: number;
  breaches_detected: number;
  incidents_created: number;
  incidents_updated: number;
  incidents_resolved: number;
  evaluation_duration_ms: number;
}

export default function IncidentsDashboard() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [stats, setStats] = useState<IncidentStats | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState<string>("active");
  const [severityFilter, setSeverityFilter] = useState<string>("ALL");
  const [serviceFilter, setServiceFilter] = useState<string>("ALL");

  // Selected Incident Drawer
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState<boolean>(false);
  const [actionLoading, setActionLoading] = useState<boolean>(false);

  // Detection Run
  const [evaluating, setEvaluating] = useState<boolean>(false);
  const [evalResult, setEvalResult] = useState<DetectionRunResult | null>(null);

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  // Fetch stats and incident list
  const fetchIncidents = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (statusFilter !== "ALL") params.append("status", statusFilter);
      if (severityFilter !== "ALL") params.append("severity", severityFilter);
      if (serviceFilter !== "ALL") params.append("service", serviceFilter);

      const [incRes, statsRes] = await Promise.all([
        fetch(`${apiUrl}/api/v1/incidents?${params.toString()}`, { cache: "no-store" }),
        fetch(`${apiUrl}/api/v1/incidents/stats`, { cache: "no-store" }),
      ]);

      if (incRes.ok && statsRes.ok) {
        const incData = await incRes.json();
        const statsData = await statsRes.json();
        setIncidents(incData);
        setStats(statsData);
        setError(null);
      } else {
        setError(`Failed to fetch incidents (HTTP ${incRes.status})`);
      }
    } catch (err) {
      setError(`Backend connection error: ${String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [apiUrl, statusFilter, severityFilter, serviceFilter]);

  useEffect(() => {
    let isMounted = true;
    const run = async () => {
      if (isMounted) await fetchIncidents();
    };
    void run();

    const interval = setInterval(run, 8000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [fetchIncidents]);

  useEffect(() => {
    let isMounted = true;
    if (selectedId) {
      fetch(`${apiUrl}/api/v1/incidents/${selectedId}`, { cache: "no-store" })
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => {
          if (isMounted) {
            setDetail(data);
            setDetailLoading(false);
          }
        })
        .catch(() => {
          if (isMounted) setDetailLoading(false);
        });
    }
    return () => {
      isMounted = false;
    };
  }, [selectedId, apiUrl]);

  // Lifecycle action handler
  const handleAction = async (action: "acknowledge" | "investigate" | "resolve") => {
    if (!selectedId) return;
    setActionLoading(true);
    try {
      const res = await fetch(`${apiUrl}/api/v1/incidents/${selectedId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actor: "sre-engineer", comment: `Status updated via OpsPilot Web UI` }),
      });
      if (res.ok) {
        const updated = await res.json();
        setDetail(updated);
        await fetchIncidents();
      }
    } catch {
      // ignore
    } finally {
      setActionLoading(false);
    }
  };

  // Trigger immediate detection run
  const handleRunEvaluation = async () => {
    setEvaluating(true);
    try {
      const res = await fetch(`${apiUrl}/api/v1/incidents/evaluate`, {
        method: "POST",
      });
      if (res.ok) {
        const data = await res.json();
        setEvalResult(data);
        await fetchIncidents();
      }
    } catch {
      // ignore
    } finally {
      setEvaluating(false);
    }
  };

  const getSeverityBadge = (sev: string) => {
    switch (sev) {
      case "CRITICAL":
        return "bg-rose-500/10 text-rose-400 border-rose-500/30";
      case "HIGH":
        return "bg-amber-500/10 text-amber-400 border-amber-500/30";
      case "MEDIUM":
        return "bg-yellow-500/10 text-yellow-300 border-yellow-500/30";
      default:
        return "bg-slate-500/10 text-slate-400 border-slate-500/30";
    }
  };

  const getStatusBadge = (st: string) => {
    switch (st) {
      case "OPEN":
        return "bg-rose-950 text-rose-300 border-rose-800/60";
      case "ACKNOWLEDGED":
        return "bg-sky-950 text-sky-300 border-sky-800/60";
      case "INVESTIGATING":
        return "bg-indigo-950 text-indigo-300 border-indigo-800/60";
      case "RESOLVED":
        return "bg-emerald-950 text-emerald-300 border-emerald-800/60";
      default:
        return "bg-slate-900 text-slate-300 border-slate-800";
    }
  };

  const formatTimestamp = (isoString: string) => {
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return isoString;
    }
  };

  return (
    <div className="space-y-6">
      {/* Top Stat Cards Bar */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4">
          <div className="text-[11px] font-mono uppercase tracking-wider text-slate-400">
            Active Incidents
          </div>
          <div className="text-2xl font-bold font-mono text-white mt-1">
            {stats ? stats.open_incidents + stats.acknowledged_incidents + stats.investigating_incidents : "-"}
          </div>
          <div className="text-[11px] text-slate-500 mt-1 font-mono">Requires Attention</div>
        </div>

        <div className="bg-slate-900/60 border border-rose-950/60 rounded-xl p-4">
          <div className="text-[11px] font-mono uppercase tracking-wider text-rose-400">
            Critical Severity
          </div>
          <div className="text-2xl font-bold font-mono text-rose-400 mt-1">
            {stats ? stats.critical_incidents : "-"}
          </div>
          <div className="text-[11px] text-rose-500/70 mt-1 font-mono">P0 Outages</div>
        </div>

        <div className="bg-slate-900/60 border border-amber-950/60 rounded-xl p-4">
          <div className="text-[11px] font-mono uppercase tracking-wider text-amber-400">
            High Severity
          </div>
          <div className="text-2xl font-bold font-mono text-amber-400 mt-1">
            {stats ? stats.high_incidents : "-"}
          </div>
          <div className="text-[11px] text-amber-500/70 mt-1 font-mono">SLA Impact</div>
        </div>

        <div className="bg-slate-900/60 border border-emerald-950/60 rounded-xl p-4">
          <div className="text-[11px] font-mono uppercase tracking-wider text-emerald-400">
            Resolved
          </div>
          <div className="text-2xl font-bold font-mono text-emerald-400 mt-1">
            {stats ? stats.resolved_incidents : "-"}
          </div>
          <div className="text-[11px] text-emerald-500/70 mt-1 font-mono">Closed & Verified</div>
        </div>

        <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 flex flex-col justify-between">
          <div>
            <div className="text-[11px] font-mono uppercase tracking-wider text-slate-400">
              Deterministic Detection
            </div>
            <div className="text-xs text-slate-300 font-mono mt-1">6 Active Rules</div>
          </div>
          <button
            onClick={handleRunEvaluation}
            disabled={evaluating}
            className="w-full mt-2 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-xs font-mono font-medium transition-colors flex items-center justify-center gap-1.5"
          >
            {evaluating ? (
              <>
                <span className="h-2 w-2 rounded-full bg-white animate-ping" />
                Evaluating...
              </>
            ) : (
              <>
                <span>&#9654;</span> Evaluate Now
              </>
            )}
          </button>
        </div>
      </div>

      {/* Detection Evaluation Feedback Banner */}
      {evalResult && (
        <div className="bg-indigo-950/50 border border-indigo-800/80 rounded-xl p-3 px-4 flex items-center justify-between text-xs font-mono text-indigo-200">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-indigo-400" />
            <span>
              Evaluation cycle finished in <strong>{evalResult.evaluation_duration_ms}ms</strong>:
              Evaluated {evalResult.rules_evaluated} rules &rarr; {evalResult.breaches_detected} breaches,{" "}
              {evalResult.incidents_created} created, {evalResult.incidents_updated} updated,{" "}
              {evalResult.incidents_resolved} auto-resolved.
            </span>
          </div>
          <button
            onClick={() => setEvalResult(null)}
            className="text-slate-400 hover:text-white ml-4"
          >
            &times;
          </button>
        </div>
      )}

      {/* Filter and Query Toolbar */}
      <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-3 flex flex-wrap items-center justify-between gap-3 text-xs font-mono">
        {/* Status Filter Tabs */}
        <div className="flex items-center bg-slate-950 p-1 rounded-lg border border-slate-800">
          {[
            { label: "Active", value: "active" },
            { label: "All", value: "ALL" },
            { label: "Open", value: "OPEN" },
            { label: "Acknowledged", value: "ACKNOWLEDGED" },
            { label: "Investigating", value: "INVESTIGATING" },
            { label: "Resolved", value: "RESOLVED" },
          ].map((tab) => (
            <button
              key={tab.value}
              onClick={() => setStatusFilter(tab.value)}
              className={`px-3 py-1 rounded-md transition-colors ${
                statusFilter === tab.value
                  ? "bg-slate-800 text-white font-semibold"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Severity and Service Selects */}
        <div className="flex items-center gap-2">
          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            className="bg-slate-950 border border-slate-800 text-slate-300 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-indigo-500"
          >
            <option value="ALL">All Severities</option>
            <option value="CRITICAL">Critical</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>

          <select
            value={serviceFilter}
            onChange={(e) => setServiceFilter(e.target.value)}
            className="bg-slate-950 border border-slate-800 text-slate-300 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-indigo-500"
          >
            <option value="ALL">All Services</option>
            <option value="api-gateway">api-gateway</option>
            <option value="order-service">order-service</option>
            <option value="payment-service">payment-service</option>
          </select>

          <button
            onClick={() => fetchIncidents()}
            className="p-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition-colors"
            title="Refresh list"
          >
            &#x21bb;
          </button>
        </div>
      </div>

      {/* Incidents Table */}
      <div className="bg-slate-900/40 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        {loading ? (
          <div className="py-16 text-center text-slate-500 font-mono text-xs">
            Loading active incident records...
          </div>
        ) : error ? (
          <div className="py-12 text-center text-rose-400 font-mono text-xs">
            {error}
          </div>
        ) : incidents.length === 0 ? (
          <div className="py-16 text-center space-y-2">
            <div className="h-8 w-8 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center justify-center mx-auto text-sm">
              &#10003;
            </div>
            <div className="text-white font-medium text-sm">All Monitored Services Healthy</div>
            <div className="text-slate-400 text-xs font-mono max-w-sm mx-auto">
              No matching incidents found for the active filter. Trigger fault injection to test autonomous threshold detection.
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead className="bg-slate-950/80 border-b border-slate-800 text-slate-400 uppercase text-[10px] tracking-wider">
                <tr>
                  <th className="py-3 px-4">Severity</th>
                  <th className="py-3 px-4">Title & Incident Type</th>
                  <th className="py-3 px-4">Service</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Observed / Threshold</th>
                  <th className="py-3 px-4 text-center">Count</th>
                  <th className="py-3 px-4">Last Seen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {incidents.map((inc) => (
                  <tr
                    key={inc.id}
                    onClick={() => {
                      setSelectedId(inc.id);
                      setDetail(null);
                      setDetailLoading(true);
                    }}
                    className="hover:bg-slate-800/40 cursor-pointer transition-colors"
                  >
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded border text-[10px] font-semibold ${getSeverityBadge(
                          inc.severity
                        )}`}
                      >
                        {inc.severity}
                      </span>
                    </td>
                    <td className="py-3.5 px-4">
                      <div className="font-semibold text-slate-100">{inc.title}</div>
                      <div className="text-[11px] text-slate-500 font-mono">{inc.incident_type}</div>
                    </td>
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      <span className="bg-slate-800 text-slate-300 px-2 py-0.5 rounded border border-slate-700/60 text-[11px]">
                        {inc.service_name}
                      </span>
                    </td>
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded border text-[10px] font-medium ${getStatusBadge(
                          inc.status
                        )}`}
                      >
                        {inc.status}
                      </span>
                    </td>
                    <td className="py-3.5 px-4 whitespace-nowrap text-slate-300">
                      {inc.triggered_value !== null ? (
                        <span>
                          <strong className="text-white">{inc.triggered_value}</strong> /{" "}
                          <span className="text-slate-400">{inc.threshold_value}</span>
                        </span>
                      ) : (
                        <span className="text-slate-500">&mdash;</span>
                      )}
                    </td>
                    <td className="py-3.5 px-4 whitespace-nowrap text-center">
                      <span className="bg-slate-800/80 text-slate-300 px-1.5 py-0.5 rounded text-[10px]">
                        &times;{inc.occurrence_count}
                      </span>
                    </td>
                    <td className="py-3.5 px-4 whitespace-nowrap text-slate-400 text-[11px]">
                      {formatTimestamp(inc.last_seen_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Slide-out Incident Detail Drawer */}
      {selectedId && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="w-full max-w-xl bg-slate-950 border-l border-slate-800 h-full overflow-y-auto p-6 space-y-6 flex flex-col shadow-2xl">
            {/* Drawer Header */}
            <div className="flex items-start justify-between border-b border-slate-800 pb-4">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded border text-[10px] font-bold ${
                      detail ? getSeverityBadge(detail.severity) : ""
                    }`}
                  >
                    {detail?.severity}
                  </span>
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded border text-[10px] font-medium ${
                      detail ? getStatusBadge(detail.status) : ""
                    }`}
                  >
                    {detail?.status}
                  </span>
                  <span className="bg-slate-800 text-slate-300 px-2 py-0.5 rounded border border-slate-700/60 text-[10px] font-mono">
                    {detail?.service_name}
                  </span>
                </div>
                <h3 className="text-lg font-bold text-white tracking-tight mt-1">
                  {detail?.title}
                </h3>
              </div>
              <button
                onClick={() => setSelectedId(null)}
                className="text-slate-400 hover:text-white text-xl p-1 font-mono"
              >
                &times;
              </button>
            </div>

            {detailLoading || !detail ? (
              <div className="py-20 text-center text-slate-500 font-mono text-xs">
                Loading incident details...
              </div>
            ) : (
              <div className="space-y-6 flex-1 text-xs">
                {/* Operator Lifecycle Controls */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
                  <div className="text-[11px] font-mono uppercase tracking-wider text-slate-400">
                    Incident Response Actions
                  </div>
                  <div className="flex items-center gap-2">
                    {detail.status === "OPEN" && (
                      <>
                        <button
                          onClick={() => handleAction("acknowledge")}
                          disabled={actionLoading}
                          className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white font-mono rounded-lg transition-colors"
                        >
                          Acknowledge
                        </button>
                        <button
                          onClick={() => handleAction("investigate")}
                          disabled={actionLoading}
                          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white font-mono rounded-lg transition-colors"
                        >
                          Start Investigation
                        </button>
                      </>
                    )}

                    {detail.status === "ACKNOWLEDGED" && (
                      <>
                        <button
                          onClick={() => handleAction("investigate")}
                          disabled={actionLoading}
                          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white font-mono rounded-lg transition-colors"
                        >
                          Start Investigation
                        </button>
                        <button
                          onClick={() => handleAction("resolve")}
                          disabled={actionLoading}
                          className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white font-mono rounded-lg transition-colors"
                        >
                          Resolve Incident
                        </button>
                      </>
                    )}

                    {detail.status === "INVESTIGATING" && (
                      <button
                        onClick={() => handleAction("resolve")}
                        disabled={actionLoading}
                        className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white font-mono rounded-lg transition-colors"
                      >
                        Resolve Incident
                      </button>
                    )}

                    {detail.status === "RESOLVED" && (
                      <div className="text-emerald-400 font-mono flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-full bg-emerald-500" />
                        Incident has been verified and resolved
                      </div>
                    )}
                  </div>
                </div>

                {/* Structured Evidence Section */}
                <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-3 font-mono">
                  <div className="text-[11px] uppercase tracking-wider text-slate-400">
                    Deterministic Evidence & Telemetry
                  </div>

                  <div className="grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <span className="text-slate-500">Observed Value:</span>{" "}
                      <span className="text-white font-bold">{detail.triggered_value ?? "N/A"}</span>
                    </div>
                    <div>
                      <span className="text-slate-500">Threshold:</span>{" "}
                      <span className="text-slate-300 font-bold">{detail.threshold_value ?? "N/A"}</span>
                    </div>
                    <div>
                      <span className="text-slate-500">Occurrences:</span>{" "}
                      <span className="text-white">&times;{detail.occurrence_count}</span>
                    </div>
                    <div>
                      <span className="text-slate-500">First Triggered:</span>{" "}
                      <span className="text-slate-300">{formatTimestamp(detail.triggered_at)}</span>
                    </div>
                  </div>

                  {detail.evidence && (
                    <div className="mt-3">
                      <div className="text-[10px] uppercase text-slate-500 mb-1">Raw Evidence Payload</div>
                      <pre className="bg-slate-950 p-3 rounded-lg border border-slate-800 text-[11px] text-slate-300 overflow-x-auto">
                        {JSON.stringify(detail.evidence, null, 2)}
                      </pre>
                    </div>
                  )}

                  {/* Telemetry Deep Links */}
                  <div className="pt-2 border-t border-slate-800/80 flex items-center gap-3 text-[11px]">
                    <a
                      href="http://localhost:3001"
                      target="_blank"
                      rel="noreferrer"
                      className="text-indigo-400 hover:text-indigo-300 underline"
                    >
                      Grafana Dashboard &rarr;
                    </a>
                    <span className="text-slate-700">|</span>
                    <a
                      href="http://localhost:9090"
                      target="_blank"
                      rel="noreferrer"
                      className="text-amber-400 hover:text-amber-300 underline"
                    >
                      Prometheus &rarr;
                    </a>
                    <span className="text-slate-700">|</span>
                    <a
                      href="http://localhost:3200"
                      target="_blank"
                      rel="noreferrer"
                      className="text-emerald-400 hover:text-emerald-300 underline"
                    >
                      Tempo Traces &rarr;
                    </a>
                  </div>
                </div>

                {/* Audit Trail Timeline */}
                <div className="space-y-3 font-mono">
                  <div className="text-[11px] uppercase tracking-wider text-slate-400">
                    Chronological Audit Trail ({detail.events.length})
                  </div>

                  <div className="space-y-2 border-l border-slate-800 ml-2 pl-4">
                    {detail.events.map((evt) => (
                      <div key={evt.id} className="relative space-y-1">
                        <div className="absolute -left-[21px] top-1 h-2 w-2 rounded-full bg-slate-600 border border-slate-950" />
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-bold text-slate-400">
                            {evt.event_type}
                          </span>
                          <span className="text-[10px] text-slate-600">&bull;</span>
                          <span className="text-[10px] text-slate-500">
                            {formatTimestamp(evt.created_at)}
                          </span>
                          <span className="text-[10px] text-indigo-400">by {evt.actor}</span>
                        </div>
                        <div className="text-slate-300 text-xs">{evt.message}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
