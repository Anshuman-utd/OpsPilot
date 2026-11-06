"use client";

import { useEffect, useState, useCallback } from "react";

interface MicroserviceStatus {
  status: string;
  error?: string;
}

interface ServicesHealthResponse {
  status: string;
  timestamp: string;
  services: {
    "api-gateway"?: MicroserviceStatus;
    "order-service"?: MicroserviceStatus;
    "payment-service"?: MicroserviceStatus;
  };
}

export default function DemoServicesOverview() {
  const [healthData, setHealthData] = useState<ServicesHealthResponse | null>(null);
  const [gatewayStatus, setGatewayStatus] = useState<"checking" | "online" | "offline">("checking");
  const [orderResult, setOrderResult] = useState<{
    loading: boolean;
    data?: Record<string, unknown>;
    error?: string;
  }>({ loading: false });

  const gatewayUrl = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8001";

  const fetchServicesHealth = useCallback(async () => {
    try {
      const res = await fetch(`${gatewayUrl}/services/health`, { cache: "no-store" });
      if (res.ok) {
        const json = await res.json();
        setHealthData(json);
        setGatewayStatus("online");
      } else {
        setGatewayStatus("offline");
      }
    } catch {
      setGatewayStatus("offline");
      setHealthData(null);
    }
  }, [gatewayUrl]);

  useEffect(() => {
    let isMounted = true;
    const runCheck = async () => {
      if (isMounted) {
        await fetchServicesHealth();
      }
    };
    void runCheck();
    const interval = setInterval(runCheck, 10000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [fetchServicesHealth]);

  const handleTestOrder = async () => {
    setOrderResult({ loading: true });
    try {
      const res = await fetch(`${gatewayUrl}/api/orders`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          customer_id: `cust-${Math.floor(Math.random() * 9000 + 1000)}`,
          amount: parseFloat((Math.random() * 100 + 10).toFixed(2)),
          currency: "USD",
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setOrderResult({ loading: false, data });
      } else {
        setOrderResult({ loading: false, error: data.detail || "Failed to create order" });
      }
    } catch (err: unknown) {
      setOrderResult({
        loading: false,
        error: err instanceof Error ? err.message : "Network error contacting API Gateway",
      });
    }
  };

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 backdrop-blur-sm space-y-4">
      <div className="flex items-center justify-between pb-3 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <span
            className={`h-2.5 w-2.5 rounded-full ${
              gatewayStatus === "online" ? "bg-emerald-500 animate-pulse" : "bg-slate-500"
            }`}
          />
          <h3 className="font-mono text-sm font-semibold tracking-wide uppercase text-slate-200">
            Phase 2 Demo Microservices (:8001 - :8003)
          </h3>
        </div>

        <div className="flex items-center gap-2 text-xs font-mono">
          <a
            href="http://localhost:3001"
            target="_blank"
            rel="noreferrer"
            className="rounded bg-indigo-950/80 border border-indigo-700/60 px-2.5 py-1 text-indigo-300 hover:text-white transition-colors"
          >
            Grafana (:3001) &rarr;
          </a>
          <a
            href="http://localhost:9090"
            target="_blank"
            rel="noreferrer"
            className="rounded bg-amber-950/80 border border-amber-700/60 px-2.5 py-1 text-amber-300 hover:text-white transition-colors"
          >
            Prometheus (:9090) &rarr;
          </a>
        </div>
      </div>

      {/* Services Matrix Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 font-mono text-xs">
        <div className="rounded-lg bg-slate-950/70 p-3.5 border border-slate-800/80 space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-slate-400">API Gateway</span>
            <span className="text-[10px] text-slate-500">:8001</span>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full ${
                gatewayStatus === "online" ? "bg-emerald-400" : "bg-rose-500"
              }`}
            />
            <span className="text-slate-200 uppercase font-semibold">
              {gatewayStatus === "online" ? "HEALTHY" : "OFFLINE"}
            </span>
          </div>
        </div>

        <div className="rounded-lg bg-slate-950/70 p-3.5 border border-slate-800/80 space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-slate-400">Order Service</span>
            <span className="text-[10px] text-slate-500">:8002</span>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full ${
                healthData?.services["order-service"]?.status === "healthy"
                  ? "bg-emerald-400"
                  : "bg-amber-500"
              }`}
            />
            <span className="text-slate-200 uppercase font-semibold">
              {healthData?.services["order-service"]?.status || "UNKNOWN"}
            </span>
          </div>
        </div>

        <div className="rounded-lg bg-slate-950/70 p-3.5 border border-slate-800/80 space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-slate-400">Payment Service</span>
            <span className="text-[10px] text-slate-500">:8003</span>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 rounded-full ${
                healthData?.services["payment-service"]?.status === "healthy"
                  ? "bg-emerald-400"
                  : "bg-amber-500"
              }`}
            />
            <span className="text-slate-200 uppercase font-semibold">
              {healthData?.services["payment-service"]?.status || "UNKNOWN"}
            </span>
          </div>
        </div>
      </div>

      {/* Quick Test Traffic Generator */}
      <div className="rounded-lg bg-slate-950/40 p-4 border border-slate-800/60 flex flex-col md:flex-row items-start md:items-center justify-between gap-3 text-xs font-mono">
        <div>
          <span className="text-slate-300 font-semibold block">Synthetic Order Request Generator</span>
          <span className="text-slate-500 text-[11px]">
            Exercises Gateway &rarr; Order Service &rarr; Payment Service with W3C distributed traces.
          </span>
        </div>

        <button
          onClick={handleTestOrder}
          disabled={orderResult.loading || gatewayStatus !== "online"}
          className="rounded-md border border-slate-700 bg-slate-800 px-4 py-1.5 text-xs font-mono text-slate-200 hover:bg-slate-700 hover:text-white disabled:opacity-50 transition-colors cursor-pointer"
        >
          {orderResult.loading ? "Sending..." : "Create Test Order"}
        </button>
      </div>

      {/* Test Order Outcome Display */}
      {orderResult.data && (
        <div className="rounded-lg border border-emerald-900/60 bg-emerald-950/30 p-3 text-xs font-mono text-emerald-300">
          ✓ Order Result: ID={String(orderResult.data.id)} | Status={String(orderResult.data.status)} |
          PaymentID={String(orderResult.data.payment_id || "N/A")}
        </div>
      )}

      {orderResult.error && (
        <div className="rounded-lg border border-rose-900/60 bg-rose-950/30 p-3 text-xs font-mono text-rose-300">
          ✕ Request Error: {orderResult.error}
        </div>
      )}
    </div>
  );
}
