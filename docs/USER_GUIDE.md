# Use OpsPilot

## Join the workspace

Open the workspace URL and select **Create an account**. Enter the signup code from an operator, choose a username, and set a password of at least 12 characters. Operators can launch demo scenarios and approve actions. Viewers can read incidents, evidence, and reports.

## Run the payment demo

1. Sign in as an operator and open **Demo lab**. Select **Payment declines** and start the scenario. The lab warms up the sample application and continues sending synthetic orders while you investigate.
2. Follow the incident link when it appears in the lab, or open **Incidents** and search for payment. Detection runs periodically. Use **Run detection** to queue an additional pass. A payment failure can also raise a separate order-service incident because the order service depends on payments.
3. Open the payment incident and select **Start investigation**. Review observed facts, cited evidence, and the proposed cause. Without a Groq key, the report uses deterministic reasoning.
4. Under **Remediation**, propose a plan. Check the target, action parameters, and verification criteria in the approval dialog. Confirm the action while the fault is active. Cancel a plan from the remediation section while it is still queued.
5. Watch the recovery observations. A successful HTTP action begins verification. The verifier waits for consecutive healthy readings. The error-rate window can take roughly two minutes to clear.
6. Open **All statuses** or filter for **Resolved** to revisit the incident. Select **Download incident report** for the timeline, investigation, execution, and verification record.

The lab has six scenarios. Run one at a time. Recent failures can stay in rolling telemetry windows after a scenario ends. For a clean timing comparison, wait for the relevant signals to settle or use a disposable Compose project with fresh volumes.

## Work with incidents

- Search by title, service, or exact incident ID. Use status, severity, and service filters to narrow the queue.
- **Acknowledge** records who took the incident. Use **Start investigation** to collect evidence. Use **Resolve** when an operator has independently confirmed that the issue is closed.
- In **Services**, check the gateway, order, and payment services. The gateway calls the order service, which calls the payment service.
- In **System health**, check the platform API, PostgreSQL, and Redis. An unavailable telemetry signal can prevent automated recovery from being confirmed.
- In **Team access**, create a viewer signup code and share it privately with the workspace URL.
