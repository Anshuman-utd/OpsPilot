"use client";

import { useEffect, useState } from "react";
import { useIdentity } from "./Workspace";

const catalog = [
  { id: "api-gateway", label: "API gateway", description: "Routes incoming requests to the order service." },
  { id: "order-service", label: "Order service", description: "Creates orders and coordinates payment." },
  { id: "payment-service", label: "Payment service", description: "Processes transactions and reports payment outcomes." },
];

export default function DemoServicesOverview() {
  const identity = useIdentity();
  const [services, setServices] = useState<Record<string, { status: string }> | null>(null);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [orderStatus, setOrderStatus] = useState("");

  async function createOrder() {
    setSending(true);
    setOrderStatus("");
    try {
      const response = await fetch("/api/v1/services/demo-orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ customer_id: "workspace-demo", amount: 12, currency: "USD" }),
      });
      if (!response.ok) throw new Error();
      const order = await response.json();
      setOrderStatus(`Demo order ${String(order.status).toLowerCase().replaceAll("_", " ")}.`);
    } catch {
      setOrderStatus("Could not create a demo order.");
    } finally {
      setSending(false);
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    async function refresh() {
      try {
        const response = await fetch("/api/v1/services/health", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("Service health is temporarily unavailable.");
        const result = await response.json();
        setServices(result.services);
        setError("");
      } catch {
        if (!controller.signal.aborted) {
          setError("Service health is temporarily unavailable.");
          setServices(null);
        }
      }
    }
    void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 10_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);

  return (
    <div className="space-y-4">
      <section className="workspace-panel" aria-label="Service dependencies">
        <h2>Order request path</h2>
        <ol className="service-path" aria-label="Request order">
          {catalog.map((item) => (
            <li key={item.id}>
              <strong>{item.label}</strong>
              <span>{services?.[item.id]?.status ?? "Unknown"}</span>
            </li>
          ))}
        </ol>
      </section>
      <section className="service-list" aria-label="Monitored services">
      {error && <p className="form-error" role="alert">{error}</p>}
      {catalog.map((item) => {
        const status = services?.[item.id]?.status;
        return (
          <article key={item.id}>
            <span className="service-icon nav-icon services" aria-hidden="true" />
            <div><h2>{item.label}</h2><p>{item.description}</p></div>
            <span className={`service-state ${status === "healthy" ? "healthy" : status ? "degraded" : ""}`}>
              {status ?? (error ? "Unknown" : "Checking…")}
            </span>
          </article>
        );
      })}
      {identity.role === "operator" && identity.demo_orders_enabled && (
        <article>
          <div><h2>Demo traffic</h2><p role="status">{orderStatus || "Send one synthetic order through the three services."}</p></div>
          <button className="primary-button ml-auto" disabled={sending} onClick={createOrder}>
            {sending ? "Sending…" : "Create test order"}
          </button>
        </article>
      )}
      </section>
    </div>
  );
}
