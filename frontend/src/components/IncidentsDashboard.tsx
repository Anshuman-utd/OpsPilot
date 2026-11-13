"use client";

import { useEffect, useState, useCallback } from "react";
import { useIdentity } from "./Workspace";
import { RemediationDrawerSection } from "./RemediationDrawerSection";

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

// Phase 4 Investigation Types
export interface InvestigationAgentRun {
  id: string;
  investigation_id: string;
  agent_name: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "SKIPPED";
  duration_ms: number | null;
  findings?: Record<string, unknown>;
  error_message?: string | null;
  created_at: string;
}

export interface Hypothesis {
  hypothesis_id: string;
  title: string;
  explanation: string;
  confidence: "LOW" | "MEDIUM" | "HIGH";
  confidence_score: number;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids?: string[];
  affected_components: string[];
  assumptions?: string[];
  missing_information?: string[];
  is_contested?: boolean;
  validation_notes?: string[];
}

export interface EvidenceReference {
  evidence_id: string;
  source_type: "METRIC" | "LOG" | "TRACE" | "INCIDENT_HISTORY";
  source_reference: string;
  service_name: string;
  timestamp?: string | null;
  observation: string;
  observed_value?: number | string | null;
  relevant_threshold?: number | string | null;
  relevance: string;
  evidence_strength: string;
  supporting_data?: Record<string, unknown>;
  limitations?: string | null;
}

export interface FinalRCAReport {
  executive_summary: string;
  incident_description: string;
  timeline?: Array<{ time: string; event: string }>;
  observed_facts: string[];
  hypotheses: Hypothesis[];
  confidence_level: "LOW" | "MEDIUM" | "HIGH";
  affected_services: string[];
  contributing_factors?: string[];
  recommended_next_steps?: string[];
  evidence_sources?: EvidenceReference[];
  investigation_limitations?: string[];
  validated_at?: string | null;
}

export interface Investigation {
  id: string;
  incident_id: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  trigger_type: "MANUAL" | "AUTOMATIC";
  model_provider?: string;
  model_name?: string;
  started_at?: string | null;
  completed_at?: string | null;
  total_duration_ms?: number | null;
  summary?: string | null;
  error_message?: string | null;
  final_report?: FinalRCAReport | null;
  agent_runs?: InvestigationAgentRun[];
  created_at: string;
  updated_at: string;
}

const AGENT_DISPLAY_NAMES: Record<string, string> = {
  incident_analyzer: "Incident Analyzer",
  metrics_agent: "Metrics Investigator",
  log_agent: "Log Investigator",
  trace_agent: "Trace Investigator",
  historical_agent: "Historical Context",
  aggregator: "Evidence Aggregator",
  rca_agent: "RCA Synthesizer",
  validator_agent: "Evidence Validator",
};

export default function IncidentsDashboard() {
  const canOperate = useIdentity().role === "operator";
  const [actionError, setActionError] = useState("");
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

  // Autonomous Investigation States
  const [investigations, setInvestigations] = useState<Investigation[]>([]);
  const [selectedInvestigation, setSelectedInvestigation] = useState<Investigation | null>(null);
  const [startingInvestigation, setStartingInvestigation] = useState<boolean>(false);
  const [expandedEvidenceId, setExpandedEvidenceId] = useState<string | null>(null);

  // Detection Run
  const [evaluating, setEvaluating] = useState<boolean>(false);
  const [evalResult, setEvalResult] = useState<DetectionRunResult | null>(null);

  const apiUrl = "";

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

  const fetchInvestigationDetail = useCallback(async (invId: string) => {
    try {
      const res = await fetch(`${apiUrl}/api/v1/investigations/${invId}`, { cache: "no-store" });
      if (res.ok) {
        const fullInv: Investigation = await res.json();
        setSelectedInvestigation(fullInv);
      }
    } catch {
      // ignore
    }
  }, [apiUrl]);

  // Load selected incident details and investigation history
  useEffect(() => {
    let isMounted = true;
    if (selectedId) {
      const loadDrawerData = async () => {
        try {
          const [detailRes, invRes] = await Promise.all([
            fetch(`${apiUrl}/api/v1/incidents/${selectedId}`, { cache: "no-store" }),
            fetch(`${apiUrl}/api/v1/incidents/${selectedId}/investigations`, { cache: "no-store" }),
          ]);

          if (isMounted) {
            if (detailRes.ok) {
              const d = await detailRes.json();
              setDetail(d);
            }
            if (invRes.ok) {
              const invs: Investigation[] = await invRes.json();
              setInvestigations(invs);
              if (invs.length > 0) {
                const fullRes = await fetch(`${apiUrl}/api/v1/investigations/${invs[0].id}`, { cache: "no-store" });
                if (fullRes.ok && isMounted) {
                  const fullInv: Investigation = await fullRes.json();
                  setSelectedInvestigation(fullInv);
                }
              } else {
                setSelectedInvestigation(null);
              }
            }
          }
        } catch {
          // ignore
        } finally {
          if (isMounted) setDetailLoading(false);
        }
      };
      void loadDrawerData();
    }
    return () => {
      isMounted = false;
    };
  }, [selectedId, apiUrl]);

  // Polling for active investigation progress
  useEffect(() => {
    let isMounted = true;
    if (
      selectedInvestigation &&
      (selectedInvestigation.status === "RUNNING" || selectedInvestigation.status === "PENDING")
    ) {
      const pollTimer = setInterval(async () => {
        try {
          const res = await fetch(`${apiUrl}/api/v1/investigations/${selectedInvestigation.id}`, {
            cache: "no-store",
          });
          if (res.ok && isMounted) {
            const updated: Investigation = await res.json();
            setSelectedInvestigation(updated);
            if (updated.status === "COMPLETED" || updated.status === "FAILED") {
              clearInterval(pollTimer);
              void fetchIncidents();
              if (selectedId) {
                const listRes = await fetch(`${apiUrl}/api/v1/incidents/${selectedId}/investigations`, { cache: "no-store" });
                if (listRes.ok && isMounted) {
                  const invs: Investigation[] = await listRes.json();
                  setInvestigations(invs);
                }
              }
            }
          }
        } catch {
          // ignore
        }
      }, 2500);

      return () => {
        isMounted = false;
        clearInterval(pollTimer);
      };
    }
  }, [selectedInvestigation, apiUrl, selectedId, fetchIncidents]);

  // Trigger autonomous investigation
  const handleStartInvestigation = async () => {
    if (!selectedId) return;
    setStartingInvestigation(true);
    setActionError("");
    try {
      const res = await fetch(`${apiUrl}/api/v1/incidents/${selectedId}/investigations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trigger_type: "MANUAL" }),
      });
      if (res.ok) {
        const created: Investigation = await res.json();
        setInvestigations((prev) => [created, ...prev]);
        setSelectedInvestigation(created);
        void fetchIncidents();
      } else {
        const errData = await res.json().catch(() => ({}));
        setActionError(typeof errData.detail === "string" ? errData.detail : "Could not start the investigation.");
      }
    } catch {
      setActionError("Could not reach the workspace.");
    } finally {
      setStartingInvestigation(false);
    }
  };

  // Retry failed investigation
  const handleRetryInvestigation = async () => {
    if (!selectedInvestigation) return;
    setStartingInvestigation(true);
    setActionError("");
    try {
      const res = await fetch(`${apiUrl}/api/v1/investigations/${selectedInvestigation.id}/retry`, {
        method: "POST",
      });
      if (res.ok) {
        const retried: Investigation = await res.json();
        setSelectedInvestigation(retried);
      }
    } catch {
      setActionError("Could not retry the investigation.");
    } finally {
      setStartingInvestigation(false);
    }
  };

  // Lifecycle action handler
  const handleAction = async (action: "acknowledge" | "investigate" | "resolve") => {
    if (!selectedId) return;
    setActionLoading(true);
    setActionError("");
    try {
      const res = await fetch(`${apiUrl}/api/v1/incidents/${selectedId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (res.ok) {
        const updated = await res.json();
        setDetail(updated);
        await fetchIncidents();
      } else {
        const result = await res.json().catch(() => null);
        setActionError(typeof result?.detail === "string" ? result.detail : "Could not update the incident.");
      }
    } catch {
      setActionError("Could not reach the workspace.");
    } finally {
      setActionLoading(false);
    }
  };

  // Trigger immediate detection run
  const handleRunEvaluation = async () => {
    setEvaluating(true);
    setActionError("");
    try {
      const res = await fetch(`${apiUrl}/api/v1/incidents/evaluate`, {
        method: "POST",
      });
      if (res.ok) {
        const data = await res.json();
        setEvalResult(data);
        await fetchIncidents();
      } else {
        const result = await res.json().catch(() => null);
        setActionError(typeof result?.detail === "string" ? result.detail : "Detection failed.");
      }
    } catch {
      setActionError("Could not contact the detection service.");
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

  const getConfidenceBadge = (conf: string) => {
    switch (conf) {
      case "HIGH":
        return "bg-emerald-500/20 text-emerald-300 border-emerald-500/40";
      case "MEDIUM":
        return "bg-amber-500/20 text-amber-300 border-amber-500/40";
      default:
        return "bg-slate-500/20 text-slate-400 border-slate-500/40";
    }
  };

  const formatTimestamp = (isoString?: string | null) => {
    if (!isoString) return "-";
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return isoString;
    }
  };

  return (
    <div className="space-y-6">
      <div className="incident-metrics">
        {[
          ["Active", stats && stats.open_incidents + stats.acknowledged_incidents + stats.investigating_incidents, "active"],
          ["Critical", stats?.critical_incidents, "critical"],
          ["Investigating", stats?.investigating_incidents, "investigating"],
          ["Resolved", stats?.resolved_incidents, "resolved"],
        ].map(([label, value, color]) => (
          <div className="incident-metric" key={label}>
            <span><i className={String(color)} />{label}</span>
            <strong>{value ?? "—"}</strong>
          </div>
        ))}
      </div>
      <div className="incident-list-heading">
        <h2>Incident queue</h2>
        {canOperate && (
          <button className="primary-button" onClick={handleRunEvaluation} disabled={evaluating}>
            {evaluating ? "Checking…" : "Run detection"}
          </button>
        )}
      </div>
      {!selectedId && actionError && <p className="form-error" role="alert">{actionError}</p>}

      {/* Detection Result Banner if Triggered */}
      {evalResult && (
        <div className="bg-slate-900 border border-slate-700/80 rounded-xl p-4 text-xs font-mono text-slate-300 flex items-center justify-between animate-in fade-in">
          {evalResult.status === "queued" ? (
            <span role="status">Detection queued. The incident list refreshes automatically.</span>
          ) : <div className="flex flex-wrap items-center gap-4">
            <span className="text-emerald-400 font-bold">Detection complete</span>
            <span>Rules: {evalResult.rules_evaluated}</span>
            <span>Breaches: {evalResult.breaches_detected}</span>
            <span className="text-rose-400 font-semibold">Created: {evalResult.incidents_created}</span>
            <span className="text-amber-400 font-semibold">Updated: {evalResult.incidents_updated}</span>
            <span className="text-emerald-400 font-semibold">Resolved: {evalResult.incidents_resolved}</span>
            <span className="text-slate-500">Duration: {evalResult.evaluation_duration_ms}ms</span>
          </div>}
          <button
            aria-label="Dismiss detection result"
            onClick={() => setEvalResult(null)}
            className="text-slate-500 hover:text-slate-300 text-sm font-bold"
          >
            &times;
          </button>
        </div>
      )}

      {/* Incidents Filter Controls */}
      <div className="flex flex-wrap items-center gap-3 text-xs">

        <select
          aria-label="Incident status"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-slate-900 border border-slate-800 text-slate-200 rounded-lg px-2.5 py-1 text-xs focus:outline-none focus:border-indigo-500"
        >
          <option value="active">Active incidents</option>
          <option value="ALL">All statuses</option>
          <option value="OPEN">Status: OPEN</option>
          <option value="ACKNOWLEDGED">Status: ACKNOWLEDGED</option>
          <option value="INVESTIGATING">Status: INVESTIGATING</option>
          <option value="RESOLVED">Status: RESOLVED</option>
        </select>

        <select
          aria-label="Severity"
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          className="bg-slate-900 border border-slate-800 text-slate-200 rounded-lg px-2.5 py-1 text-xs focus:outline-none focus:border-indigo-500"
        >
          <option value="ALL">All severities</option>
          <option value="CRITICAL">Severity: CRITICAL</option>
          <option value="HIGH">Severity: HIGH</option>
          <option value="MEDIUM">Severity: MEDIUM</option>
          <option value="LOW">Severity: LOW</option>
        </select>

        <select
          aria-label="Service"
          value={serviceFilter}
          onChange={(e) => setServiceFilter(e.target.value)}
          className="bg-slate-900 border border-slate-800 text-slate-200 rounded-lg px-2.5 py-1 text-xs focus:outline-none focus:border-indigo-500"
        >
          <option value="ALL">All services</option>
          <option value="api-gateway">api-gateway</option>
          <option value="order-service">order-service</option>
          <option value="payment-service">payment-service</option>
        </select>

        <button
          onClick={() => {
            setStatusFilter("active");
            setSeverityFilter("ALL");
            setServiceFilter("ALL");
          }}
          className="text-slate-400 hover:text-slate-200 underline text-[11px]"
        >
          Reset filters
        </button>
      </div>

      {/* Incidents Table */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        {loading ? (
          <div className="py-20 text-center text-slate-500 font-mono text-xs">
            Loading active SRE incidents...
          </div>
        ) : error ? (
          <div className="py-12 text-center text-rose-400 font-mono text-xs">{error}</div>
        ) : incidents.length === 0 ? (
          <div className="py-16 text-center text-slate-500 font-mono space-y-2">
            <div className="text-2xl">&#x2713;</div>
            <div className="text-slate-300 font-semibold">No matching incidents</div>
            <div className="text-xs text-slate-500">
              Try changing the filters to see other incidents.
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
                      <button type="button" className="incident-link" onClick={(event) => {
                        event.stopPropagation();
                        setSelectedId(inc.id);
                        setDetail(null);
                        setDetailLoading(true);
                      }}>{inc.title}</button>
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

      {/* Slide-out Incident Detail & Autonomous Investigation Drawer */}
      {selectedId && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
          <div role="dialog" aria-modal="true" aria-label="Incident details" className="incident-drawer w-full max-w-2xl bg-slate-950 border-l border-slate-800 h-full overflow-y-auto p-6 space-y-6 flex flex-col shadow-2xl">
            {actionError && <p className="form-error" role="alert">{actionError}</p>}
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
                onClick={() => { setSelectedId(null); setActionError(""); }}
                aria-label="Close incident details"
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
              <div className="space-y-6 flex-1 text-xs font-mono">
                {/* Operator Lifecycle Controls */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
                  <div className="text-[11px] font-mono uppercase tracking-wider text-slate-400 flex items-center justify-between">
                    <span>Incident Response Actions</span>
                    <span className="text-[10px] text-slate-500">ID: {detail.id.slice(0, 8)}...</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {detail.status === "OPEN" && (
                      <>
                        <button
                          onClick={() => handleAction("acknowledge")}
                          hidden={!canOperate}
                          disabled={actionLoading}
                          className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white font-mono rounded-lg transition-colors"
                        >
                          Acknowledge
                        </button>
                        <button
                          onClick={() => handleAction("investigate")}
                          hidden={!canOperate}
                          disabled={actionLoading}
                          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white font-mono rounded-lg transition-colors"
                        >
                          Mark Investigating
                        </button>
                      </>
                    )}

                    {detail.status === "ACKNOWLEDGED" && (
                      <>
                        <button
                          onClick={() => handleAction("investigate")}
                          hidden={!canOperate}
                          disabled={actionLoading}
                          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white font-mono rounded-lg transition-colors"
                        >
                          Mark Investigating
                        </button>
                        <button
                          onClick={() => handleAction("resolve")}
                          hidden={!canOperate}
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
                        hidden={!canOperate}
                          disabled={actionLoading}
                        className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white font-mono rounded-lg transition-colors"
                      >
                        Resolve Incident
                      </button>
                    )}

                    {detail.status === "RESOLVED" && (
                      <div className="text-emerald-400 font-mono flex items-center gap-1.5">
                        <span className="h-2 w-2 rounded-full bg-emerald-500" />
                        Incident resolved
                      </div>
                    )}
                  </div>
                </div>

                {/* ========================================================================= */}
                {/* PHASE 4: AUTONOMOUS MULTI-AGENT RCA INVESTIGATION (LANGGRAPH) */}
                {/* ========================================================================= */}
                <div className="bg-gradient-to-b from-indigo-950/40 to-slate-900/60 border border-indigo-900/50 rounded-xl p-4 space-y-4 shadow-md">
                  <div className="flex items-center justify-between border-b border-indigo-900/40 pb-3">
                    <div className="space-y-0.5">
                      <div className="text-[11px] uppercase tracking-wider text-indigo-300 font-bold flex items-center gap-1.5">
                        <span>Investigation</span>
                      </div>
                      <div className="text-[11px] text-slate-400">
                        Metrics, logs, traces, and incident history.
                      </div>
                    </div>

                    <button
                      onClick={handleStartInvestigation}
                      hidden={!canOperate}
                      disabled={startingInvestigation || selectedInvestigation?.status === "RUNNING" || selectedInvestigation?.status === "PENDING"}
                      className="px-3.5 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-mono font-medium rounded-lg transition-colors shadow-sm flex items-center gap-1.5"
                    >
                      {startingInvestigation ? (
                        <>
                          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-solid border-white border-r-transparent" />
                          <span>Dispatching...</span>
                        </>
                      ) : (
                        <>
                          <span>&#x25B6;</span>
                          <span>{selectedInvestigation ? "Run again" : "Start investigation"}</span>
                        </>
                      )}
                    </button>
                  </div>

                  {/* Past Runs Selector */}
                  {investigations.length > 1 && (
                    <div className="flex items-center gap-2 text-[11px]">
                      <span className="text-slate-400">Past Runs:</span>
                      <div className="flex items-center gap-1 overflow-x-auto">
                        {investigations.map((inv, idx) => (
                          <button
                            key={inv.id}
                            onClick={() => void fetchInvestigationDetail(inv.id)}
                            className={`px-2 py-0.5 rounded border text-[10px] font-mono transition-colors ${
                              selectedInvestigation?.id === inv.id
                                ? "bg-indigo-600 text-white border-indigo-500"
                                : "bg-slate-900 text-slate-400 border-slate-800 hover:border-slate-700"
                            }`}
                          >
                            Run #{investigations.length - idx} ({inv.status})
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {!selectedInvestigation ? (
                    <div className="py-6 text-center text-slate-500 font-mono text-xs">
                      No investigation has been executed for this incident yet. Click &quot;Start investigation&quot; to begin.
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {/* Investigation Status Bar */}
                      <div className="bg-slate-950/80 border border-slate-800 rounded-lg p-3 flex flex-wrap items-center justify-between gap-3 text-xs">
                        <div className="flex items-center gap-2">
                          <span className="text-slate-400">Status:</span>
                          <span
                            className={`px-2 py-0.5 rounded border text-[10px] font-bold ${
                              selectedInvestigation.status === "COMPLETED"
                                ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                                : selectedInvestigation.status === "RUNNING"
                                ? "bg-sky-500/10 text-sky-400 border-sky-500/30 animate-pulse"
                                : selectedInvestigation.status === "FAILED"
                                ? "bg-rose-500/10 text-rose-400 border-rose-500/30"
                                : "bg-amber-500/10 text-amber-400 border-amber-500/30"
                            }`}
                          >
                            {selectedInvestigation.status}
                          </span>
                          {selectedInvestigation.model_name && (
                            <span className="text-slate-500 text-[11px]">
                              Model: <span className="text-slate-300">{selectedInvestigation.model_name}</span>
                            </span>
                          )}
                        </div>

                        <div className="flex items-center gap-3 text-slate-400 text-[11px]">
                          {selectedInvestigation.total_duration_ms && (
                            <span>
                              Duration:{" "}
                              <strong className="text-slate-200">
                                {(selectedInvestigation.total_duration_ms / 1000).toFixed(1)}s
                              </strong>
                            </span>
                          )}
                          <span>Started: {formatTimestamp(selectedInvestigation.started_at)}</span>
                        </div>
                      </div>

                      {/* Specialized Agent Execution Swarm Pipeline */}
                      <div className="space-y-2">
                        <div className="text-[10px] uppercase tracking-wider text-slate-400 font-bold">
                          Multi-Agent Swarm Pipeline
                        </div>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                          {[
                            "incident_analyzer",
                            "metrics_agent",
                            "log_agent",
                            "trace_agent",
                            "historical_agent",
                            "aggregator",
                            "rca_agent",
                            "validator_agent",
                          ].map((agKey) => {
                            const run = selectedInvestigation.agent_runs?.find((r) => r.agent_name === agKey);
                            const agStatus = run?.status || (selectedInvestigation.status === "COMPLETED" ? "COMPLETED" : "PENDING");
                            return (
                              <div
                                key={agKey}
                                className="bg-slate-900 border border-slate-800 rounded-lg p-2.5 space-y-1 text-[11px]"
                              >
                                <div className="text-slate-300 font-semibold truncate">
                                  {AGENT_DISPLAY_NAMES[agKey]}
                                </div>
                                <div className="flex items-center justify-between">
                                  <span
                                    className={`px-1.5 py-0.2 rounded text-[9px] font-bold ${
                                      agStatus === "COMPLETED"
                                        ? "text-emerald-400 bg-emerald-950/60"
                                        : agStatus === "RUNNING"
                                        ? "text-sky-400 bg-sky-950/60 animate-pulse"
                                        : agStatus === "FAILED"
                                        ? "text-rose-400 bg-rose-950/60"
                                        : "text-slate-500 bg-slate-950"
                                    }`}
                                  >
                                    {agStatus === "COMPLETED" ? "\u2713 DONE" : agStatus}
                                  </span>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>

                      {/* Error Banner if Failed */}
                      {selectedInvestigation.status === "FAILED" && (
                        <div className="bg-rose-950/40 border border-rose-900/60 rounded-xl p-3 text-xs text-rose-300 space-y-2">
                          <div className="font-bold flex items-center justify-between">
                            <span>Investigation Failed</span>
                            <button
                              onClick={handleRetryInvestigation}
                              hidden={!canOperate}
                              className="px-2 py-0.5 bg-rose-800 hover:bg-rose-700 text-white rounded text-[10px]"
                            >
                              Retry
                            </button>
                          </div>
                          <div className="text-[11px] text-rose-400 font-mono">
                            {selectedInvestigation.error_message || "An unhandled error occurred during execution."}
                          </div>
                        </div>
                      )}

                      {/* RCA Report Output Display */}
                      {selectedInvestigation.final_report && (
                        <div className="space-y-4 pt-2">
                          {/* Executive Summary Card */}
                          <div className="bg-slate-900 border border-indigo-900/40 rounded-xl p-4 space-y-2.5">
                            <div className="flex items-center justify-between">
                              <span className="text-[11px] uppercase tracking-wider text-indigo-300 font-bold">
                                Executive Summary
                              </span>
                              <span
                                className={`px-2 py-0.5 rounded border text-[10px] font-bold ${getConfidenceBadge(
                                  selectedInvestigation.final_report.confidence_level
                                )}`}
                              >
                                Confidence: {selectedInvestigation.final_report.confidence_level}
                              </span>
                            </div>
                            <p className="text-slate-200 text-xs leading-relaxed font-sans">
                              {selectedInvestigation.final_report.executive_summary}
                            </p>
                          </div>

                          {/* Observed Empirical Facts */}
                          {selectedInvestigation.final_report.observed_facts?.length > 0 && (
                            <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 space-y-2">
                              <div className="text-[11px] uppercase tracking-wider text-slate-400 font-bold">
                                Empirical Observed Facts ({selectedInvestigation.final_report.observed_facts.length})
                              </div>
                              <ul className="space-y-1.5 list-none pl-0">
                                {selectedInvestigation.final_report.observed_facts.map((fact, idx) => (
                                  <li key={idx} className="text-slate-300 text-[11px] flex items-start gap-2">
                                    <span className="text-emerald-400 font-bold mt-0.5">&#x2022;</span>
                                    <span>{fact}</span>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}

                          {/* Root Cause Hypotheses */}
                          {selectedInvestigation.final_report.hypotheses?.length > 0 && (
                            <div className="space-y-3">
                              <div className="text-[11px] uppercase tracking-wider text-slate-400 font-bold">
                                Formulated Root-Cause Hypotheses ({selectedInvestigation.final_report.hypotheses.length})
                              </div>

                              <div className="space-y-3">
                                {selectedInvestigation.final_report.hypotheses.map((hyp) => (
                                  <div
                                    key={hyp.hypothesis_id}
                                    className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-2.5"
                                  >
                                    <div className="flex items-start justify-between gap-2">
                                      <div className="space-y-1">
                                        <div className="text-[10px] text-indigo-400 font-bold">
                                          {hyp.hypothesis_id}
                                        </div>
                                        <div className="text-sm font-bold text-white">{hyp.title}</div>
                                      </div>
                                      <div className="flex items-center gap-1.5 flex-wrap justify-end">
                                        {hyp.is_contested && (
                                          <span className="px-1.5 py-0.5 rounded border border-amber-500/40 bg-amber-500/10 text-amber-300 text-[10px] font-bold">
                                            Contested
                                          </span>
                                        )}
                                        <span
                                          className={`px-2 py-0.5 rounded border text-[10px] font-bold whitespace-nowrap ${getConfidenceBadge(
                                            hyp.confidence
                                          )}`}
                                        >
                                          {hyp.confidence} (Score: {hyp.confidence_score.toFixed(2)})
                                        </span>
                                      </div>
                                    </div>

                                    <p className="text-slate-300 text-xs font-sans leading-relaxed">
                                      {hyp.explanation}
                                    </p>

                                    {/* Validation Notes if any */}
                                    {hyp.validation_notes && hyp.validation_notes.length > 0 && (
                                      <div className="bg-slate-950/60 border border-slate-800/80 rounded-lg p-2.5 space-y-1">
                                        <div className="text-[10px] font-bold text-slate-400">Validation Checks:</div>
                                        <ul className="text-[10px] text-slate-400 space-y-0.5 pl-3 list-disc">
                                          {hyp.validation_notes.map((note, nIdx) => (
                                            <li key={nIdx}>{note}</li>
                                          ))}
                                        </ul>
                                      </div>
                                    )}

                                    {/* Supporting Evidence Chips */}
                                    {hyp.supporting_evidence_ids?.length > 0 && (
                                      <div className="flex flex-wrap items-center gap-1.5 pt-1">
                                        <span className="text-slate-500 text-[10px]">Cites:</span>
                                        {hyp.supporting_evidence_ids.map((evId) => (
                                          <button
                                            key={evId}
                                            onClick={() =>
                                              setExpandedEvidenceId(expandedEvidenceId === evId ? null : evId)
                                            }
                                            className="px-1.5 py-0.5 rounded bg-indigo-950/70 border border-indigo-800/60 text-indigo-300 text-[10px] hover:border-indigo-500 font-mono"
                                          >
                                            {evId}
                                          </button>
                                        ))}
                                      </div>
                                    )}

                                    {/* Affected Components & Assumptions */}
                                    {hyp.affected_components?.length > 0 && (
                                      <div className="text-[10px] text-slate-400 flex items-center gap-1.5 pt-1">
                                        <span className="text-slate-500">Affected:</span>
                                        {hyp.affected_components.map((comp) => (
                                          <span
                                            key={comp}
                                            className="bg-slate-800 px-1.5 py-0.5 rounded text-slate-300"
                                          >
                                            {comp}
                                          </span>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Evidence Sources & Deep Links */}
                          {selectedInvestigation.final_report.evidence_sources &&
                            selectedInvestigation.final_report.evidence_sources.length > 0 && (
                              <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-3">
                                <div className="text-[11px] uppercase tracking-wider text-slate-400 font-bold">
                                  Telemetry Evidence Sources ({selectedInvestigation.final_report.evidence_sources.length})
                                </div>

                                <div className="space-y-2">
                                  {selectedInvestigation.final_report.evidence_sources.map((ev) => {
                                    const isExpanded = expandedEvidenceId === ev.evidence_id;
                                    const deepLink = ev.supporting_data?.deep_link;
                                    return (
                                      <div
                                        key={ev.evidence_id}
                                        className={`p-3 rounded-lg border transition-all ${
                                          isExpanded
                                            ? "bg-slate-900 border-indigo-500/60 shadow-sm"
                                            : "bg-slate-950/60 border-slate-800/80"
                                        }`}
                                      >
                                        <div
                                          onClick={() =>
                                            setExpandedEvidenceId(isExpanded ? null : ev.evidence_id)
                                          }
                                          className="cursor-pointer flex items-start justify-between gap-2"
                                        >
                                          <div className="space-y-1">
                                            <div className="flex items-center gap-2">
                                              <span className="text-[10px] font-bold text-indigo-300 font-mono">
                                                {ev.evidence_id}
                                              </span>
                                              <span
                                                className={`px-1.5 py-0.2 rounded text-[9px] font-bold ${
                                                  ev.source_type === "METRIC"
                                                    ? "bg-amber-950 text-amber-300 border border-amber-800"
                                                    : ev.source_type === "LOG"
                                                    ? "bg-sky-950 text-sky-300 border border-sky-800"
                                                    : ev.source_type === "TRACE"
                                                    ? "bg-indigo-950 text-indigo-300 border border-indigo-800"
                                                    : "bg-purple-950 text-purple-300 border border-purple-800"
                                                }`}
                                              >
                                                {ev.source_type}
                                              </span>
                                              <span className="text-[10px] text-slate-500 font-mono">
                                                {ev.service_name}
                                              </span>
                                            </div>
                                            <div className="text-slate-200 text-xs">{ev.observation}</div>
                                          </div>
                                          <span className="text-slate-500 text-xs font-mono">
                                            {isExpanded ? "\u25B2" : "\u25BC"}
                                          </span>
                                        </div>

                                        {isExpanded && (
                                          <div className="mt-3 pt-3 border-t border-slate-800/80 space-y-2 text-[11px]">
                                            <div>
                                              <div className="text-[10px] text-slate-500">Query / Reference:</div>
                                              <pre className="bg-slate-950 p-2 rounded border border-slate-800 text-[10px] text-slate-300 overflow-x-auto mt-0.5">
                                                {ev.source_reference}
                                              </pre>
                                            </div>

                                            {typeof deepLink === "string" && /^https?:\/\//.test(deepLink) && (
                                              <div className="pt-1">
                                                <a
                                                  href={deepLink}
                                                  target="_blank"
                                                  rel="noreferrer"
                                                  className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-indigo-900/40 hover:bg-indigo-900/60 text-indigo-300 rounded border border-indigo-700/50 transition-colors"
                                                >
                                                  <span>Open in Observability Tool</span>
                                                  <span>&rarr;</span>
                                                </a>
                                              </div>
                                            )}
                                          </div>
                                        )}
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            )}

                          {/* Recommended Next Steps */}
                          {selectedInvestigation.final_report.recommended_next_steps &&
                            selectedInvestigation.final_report.recommended_next_steps.length > 0 && (
                              <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-2">
                                <div className="text-[11px] uppercase tracking-wider text-slate-400 font-bold">
                                  Recommended Next Steps
                                </div>
                                <ol className="space-y-1.5 pl-4 list-decimal text-slate-300 text-xs">
                                  {selectedInvestigation.final_report.recommended_next_steps.map((step, idx) => (
                                    <li key={idx} className="leading-relaxed">
                                      {step}
                                    </li>
                                  ))}
                                </ol>
                              </div>
                            )}

                          {/* Calibration Disclaimer Note */}
                          <div className="text-[10px] text-slate-500 italic pt-1 border-t border-slate-800">
                            * Note: Confidence scores (0.00 – 1.00) are deterministic heuristic ratings based on telemetry source diversity and validation consistency, not statistical Bayesian probabilities.
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* ========================================================================= */}
                {/* PHASE 5: CONTROLLED REMEDIATION & RECOVERY VERIFICATION */}
                {/* ========================================================================= */}
                <RemediationDrawerSection
                  incidentId={detail.id}
                  investigationId={selectedInvestigation?.id}
                  apiUrl={apiUrl}
                  onIncidentResolved={async () => {
                    try {
                      const res = await fetch(`${apiUrl}/api/v1/incidents/${detail.id}`);
                      if (res.ok) {
                        const updated = await res.json();
                        setDetail(updated);
                      }
                    } catch {
                      // ignore
                    }
                    void fetchIncidents();
                  }}
                />

                {/* Structured Deterministic Evidence Section */}
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
