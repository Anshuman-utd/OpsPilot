"use client";

import { useEffect, useState } from "react";
import { useIdentity } from "./Workspace";

type Scenario = { id: string; label: string; service: string; signals: string[] };
type Run = {
  job: string;
  scenario: string;
  status: string;
  orders_sent: number;
  incident?: { id: string; title: string; status: string };
};
type Lab = { scenarios: Scenario[]; run: Run | null };

export default function DemoLab({ openIncidents }: { openIncidents: (incidentId?: string) => void }) {
  const identity = useIdentity();
  const [lab, setLab] = useState<Lab>();
  const [selection, setSelection] = useState("payment_failure_spike");
  const [loadError, setLoadError] = useState("");
  const [launchError, setLaunchError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function refresh() {
      try {
        const response = await fetch("/api/v1/demo", { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error();
        setLab(await response.json());
        setLoadError("");
      } catch {
        if (!controller.signal.aborted) setLoadError("Could not load the lab. Retrying…");
      }
    }
    void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 4000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);

  async function start() {
    setBusy(true);
    setLaunchError("");
    try {
      const response = await fetch(`/api/v1/demo/${selection}`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Could not start the scenario.");
      setLab((current) => current ? { ...current, run: result } : current);
    } catch (cause) {
      setLaunchError(cause instanceof Error ? cause.message : "Could not start the scenario.");
    } finally {
      setBusy(false);
    }
  }

  const run = lab?.run;
  const active = run && ["queued", "warming", "running"].includes(run.status);
  const scenario = lab?.scenarios.find((item) => item.id === selection);
  return (
    <div className="space-y-4">
      <section className="workspace-panel">
        <h2>Run an incident scenario</h2>
        <p>The lab creates synthetic orders and applies a temporary fault to the demo services.</p>
        {identity.role === "operator" && lab?.scenarios.length ? (
          <div className="lab-controls">
            <label htmlFor="scenario">Scenario</label>
            <select id="scenario" value={selection} onChange={(event) => setSelection(event.target.value)}>
              {lab.scenarios.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
            {scenario && <p>{scenario.service} · {scenario.signals.map((signal) => signal.replaceAll("_", " ")).join(", ")}</p>}
            <button className="primary-button" disabled={busy || Boolean(active)} onClick={start}>
              {busy ? "Queuing…" : active ? "Scenario running" : "Start scenario"}
            </button>
          </div>
        ) : null}
        {run && <p role="status"><strong>{lab?.scenarios.find((item) => item.id === run.scenario)?.label}</strong> · {run.status} · {run.orders_sent} requests sent</p>}
        {run?.incident && (
          <button className="secondary-button text-sm" onClick={() => openIncidents(run.incident?.id)}>
            {run.incident.title} · {run.incident.status.toLowerCase()}
          </button>
        )}
        {loadError && <p className="form-error" role="alert">{loadError}</p>}
        {launchError && <p className="form-error" role="alert">{launchError}</p>}
      </section>
      <section className="workspace-panel">
        <h2>Respond to the incident</h2>
        <ol className="workflow-list">
          <li>Open the incident queue and look for the affected service. Detection can take up to a minute.</li>
          <li>Open the incident and start an investigation. Review the observed facts and cited evidence.</li>
          <li>Propose a remediation plan, review the action, and approve it. Prometheus checks recovery while the lab sends traffic.</li>
          <li>Open the resolved incident to download its report.</li>
        </ol>
        <button className="primary-button" onClick={() => openIncidents()}>Open incident queue</button>
      </section>
    </div>
  );
}
