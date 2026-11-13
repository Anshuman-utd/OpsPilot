# OpsPilot architecture

```mermaid
flowchart LR
  User -->|HTTPS| Caddy
  Caddy --> Next[Next.js workspace]
  Caddy -->|API requests| API[FastAPI]
  API --> DB[(PostgreSQL)]
  API --> Redis[(Redis)]
  Beat --> Redis --> Worker[Celery workers]
  Worker --> DB
  Worker --> Prometheus
  Worker --> Loki
  Worker --> Tempo
  Gateway --> Orders --> Payments
  Gateway & Orders & Payments --> Collector --> Tempo
  Gateway & Orders & Payments --> Alloy --> Loki
  Prometheus -->|Scrapes| Gateway & Orders & Payments
  Worker -->|Approved demo controls| Orders & Payments
```

```mermaid
sequenceDiagram
  participant Operator
  participant API
  participant Worker
  participant Telemetry
  participant Target
  participant DB as PostgreSQL
  Worker->>Telemetry: Evaluate detection rules
  Worker->>DB: Persist an incident
  Operator->>API: Start investigation
  API->>Worker: Queue evidence collection and RCA
  Worker->>Telemetry: Collect metrics, logs, and traces
  Operator->>API: Review plan and approve action hash
  API->>Worker: Queue approved execution
  Worker->>Target: Recheck approval and disable fault
  Worker->>Telemetry: Verify metric and target health
  Operator->>API: Download incident report
```

```mermaid
erDiagram
  ACCOUNTS ||--o{ ACCOUNT_SESSIONS : owns
  INCIDENTS ||--o{ INCIDENT_EVENTS : records
  INCIDENTS ||--o{ INVESTIGATIONS : investigated_by
  INVESTIGATIONS ||--o{ INVESTIGATION_AGENT_RUNS : contains
  INCIDENTS ||--o{ REMEDIATION_PLANS : addressed_by
  REMEDIATION_PLANS ||--o{ REMEDIATION_ACTIONS : proposes
  REMEDIATION_ACTIONS ||--o{ REMEDIATION_APPROVALS : reviewed_by
  REMEDIATION_ACTIONS ||--o{ REMEDIATION_EXECUTIONS : dispatched_as
  REMEDIATION_EXECUTIONS ||--o{ REMEDIATION_VERIFICATION_RUNS : verified_by
  REMEDIATION_PLANS ||--o{ REMEDIATION_AUDIT_EVENTS : records
```

## Security boundaries

- Caddy is the only published application service. HTTPS and the configured request origin protect browser sessions. Passwords use scrypt, session and invite tokens are stored as digests, and sign-in and signup are rate-limited.
- Host administrators provision operator accounts or operator invitations. In-app invites grant viewer access. The workspace is shared by all accounts on the installation.
- Operators approve the digest of the displayed action. The server derives their identity from the session, records the action snapshot, and checks the digest again in the worker. Cancellation stops queued work. A lab approval includes the run ID so an old action cannot disable a newer simulation. A transport error after dispatch has an unknown outcome and needs inspection.
- Telemetry and model outputs can suggest hypotheses and plans. They cannot bypass the approval gate or select targets outside the demo allowlist. When configured, Groq receives the structured evidence supplied to the RCA provider.
- The release assumes a trusted Docker host and trusted database administrators. Protect backups and operator invites. Review telemetry and error messages before sharing exported reports.

The local development API offers FastAPI documentation at `http://localhost:8000/docs`. The release uses same-origin routes for signup, sessions, incident lists and reports, investigations, plan decisions, and the optional demo lab.
