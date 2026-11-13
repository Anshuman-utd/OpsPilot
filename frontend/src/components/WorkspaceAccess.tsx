"use client";

import { useState } from "react";

export default function WorkspaceAccess() {
  const [invite, setInvite] = useState<{ code: string; expires_at: string }>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function createInvite() {
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/auth/invites", { method: "POST" });
      if (!response.ok) throw new Error();
      setInvite(await response.json());
    } catch {
      setMessage("Could not create an invite. Check your session and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="workspace-panel">
      <h2>Invite a viewer</h2>
      <p>Viewers can read incidents, investigations, and recovery history. Each code can be used once and expires in 24 hours.</p>
      <button className="primary-button" disabled={busy} onClick={createInvite}>
        {busy ? "Creating…" : "Create signup code"}
      </button>
      {invite && (
        <div className="invite-result">
          <label htmlFor="signup-code">Signup code</label>
          <input id="signup-code" readOnly value={invite.code} onFocus={(event) => event.target.select()} />
          <p>Share the workspace URL and this code privately. Expires {new Date(invite.expires_at).toLocaleString()}.</p>
        </div>
      )}
      {message && <p role="alert" className="form-error">{message}</p>}
    </section>
  );
}
