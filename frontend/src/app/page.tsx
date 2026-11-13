"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import ApiStatusIndicator from "@/components/ApiStatusIndicator";
import DemoServicesOverview from "@/components/DemoServicesOverview";
import IncidentsDashboard from "@/components/IncidentsDashboard";
import { Brand, WorkspaceIdentity, type Identity } from "@/components/Workspace";

const sections = [
  { id: "incidents", label: "Incidents", icon: "incident", summary: "Review alerts, investigate causes, and track recovery." },
  { id: "services", label: "Services", icon: "services", summary: "Check the availability of the services OpsPilot monitors." },
  { id: "system", label: "System health", icon: "health", summary: "Check the API, database, and message broker." },
] as const;

function SignIn({ onLogin, error }: { onLogin: (identity: Identity) => void; error?: string }) {
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setPending(true);
    setFailure("");
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: data.get("username"), password: data.get("password") }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => null);
        throw new Error(typeof result?.detail === "string" ? result.detail : "Unable to sign in.");
      }
      onLogin(await response.json());
    } catch (cause) {
      setFailure(cause instanceof Error ? cause.message : "Unable to sign in.");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="sign-in">
      <aside className="sign-in-aside">
        <Brand />
        <div className="sign-in-intro">
          <h2>Incident response,<br />from alert to recovery.</h2>
          <div className="response-flow" aria-label="Incident response stages">
            <div><span className="nav-icon incident" /> Detect</div>
            <div><span className="nav-icon services" /> Investigate</div>
            <div><span className="nav-icon health" /> Verify recovery</div>
          </div>
        </div>
        <span className="sign-in-foot">OpsPilot Reliability Workspace</span>
      </aside>
      <section className="sign-in-body">
        <form className="sign-in-form" onSubmit={submit}>
          <h1>Sign in</h1>
          <p>Use your workspace account to access OpsPilot.</p>
          <label htmlFor="username">Username</label>
          <input id="username" name="username" autoComplete="username" required maxLength={64} />
          <label htmlFor="password">Password</label>
          <input id="password" name="password" type="password" autoComplete="current-password" required maxLength={256} />
          {(failure || error) && <p className="form-error" role="alert">{failure || error}</p>}
          <button className="primary-button" disabled={pending}>{pending ? "Signing in…" : "Sign in"}</button>
        </form>
      </section>
    </main>
  );
}

export default function Home() {
  const [identity, setIdentity] = useState<Identity | null | undefined>();
  const [sessionError, setSessionError] = useState("");
  const [sectionId, setSection] = useState<(typeof sections)[number]["id"]>("incidents");
  const [signingOut, setSigningOut] = useState(false);

  const checkSession = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch("/api/auth/session", { cache: "no-store", signal });
      if (response.status === 401) setIdentity(null);
      else if (response.ok) setIdentity(await response.json());
      else throw new Error("The workspace is unavailable. Try again shortly.");
      setSessionError("");
    } catch (cause) {
      if (signal?.aborted) return;
      setSessionError(cause instanceof Error ? cause.message : "Unable to contact the workspace.");
      setIdentity((current) => current ?? null);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const initial = setTimeout(() => { void checkSession(controller.signal); }, 0);
    const timer = setInterval(() => { if (!document.hidden) void checkSession(controller.signal); }, 30_000);
    return () => { controller.abort(); clearTimeout(initial); clearInterval(timer); };
  }, [checkSession]);

  async function signOut() {
    setSigningOut(true);
    try {
      const response = await fetch("/api/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error("Sign-out failed. Try again.");
      setIdentity(null);
    } catch {
      setSessionError("Sign-out failed. Try again.");
    } finally {
      setSigningOut(false);
    }
  }

  if (identity === undefined) return <div className="workspace-loading"><Brand /><p role="status">Loading workspace…</p></div>;
  if (!identity) return <SignIn onLogin={(account) => { setSessionError(""); setIdentity(account); }} error={sessionError} />;

  const section = sections.find((item) => item.id === sectionId) ?? sections[0];
  return (
    <WorkspaceIdentity.Provider value={identity}>
      <div className="workspace">
        <aside className="sidebar">
          <Brand caption />
          <nav aria-label="Workspace">
            {sections.map((item) => (
              <button key={item.id} aria-current={sectionId === item.id ? "page" : undefined} onClick={() => setSection(item.id)}>
                <span className={`nav-icon ${item.icon}`} aria-hidden="true" /> {item.label}
              </button>
            ))}
          </nav>
          <div className="sidebar-account">
            <div className="avatar" aria-hidden="true">{identity.username.slice(0, 2).toUpperCase()}</div>
            <div><strong>{identity.username}</strong><small>{identity.role === "operator" ? "Operator" : "Viewer"}</small></div>
            {identity.authenticated && <button aria-label="Sign out" title="Sign out" disabled={signingOut} onClick={signOut}>↪</button>}
          </div>
        </aside>
        <div className="workspace-main">
          <header className="workspace-bar"><span>Workspace</span><span aria-hidden="true">/</span><strong>{section.label}</strong></header>
          <main className="workspace-content">
            <div className="workspace-heading"><h1>{section.label}</h1><p>{section.summary}</p></div>
            {sessionError && <p className="form-error" role="alert">{sessionError}</p>}
            {sectionId === "incidents" && <IncidentsDashboard />}
            {sectionId === "services" && <DemoServicesOverview />}
            {sectionId === "system" && <ApiStatusIndicator />}
          </main>
        </div>
      </div>
    </WorkspaceIdentity.Provider>
  );
}
