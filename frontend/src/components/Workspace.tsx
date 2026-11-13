"use client";

import { createContext, useContext } from "react";

export type Identity = {
  username: string;
  role: "operator" | "viewer";
  authenticated: boolean;
  remediation_enabled: boolean;
  demo_orders_enabled: boolean;
  demo_lab_enabled: boolean;
};

export const WorkspaceIdentity = createContext<Identity>({
  username: "local-operator",
  role: "operator",
  authenticated: false,
  remediation_enabled: true,
  demo_orders_enabled: false,
  demo_lab_enabled: false,
});

export function useIdentity() {
  return useContext(WorkspaceIdentity);
}

export function Brand({ caption = false }: { caption?: boolean }) {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
      <div><strong>OpsPilot</strong>{caption && <small>Reliability workspace</small>}</div>
    </div>
  );
}
