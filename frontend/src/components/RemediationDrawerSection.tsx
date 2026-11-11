"use client";

import { useEffect, useState, useCallback } from "react";

export interface RemediationAction {
  id: string;
  plan_id: string;
  action_type: string;
  target_service: string;
  environment: string;
  parameters: Record<string, unknown>;
  rationale: string;
  expected_outcome: string;
  risk_level: "LOW" | "MEDIUM" | "HIGH";
  reversible: boolean;
  preconditions: Record<string, unknown>;
  verification_conditions: Record<string, unknown>;
  status:
    | "PROPOSED"
    | "PENDING_APPROVAL"
    | "APPROVED"
    | "REJECTED"
    | "EXECUTING"
    | "EXECUTED"
    | "FAILED"
    | "VERIFYING"
    | "VERIFIED"
    | "CANCELLED";
  created_at: string;
}

export interface VerificationObservation {
  cycle: number;
  timestamp: string;
  healthy: boolean;
  metric_name: string;
  value: number | null;
  threshold?: number | null;
  failure_reason?: string | null;
}

export interface RemediationVerificationRun {
  id: string;
  execution_id: string;
  status: "VERIFYING" | "RECOVERED" | "STILL_DEGRADED" | "REGRESSION_DETECTED" | "TELEMETRY_UNAVAILABLE";
  started_at: string;
  completed_at: string | null;
  consecutive_healthy_count: number;
  required_healthy_count: number;
  observations: VerificationObservation[];
  summary: string | null;
  created_at: string;
}

export interface RemediationExecution {
  id: string;
  plan_id: string;
  action_id: string;
  approval_id: string;
  idempotency_key: string;
  status: "PENDING" | "EXECUTING" | "SUCCEEDED" | "FAILED" | "VERIFYING" | "VERIFICATION_FAILED" | "RECOVERED";
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  execution_output: Record<string, unknown> | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  verification_runs?: RemediationVerificationRun[];
}

export interface RemediationAuditEvent {
  id: string;
  incident_id: string;
  investigation_id: string | null;
  plan_id: string | null;
  action_id: string | null;
  execution_id: string | null;
  event_type: string;
  actor: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface RemediationPlan {
  id: string;
  incident_id: string;
  investigation_id: string | null;
  status:
    | "DRAFT"
    | "PENDING_APPROVAL"
    | "APPROVED"
    | "REJECTED"
    | "EXPIRED"
    | "EXECUTING"
    | "VERIFYING"
    | "SUCCEEDED"
    | "FAILED"
    | "CANCELLED";
  version: number;
  summary: string;
  root_cause_hypothesis: {
    hypothesis_id?: string;
    title?: string;
    confidence?: string;
    explanation?: string;
    affected_components?: string[];
  } | null;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
  risk_assessment: {
    risk_level?: string;
    blast_radius?: string;
    downtime_risk?: string;
    data_loss_risk?: string;
    overall_risk_notes?: string;
  } | null;
  assumptions: string[];
  limitations: string[];
  expires_at: string | null;
  created_at: string;
  updated_at: string;
  actions: RemediationAction[];
  executions?: RemediationExecution[];
}

interface RemediationDrawerSectionProps {
  incidentId: string;
  investigationId?: string | null;
  apiUrl: string;
  onIncidentResolved?: () => void;
}

export function RemediationDrawerSection({
  incidentId,
  investigationId,
  apiUrl,
  onIncidentResolved,
}: RemediationDrawerSectionProps) {
  const [plans, setPlans] = useState<RemediationPlan[]>([]);
  const [selectedPlan, setSelectedPlan] = useState<RemediationPlan | null>(null);
  const [auditEvents, setAuditEvents] = useState<RemediationAuditEvent[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [proposing, setProposing] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Approval modal state
  const [approvingAction, setApprovingAction] = useState<RemediationAction | null>(null);
  const [approverId, setApproverId] = useState<string>("operator-sre");
  const [approvalComment, setApprovalComment] = useState<string>("");
  const [confirmCheckbox, setConfirmCheckbox] = useState<boolean>(false);
  const [submittingApproval, setSubmittingApproval] = useState<boolean>(false);

  // Rejection modal state
  const [rejectingAction, setRejectingAction] = useState<RemediationAction | null>(null);
  const [rejectionComment, setRejectionComment] = useState<string>("");
  const [submittingRejection, setSubmittingRejection] = useState<boolean>(false);

  // Load remediation plans on mount or incident change
  useEffect(() => {
    let isMounted = true;
    const loadPlans = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/v1/incidents/${incidentId}/remediation-plans`, { cache: "no-store" });
        if (res.ok && isMounted) {
          const data: RemediationPlan[] = await res.json();
          setPlans(data);
          if (data.length > 0) {
            setSelectedPlan((prev) => {
              if (prev) {
                const matched = data.find((p) => p.id === prev.id);
                return matched || data[0];
              }
              return data[0];
            });
          } else {
            setSelectedPlan(null);
          }
        }
      } catch (err) {
        console.error("Error fetching remediation plans:", err);
      } finally {
        if (isMounted) setLoading(false);
      }
    };

    void loadPlans();
    return () => {
      isMounted = false;
    };
  }, [incidentId, apiUrl]);

  // Load audit events when selected plan changes
  useEffect(() => {
    let isMounted = true;
    if (selectedPlan?.id) {
      const loadAudit = async () => {
        try {
          const res = await fetch(`${apiUrl}/api/v1/remediation-plans/${selectedPlan.id}/audit-events`, { cache: "no-store" });
          if (res.ok && isMounted) {
            const events: RemediationAuditEvent[] = await res.json();
            setAuditEvents(events);
          }
        } catch (err) {
          console.error("Error fetching remediation audit events:", err);
        }
      };
      void loadAudit();
    }
    return () => {
      isMounted = false;
    };
  }, [selectedPlan?.id, apiUrl]);

  // Manual refresh helper
  const refreshPlans = useCallback(async () => {
    try {
      const res = await fetch(`${apiUrl}/api/v1/incidents/${incidentId}/remediation-plans`, { cache: "no-store" });
      if (res.ok) {
        const data: RemediationPlan[] = await res.json();
        setPlans(data);
        if (data.length > 0) {
          setSelectedPlan((prev) => {
            if (prev) {
              const matched = data.find((p) => p.id === prev.id);
              return matched || data[0];
            }
            return data[0];
          });
        }
      }
    } catch (err) {
      console.error("Error refreshing plans:", err);
    }
  }, [apiUrl, incidentId]);

  const refreshAuditEvents = useCallback(
    async (planId: string) => {
      try {
        const res = await fetch(`${apiUrl}/api/v1/remediation-plans/${planId}/audit-events`, { cache: "no-store" });
        if (res.ok) {
          const events: RemediationAuditEvent[] = await res.json();
          setAuditEvents(events);
        }
      } catch (err) {
        console.error("Error refreshing audit events:", err);
      }
    },
    [apiUrl]
  );

  // Real-time polling when plan is executing or verifying
  useEffect(() => {
    if (!selectedPlan) return;
    const isWorking =
      selectedPlan.status === "EXECUTING" ||
      selectedPlan.status === "VERIFYING" ||
      selectedPlan.executions?.some(
        (e) => e.status === "EXECUTING" || e.status === "VERIFYING" || e.status === "PENDING"
      );

    if (!isWorking) return;

    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${apiUrl}/api/v1/remediation-plans/${selectedPlan.id}`);
        if (res.ok) {
          const updated: RemediationPlan = await res.json();
          setSelectedPlan(updated);
          setPlans((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
          void refreshAuditEvents(updated.id);

          if (updated.status === "SUCCEEDED") {
            onIncidentResolved?.();
          }
        }
      } catch (err) {
        console.error("Polling remediation plan status failed:", err);
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [selectedPlan, apiUrl, refreshAuditEvents, onIncidentResolved]);

  // Handle Propose Plan
  const handleProposePlan = async () => {
    try {
      setProposing(true);
      setErrorMessage(null);
      const res = await fetch(`${apiUrl}/api/v1/incidents/${incidentId}/remediation-plans`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          investigation_id: investigationId || null,
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Failed with status ${res.status}`);
      }

      const created: RemediationPlan = await res.json();
      setPlans((prev) => [created, ...prev]);
      setSelectedPlan(created);
    } catch (err) {
      setErrorMessage((err as Error).message);
    } finally {
      setProposing(false);
    }
  };

  // Handle Approve Action
  const handleConfirmApproval = async () => {
    if (!selectedPlan || !approvingAction || !confirmCheckbox) return;
    try {
      setSubmittingApproval(true);
      setErrorMessage(null);
      const res = await fetch(
        `${apiUrl}/api/v1/remediation-plans/${selectedPlan.id}/actions/${approvingAction.id}/approve`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            approver_id: approverId.trim() || "operator-sre",
            comment: approvalComment.trim() || "Operator approved remediation action execution",
            plan_version: selectedPlan.version,
          }),
        }
      );

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Approval failed");
      }

      setApprovingAction(null);
      setApprovalComment("");
      setConfirmCheckbox(false);
      // Refresh plan immediately
      void refreshPlans();
    } catch (err) {
      setErrorMessage((err as Error).message);
    } finally {
      setSubmittingApproval(false);
    }
  };

  // Handle Reject Action
  const handleConfirmRejection = async () => {
    if (!selectedPlan || !rejectingAction || !rejectionComment.trim()) return;
    try {
      setSubmittingRejection(true);
      setErrorMessage(null);
      const res = await fetch(
        `${apiUrl}/api/v1/remediation-plans/${selectedPlan.id}/actions/${rejectingAction.id}/reject`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            approver_id: approverId.trim() || "operator-sre",
            comment: rejectionComment.trim(),
          }),
        }
      );

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || "Rejection failed");
      }

      setRejectingAction(null);
      setRejectionComment("");
      void refreshPlans();
    } catch (err) {
      setErrorMessage((err as Error).message);
    } finally {
      setSubmittingRejection(false);
    }
  };

  // Latest execution & verification run
  const activeExecution = selectedPlan?.executions?.[0];
  const activeVerification = activeExecution?.verification_runs?.[0];

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "SUCCEEDED":
      case "RECOVERED":
      case "VERIFIED":
        return "bg-emerald-500/20 text-emerald-400 border-emerald-500/30";
      case "EXECUTING":
      case "VERIFYING":
        return "bg-sky-500/20 text-sky-400 border-sky-500/30 animate-pulse";
      case "PENDING_APPROVAL":
        return "bg-amber-500/20 text-amber-400 border-amber-500/30";
      case "APPROVED":
        return "bg-indigo-500/20 text-indigo-400 border-indigo-500/30";
      case "REJECTED":
      case "CANCELLED":
        return "bg-slate-700/50 text-slate-400 border-slate-600";
      case "FAILED":
      case "STILL_DEGRADED":
      case "REGRESSION_DETECTED":
        return "bg-rose-500/20 text-rose-400 border-rose-500/30";
      case "TELEMETRY_UNAVAILABLE":
        return "bg-purple-500/20 text-purple-400 border-purple-500/30";
      default:
        return "bg-slate-800 text-slate-300 border-slate-700";
    }
  };

  const getRiskBadge = (risk: string) => {
    switch (risk?.toUpperCase()) {
      case "LOW":
        return "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
      case "MEDIUM":
        return "bg-amber-500/15 text-amber-300 border-amber-500/30";
      case "HIGH":
      case "CRITICAL":
        return "bg-rose-500/15 text-rose-300 border-rose-500/30";
      default:
        return "bg-slate-800 text-slate-400 border-slate-700";
    }
  };

  return (
    <div className="bg-gradient-to-b from-slate-900/90 via-slate-900/60 to-slate-950/80 border border-emerald-900/40 rounded-xl p-4 space-y-4 shadow-lg font-mono">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-emerald-900/30 pb-3">
        <div className="space-y-0.5">
          <div className="text-[11px] uppercase tracking-wider text-emerald-300 font-bold flex items-center gap-1.5">
            <span>&#x2699; Human-Approved Remediation & Recovery</span>
            <span className="text-[10px] bg-emerald-500/20 text-emerald-300 px-1.5 py-0.2 rounded border border-emerald-500/30">
              Phase 5
            </span>
          </div>
          <div className="text-[11px] text-slate-400 font-sans">
            Propose allowlisted operational fixes, enforce mandatory human approval, and verify telemetry recovery.
          </div>
        </div>

        <button
          onClick={handleProposePlan}
          disabled={proposing || selectedPlan?.status === "EXECUTING" || selectedPlan?.status === "VERIFYING"}
          className="px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-xs font-mono font-medium rounded-lg transition-colors shadow-sm flex items-center gap-1.5"
        >
          {proposing ? (
            <>
              <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-solid border-white border-r-transparent" />
              <span>Formulating Plan...</span>
            </>
          ) : (
            <>
              <span>&#x25C6;</span>
              <span>{selectedPlan ? "Re-plan Remediation" : "Propose Remediation Plan"}</span>
            </>
          )}
        </button>
      </div>

      {/* Error alert */}
      {errorMessage && (
        <div className="p-3 bg-rose-950/60 border border-rose-800 text-rose-300 rounded-lg text-xs flex items-center justify-between">
          <span>{errorMessage}</span>
          <button onClick={() => setErrorMessage(null)} className="text-rose-400 hover:text-white font-bold ml-2">
            &times;
          </button>
        </div>
      )}

      {/* Plan History Selector */}
      {plans.length > 1 && (
        <div className="flex items-center gap-2 text-[11px]">
          <span className="text-slate-400">Plans:</span>
          <div className="flex items-center gap-1 overflow-x-auto">
            {plans.map((p, idx) => (
              <button
                key={p.id}
                onClick={() => setSelectedPlan(p)}
                className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
                  selectedPlan?.id === p.id
                    ? "bg-emerald-950 border-emerald-500 text-emerald-200"
                    : "bg-slate-900 border-slate-800 text-slate-400 hover:text-slate-200"
                }`}
              >
                Plan #{plans.length - idx} (v{p.version}) &bull; {p.status}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* No Plan State */}
      {!selectedPlan ? (
        <div className="p-6 text-center border border-dashed border-slate-800 rounded-xl space-y-2">
          <div className="text-slate-400 text-xs">
            {loading
              ? "Checking for existing remediation plans..."
              : "No remediation plan formulated yet. Click \"Propose Remediation Plan\" to evaluate evidence and generate allowlisted actions."}
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Plan Status Banner */}
          <div className="bg-slate-950/70 border border-slate-800/80 rounded-lg p-3 flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="text-slate-400">Status:</span>
              <span className={`px-2 py-0.5 rounded border text-[11px] font-bold ${getStatusBadge(selectedPlan.status)}`}>
                {selectedPlan.status}
              </span>
              <span className="text-slate-500">&bull;</span>
              <span className="text-slate-400">Version: <span className="text-white">v{selectedPlan.version}</span></span>
            </div>

            <div className="flex items-center gap-3 text-slate-400 text-[11px]">
              {selectedPlan.risk_assessment?.risk_level && (
                <div>
                  Risk:{" "}
                  <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${getRiskBadge(selectedPlan.risk_assessment.risk_level)}`}>
                    {selectedPlan.risk_assessment.risk_level}
                  </span>
                </div>
              )}
              {selectedPlan.expires_at && (
                <div title={selectedPlan.expires_at}>
                  Expires: <span className="text-slate-300">{new Date(selectedPlan.expires_at).toLocaleTimeString()}</span>
                </div>
              )}
            </div>
          </div>

          {/* Plan Summary */}
          <div className="space-y-1.5">
            <div className="text-[11px] uppercase tracking-wider text-slate-400 font-bold">
              Remediation Strategy
            </div>
            <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg text-slate-300 text-xs font-sans leading-relaxed">
              {selectedPlan.summary}
            </div>
          </div>

          {/* Root Cause Hypothesis Reference */}
          {selectedPlan.root_cause_hypothesis && (
            <div className="p-3 bg-indigo-950/30 border border-indigo-900/40 rounded-lg space-y-1 text-xs">
              <div className="text-[10px] uppercase text-indigo-300 font-bold">
                Target Hypothesis: {selectedPlan.root_cause_hypothesis.title || "Identified Root Cause"}
              </div>
              <div className="text-slate-300 font-sans text-[11px]">
                {selectedPlan.root_cause_hypothesis.explanation}
              </div>
              {selectedPlan.root_cause_hypothesis.affected_components && (
                <div className="flex items-center gap-1.5 pt-1 text-[10px] text-slate-400">
                  <span>Affected:</span>
                  {selectedPlan.root_cause_hypothesis.affected_components.map((c) => (
                    <span key={c} className="px-1.5 py-0.2 bg-slate-800 rounded text-slate-300 border border-slate-700">
                      {c}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Execution & Verification Stepper */}
          <div className="p-3 bg-slate-950/80 border border-slate-800 rounded-lg space-y-2">
            <div className="text-[10px] uppercase text-slate-400 font-bold tracking-wider">
              Remediation Workflow Pipeline
            </div>
            <div className="grid grid-cols-5 gap-2 text-center text-[10px]">
              {/* Step 1 */}
              <div className="p-2 rounded bg-emerald-950/60 border border-emerald-500/40 text-emerald-300">
                <div className="font-bold">&#x2713; 1. Formulated</div>
                <div className="text-[9px] text-slate-400">Evidence Backed</div>
              </div>

              {/* Step 2 */}
              <div
                className={`p-2 rounded border ${
                  selectedPlan.status === "PENDING_APPROVAL"
                    ? "bg-amber-950/60 border-amber-500/60 text-amber-300 animate-pulse"
                    : selectedPlan.status === "REJECTED"
                    ? "bg-rose-950/60 border-rose-500/40 text-rose-300"
                    : selectedPlan.status === "DRAFT"
                    ? "bg-slate-900 border-slate-800 text-slate-500"
                    : "bg-emerald-950/60 border-emerald-500/40 text-emerald-300"
                }`}
              >
                <div className="font-bold">
                  {selectedPlan.status === "REJECTED" ? "&#x2717; Rejected" : selectedPlan.status === "PENDING_APPROVAL" ? "2. Awaiting Approval" : "&#x2713; 2. Approved"}
                </div>
                <div className="text-[9px] text-slate-400">Human Operator</div>
              </div>

              {/* Step 3 */}
              <div
                className={`p-2 rounded border ${
                  selectedPlan.status === "EXECUTING"
                    ? "bg-sky-950/60 border-sky-500/60 text-sky-300 animate-pulse"
                    : selectedPlan.status === "VERIFYING" || selectedPlan.status === "SUCCEEDED"
                    ? "bg-emerald-950/60 border-emerald-500/40 text-emerald-300"
                    : selectedPlan.status === "FAILED" && activeExecution?.status === "FAILED"
                    ? "bg-rose-950/60 border-rose-500/40 text-rose-300"
                    : "bg-slate-900 border-slate-800 text-slate-500"
                }`}
              >
                <div className="font-bold">
                  {selectedPlan.status === "EXECUTING"
                    ? "3. Executing..."
                    : selectedPlan.status === "VERIFYING" || selectedPlan.status === "SUCCEEDED"
                    ? "&#x2713; 3. Executed"
                    : "3. Execution"}
                </div>
                <div className="text-[9px] text-slate-400">Controlled Handler</div>
              </div>

              {/* Step 4 */}
              <div
                className={`p-2 rounded border ${
                  selectedPlan.status === "VERIFYING"
                    ? "bg-sky-950/60 border-sky-500/60 text-sky-300 animate-pulse"
                    : selectedPlan.status === "SUCCEEDED"
                    ? "bg-emerald-950/60 border-emerald-500/40 text-emerald-300"
                    : activeVerification?.status === "STILL_DEGRADED" || activeVerification?.status === "REGRESSION_DETECTED"
                    ? "bg-rose-950/60 border-rose-500/40 text-rose-300"
                    : activeVerification?.status === "TELEMETRY_UNAVAILABLE"
                    ? "bg-purple-950/60 border-purple-500/40 text-purple-300"
                    : "bg-slate-900 border-slate-800 text-slate-500"
                }`}
              >
                <div className="font-bold">
                  {selectedPlan.status === "VERIFYING"
                    ? "4. Verifying..."
                    : selectedPlan.status === "SUCCEEDED"
                    ? "&#x2713; 4. Verified"
                    : "4. Verification"}
                </div>
                <div className="text-[9px] text-slate-400">Prometheus Telemetry</div>
              </div>

              {/* Step 5 */}
              <div
                className={`p-2 rounded border ${
                  selectedPlan.status === "SUCCEEDED"
                    ? "bg-emerald-950/80 border-emerald-500 text-emerald-300 font-bold"
                    : "bg-slate-900 border-slate-800 text-slate-500"
                }`}
              >
                <div className="font-bold">
                  {selectedPlan.status === "SUCCEEDED" ? "&#x2713; 5. Restored" : "5. Recovery"}
                </div>
                <div className="text-[9px] text-slate-400">Incident Auto-Resolved</div>
              </div>
            </div>
          </div>

          {/* Proposed Actions List */}
          <div className="space-y-3">
            <div className="text-[11px] uppercase tracking-wider text-slate-400 font-bold flex items-center justify-between">
              <span>Proposed Actions ({selectedPlan.actions.length})</span>
              <span className="text-[10px] text-emerald-400 bg-emerald-950/50 px-2 py-0.5 rounded border border-emerald-800/60">
                Deterministic Policy Checked
              </span>
            </div>

            {selectedPlan.actions.map((act) => (
              <div
                key={act.id}
                className="p-3.5 bg-slate-950/60 border border-slate-800 hover:border-slate-700 rounded-xl space-y-3 transition-colors"
              >
                {/* Action Title & Badges */}
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-white text-xs font-bold font-mono">{act.action_type}</span>
                    <span className="px-1.5 py-0.2 bg-slate-800 text-slate-300 rounded text-[10px] border border-slate-700">
                      target: {act.target_service}
                    </span>
                    <span className="px-1.5 py-0.2 bg-slate-800 text-slate-400 rounded text-[10px]">
                      env: {act.environment}
                    </span>
                  </div>

                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-0.5 rounded border text-[10px] font-bold ${getStatusBadge(act.status)}`}>
                      {act.status}
                    </span>
                    <span className={`px-2 py-0.5 rounded border text-[10px] font-bold ${getRiskBadge(act.risk_level)}`}>
                      {act.risk_level} RISK
                    </span>
                  </div>
                </div>

                {/* Parameters & Rationale */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-sans">
                  <div className="space-y-1">
                    <span className="text-[10px] uppercase font-mono text-slate-500 font-bold">Rationale:</span>
                    <p className="text-slate-300 text-[11px]">{act.rationale}</p>
                  </div>
                  <div className="space-y-1">
                    <span className="text-[10px] uppercase font-mono text-slate-500 font-bold">Expected Outcome:</span>
                    <p className="text-slate-300 text-[11px]">{act.expected_outcome}</p>
                  </div>
                </div>

                {/* Parameters Display */}
                {Object.keys(act.parameters || {}).length > 0 && (
                  <div className="p-2 bg-slate-900 rounded border border-slate-800/80 text-[11px]">
                    <span className="text-slate-500 text-[10px] uppercase font-bold mr-2">Parameters:</span>
                    <span className="text-emerald-300 font-mono">{JSON.stringify(act.parameters)}</span>
                  </div>
                )}

                {/* Safety & Precondition Checklist */}
                <div className="flex flex-wrap items-center gap-3 text-[10px] text-slate-400 pt-1 border-t border-slate-800/60">
                  <div className="flex items-center gap-1 text-emerald-400">
                    <span>&#x2713;</span>
                    <span>Allowlist Verified</span>
                  </div>
                  <div className="flex items-center gap-1 text-emerald-400">
                    <span>&#x2713;</span>
                    <span>Reversible: {act.reversible ? "Yes" : "No"}</span>
                  </div>
                  <div className="flex items-center gap-1 text-slate-400">
                    <span>Target Endpoint:</span>
                    <span className="text-slate-300 font-mono">/debug/faults</span>
                  </div>
                </div>

                {/* Human Approval Action Buttons */}
                {(act.status === "PROPOSED" || act.status === "PENDING_APPROVAL") &&
                  selectedPlan.status === "PENDING_APPROVAL" && (
                    <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
                      <button
                        onClick={() => setRejectingAction(act)}
                        className="px-3 py-1 bg-slate-800 hover:bg-rose-950 hover:text-rose-300 hover:border-rose-700 text-slate-300 text-xs rounded border border-slate-700 transition-colors"
                      >
                        Reject Action
                      </button>
                      <button
                        onClick={() => {
                          setApprovingAction(act);
                          setConfirmCheckbox(false);
                          setApprovalComment(`Approved remediation action ${act.action_type} for ${act.target_service}`);
                        }}
                        className="px-3.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold rounded transition-colors shadow-sm flex items-center gap-1.5"
                      >
                        <span>&#x2713;</span>
                        <span>Approve & Execute</span>
                      </button>
                    </div>
                  )}
              </div>
            ))}
          </div>

          {/* Recovery Verification Section */}
          {activeVerification && (
            <div className="p-4 bg-slate-950/80 border border-slate-800 rounded-xl space-y-3">
              <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                <div className="text-[11px] uppercase tracking-wider text-slate-300 font-bold flex items-center gap-2">
                  <span>Recovery Verification Telemetry</span>
                  <span className={`px-2 py-0.5 rounded border text-[10px] font-bold ${getStatusBadge(activeVerification.status)}`}>
                    {activeVerification.status}
                  </span>
                </div>

                <div className="text-xs text-slate-400">
                  Consecutive Healthy:{" "}
                  <span className="text-emerald-400 font-bold">
                    {activeVerification.consecutive_healthy_count} / {activeVerification.required_healthy_count}
                  </span>
                </div>
              </div>

              {/* Honest Telemetry Warning if UNAVAILABLE */}
              {activeVerification.status === "TELEMETRY_UNAVAILABLE" && (
                <div className="p-3 bg-purple-950/40 border border-purple-800/80 rounded-lg text-xs text-purple-200 space-y-1">
                  <div className="font-bold flex items-center gap-1.5 text-purple-300">
                    <span>&#x26A0;</span>
                    <span>Prometheus Telemetry Unavailable</span>
                  </div>
                  <div className="text-[11px] text-purple-300/80 font-sans">
                    Verification cannot honestly declare recovery because metrics are missing or Prometheus returned empty responses. SRE operator manual inspection is required.
                  </div>
                </div>
              )}

              {/* Verification Summary */}
              {activeVerification.summary && (
                <div className="text-xs text-slate-300 font-sans p-2.5 bg-slate-900 rounded border border-slate-800">
                  {activeVerification.summary}
                </div>
              )}

              {/* Multi-cycle observations table */}
              {activeVerification.observations?.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-[10px] uppercase text-slate-500 font-bold">
                    Observation Cycles ({activeVerification.observations.length})
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs border border-slate-800 rounded">
                      <thead className="bg-slate-900 text-slate-400 text-[10px] uppercase">
                        <tr>
                          <th className="p-2 border-b border-slate-800">Cycle</th>
                          <th className="p-2 border-b border-slate-800">Timestamp</th>
                          <th className="p-2 border-b border-slate-800">Metric</th>
                          <th className="p-2 border-b border-slate-800">Value</th>
                          <th className="p-2 border-b border-slate-800">Status</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/60 font-mono text-[11px]">
                        {activeVerification.observations.map((obs, idx) => (
                          <tr key={idx} className="hover:bg-slate-900/40">
                            <td className="p-2 text-slate-400">#{obs.cycle}</td>
                            <td className="p-2 text-slate-400">
                              {new Date(obs.timestamp).toLocaleTimeString()}
                            </td>
                            <td className="p-2 text-slate-300">{obs.metric_name}</td>
                            <td className="p-2 font-bold text-white">
                              {obs.value !== null && obs.value !== undefined
                                ? typeof obs.value === "number"
                                  ? obs.value.toFixed(4)
                                  : obs.value
                                : "N/A"}
                            </td>
                            <td className="p-2">
                              {obs.healthy ? (
                                <span className="px-1.5 py-0.2 bg-emerald-950 text-emerald-400 border border-emerald-800 rounded text-[10px] font-bold">
                                  PASS
                                </span>
                              ) : (
                                <span className="px-1.5 py-0.2 bg-rose-950 text-rose-400 border border-rose-800 rounded text-[10px] font-bold">
                                  FAIL
                                </span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Remediation Audit Trail */}
          {auditEvents.length > 0 && (
            <div className="space-y-2 pt-2 border-t border-slate-800">
              <div className="text-[11px] uppercase tracking-wider text-slate-400 font-bold">
                Remediation Audit Trail ({auditEvents.length})
              </div>
              <div className="space-y-2 border-l border-slate-800 ml-2 pl-3 max-h-48 overflow-y-auto">
                {auditEvents.map((evt) => (
                  <div key={evt.id} className="relative space-y-0.5 text-xs">
                    <div className="absolute -left-[17px] top-1.5 h-1.5 w-1.5 rounded-full bg-emerald-500 border border-slate-950" />
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-bold text-slate-300">{evt.event_type}</span>
                      <span className="text-[10px] text-slate-600">&bull;</span>
                      <span className="text-[10px] text-slate-500">
                        {new Date(evt.created_at).toLocaleTimeString()}
                      </span>
                      <span className="text-[10px] text-emerald-400">by {evt.actor}</span>
                    </div>
                    {evt.details && Object.keys(evt.details).length > 0 && (
                      <div className="text-[10px] text-slate-400 font-sans truncate">
                        {JSON.stringify(evt.details)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Human Approval Confirmation Modal */}
      {approvingAction && selectedPlan && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-emerald-500/50 rounded-xl p-5 max-w-lg w-full space-y-4 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="text-sm font-bold text-emerald-300 flex items-center gap-2">
                <span>&#x26A0;</span>
                <span>Mandatory Human Approval Authorization</span>
              </div>
              <button
                onClick={() => setApprovingAction(null)}
                className="text-slate-400 hover:text-white text-lg font-bold"
              >
                &times;
              </button>
            </div>

            <div className="space-y-3 text-xs font-sans">
              <div className="p-3 bg-slate-950 rounded-lg border border-slate-800 space-y-2 font-mono">
                <div className="flex justify-between text-[11px]">
                  <span className="text-slate-500">Target Service:</span>
                  <span className="text-white font-bold">{approvingAction.target_service}</span>
                </div>
                <div className="flex justify-between text-[11px]">
                  <span className="text-slate-500">Action Type:</span>
                  <span className="text-emerald-400 font-bold">{approvingAction.action_type}</span>
                </div>
                <div className="flex justify-between text-[11px]">
                  <span className="text-slate-500">Plan Version:</span>
                  <span className="text-indigo-400 font-bold">v{selectedPlan.version}</span>
                </div>
                <div className="flex justify-between text-[11px]">
                  <span className="text-slate-500">Risk Assessment:</span>
                  <span className={`px-1.5 py-0.2 rounded border text-[10px] font-bold ${getRiskBadge(approvingAction.risk_level)}`}>
                    {approvingAction.risk_level}
                  </span>
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-[11px] font-mono text-slate-300 font-bold">
                  Operator Identity:
                </label>
                <input
                  type="text"
                  value={approverId}
                  onChange={(e) => setApproverId(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded p-2 text-xs text-white focus:outline-none focus:border-emerald-500 font-mono"
                  placeholder="e.g. alice@sre-team"
                />
              </div>

              <div className="space-y-1">
                <label className="text-[11px] font-mono text-slate-300 font-bold">
                  Approval Justification / Comment:
                </label>
                <textarea
                  value={approvalComment}
                  onChange={(e) => setApprovalComment(e.target.value)}
                  rows={2}
                  className="w-full bg-slate-950 border border-slate-800 rounded p-2 text-xs text-white focus:outline-none focus:border-emerald-500"
                  placeholder="Explain why this action is authorized and verified safe..."
                />
              </div>

              <label className="flex items-start gap-2.5 p-2 bg-emerald-950/20 border border-emerald-900/50 rounded cursor-pointer">
                <input
                  type="checkbox"
                  checked={confirmCheckbox}
                  onChange={(e) => setConfirmCheckbox(e.target.checked)}
                  className="mt-0.5 rounded border-slate-700 text-emerald-600 focus:ring-0"
                />
                <span className="text-[11px] text-slate-300">
                  I explicitly authorize OpsPilot to execute this allowlisted action on{" "}
                  <strong className="text-white">{approvingAction.target_service}</strong>. I acknowledge this execution will be logged to the immutable audit trail.
                </span>
              </label>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
              <button
                onClick={() => setApprovingAction(null)}
                className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmApproval}
                disabled={!confirmCheckbox || submittingApproval}
                className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-xs font-bold rounded transition-colors flex items-center gap-1.5"
              >
                {submittingApproval ? (
                  <>
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-solid border-white border-r-transparent" />
                    <span>Executing...</span>
                  </>
                ) : (
                  <span>Authorize & Execute Action</span>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Human Rejection Modal */}
      {rejectingAction && selectedPlan && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-rose-500/50 rounded-xl p-5 max-w-md w-full space-y-4 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="text-sm font-bold text-rose-300 flex items-center gap-2">
                <span>&#x2717;</span>
                <span>Reject Remediation Action</span>
              </div>
              <button
                onClick={() => setRejectingAction(null)}
                className="text-slate-400 hover:text-white text-lg font-bold"
              >
                &times;
              </button>
            </div>

            <div className="space-y-3 text-xs font-sans">
              <p className="text-slate-300">
                You are rejecting action <strong className="text-white font-mono">{rejectingAction.action_type}</strong> for{" "}
                <strong className="text-white font-mono">{rejectingAction.target_service}</strong>.
              </p>

              <div className="space-y-1">
                <label className="text-[11px] font-mono text-slate-300 font-bold">
                  Mandatory Rejection Rationale:
                </label>
                <textarea
                  value={rejectionComment}
                  onChange={(e) => setRejectionComment(e.target.value)}
                  rows={3}
                  className="w-full bg-slate-950 border border-slate-800 rounded p-2 text-xs text-white focus:outline-none focus:border-rose-500"
                  placeholder="Specify why this action is rejected or inappropriate for this incident..."
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
              <button
                onClick={() => setRejectingAction(null)}
                className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmRejection}
                disabled={!rejectionComment.trim() || submittingRejection}
                className="px-4 py-1.5 bg-rose-600 hover:bg-rose-500 disabled:opacity-50 text-white text-xs font-bold rounded transition-colors flex items-center gap-1.5"
              >
                {submittingRejection ? (
                  <>
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-solid border-white border-r-transparent" />
                    <span>Rejecting...</span>
                  </>
                ) : (
                  <span>Confirm Rejection</span>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
