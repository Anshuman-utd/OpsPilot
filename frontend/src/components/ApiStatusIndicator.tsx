"use client";

import { useEffect, useState, useCallback } from "react";

interface ComponentHealth {
  status: string;
  details?: Record<string, unknown> | null;
}

interface ReadinessData {
  status: string;
  timestamp: string;
  components?: {
    database?: ComponentHealth;
    redis?: ComponentHealth;
  };
}

export default function ApiStatusIndicator() {
  const [status, setStatus] = useState<"checking" | "connected" | "degraded" | "offline">("checking");
  const [data, setData] = useState<ReadinessData | null>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [lastChecked, setLastChecked] = useState<string | null>(null);

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const fetchHealth = useCallback(async () => {
    const startTime = performance.now();
    try {
      const response = await fetch(`${apiUrl}/health/ready`, {
        cache: "no-store",
      });
      const elapsed = Math.round(performance.now() - startTime);
      setLatency(elapsed);
      setLastChecked(new Date().toLocaleTimeString());

      const json = await response.json();
      setData(json);

      if (response.ok && json.status === "ready") {
        setStatus("connected");
      } else {
        setStatus("degraded");
      }
    } catch {
      setStatus("offline");
      setData(null);
      setLatency(null);
      setLastChecked(new Date().toLocaleTimeString());
    }
  }, [apiUrl]);

  const handleManualCheck = () => {
    setStatus("checking");
    void fetchHealth();
  };

  useEffect(() => {
    let isMounted = true;
    const runCheck = async () => {
      if (isMounted) {
        await fetchHealth();
      }
    };
    void runCheck();
    return () => {
      isMounted = false;
    };
  }, [fetchHealth]);

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 backdrop-blur-sm">
      <div className="flex items-center justify-between pb-3 border-b border-slate-800/80">
        <div className="flex items-center space-x-3">
          <span className="relative flex h-3 w-3">
            {status === "connected" && (
              <>
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
              </>
            )}
            {status === "degraded" && (
              <span className="relative inline-flex rounded-full h-3 w-3 bg-amber-500"></span>
            )}
            {status === "offline" && (
              <span className="relative inline-flex rounded-full h-3 w-3 bg-rose-500"></span>
            )}
            {status === "checking" && (
              <span className="animate-pulse relative inline-flex rounded-full h-3 w-3 bg-slate-400"></span>
            )}
          </span>
          <span className="font-mono text-sm font-semibold tracking-wide uppercase text-slate-200">
            Backend API:{" "}
            {status === "connected" && <span className="text-emerald-400">Online & Ready</span>}
            {status === "degraded" && <span className="text-amber-400">Degraded (Backing Services Offline)</span>}
            {status === "offline" && <span className="text-rose-400">Offline</span>}
            {status === "checking" && <span className="text-slate-400">Checking...</span>}
          </span>
        </div>

        <button
          onClick={handleManualCheck}
          className="rounded-md border border-slate-700 bg-slate-800 px-3 py-1 text-xs font-mono text-slate-300 hover:bg-slate-700 hover:text-white transition-colors cursor-pointer"
        >
          Check Again
        </button>
      </div>

      <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-3 text-xs font-mono">
        <div className="rounded-lg bg-slate-950/60 p-3 border border-slate-800/60">
          <p className="text-slate-400">Target Endpoint</p>
          <p className="text-slate-200 truncate mt-1">{apiUrl}</p>
        </div>
        <div className="rounded-lg bg-slate-950/60 p-3 border border-slate-800/60">
          <p className="text-slate-400">Round-trip Latency</p>
          <p className="text-slate-200 mt-1">{latency !== null ? `${latency} ms` : "—"}</p>
        </div>
        <div className="rounded-lg bg-slate-950/60 p-3 border border-slate-800/60">
          <p className="text-slate-400">Last Checked</p>
          <p className="text-slate-200 mt-1">{lastChecked || "—"}</p>
        </div>
      </div>

      {data?.components && (
        <div className="mt-3 pt-3 border-t border-slate-800/60 flex items-center gap-6 text-xs font-mono">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                data.components.database?.status === "healthy" ? "bg-emerald-500" : "bg-rose-500"
              }`}
            />
            <span className="text-slate-300">
              PostgreSQL: <span className="text-slate-400">{data.components.database?.status}</span>
            </span>
          </div>

          <div className="flex items-center gap-2">
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                data.components.redis?.status === "healthy" ? "bg-emerald-500" : "bg-rose-500"
              }`}
            />
            <span className="text-slate-300">
              Redis: <span className="text-slate-400">{data.components.redis?.status}</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
