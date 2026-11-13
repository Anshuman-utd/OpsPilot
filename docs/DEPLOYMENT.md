# Deploy OpsPilot without AWS

Run the Next.js frontend, FastAPI backend, Celery, and the telemetry stack on **one Linux VM**. A DigitalOcean Droplet, Hetzner Cloud server, or an existing Ubuntu host can run the same Compose release. Start with 4 vCPUs, 8 GB RAM, and at least 40 GB of disk for the full stack and on-host image builds. Measure usage before sizing for additional traffic.

Caddy sends page requests to Next.js and `/api/*` requests to FastAPI on the same domain. PostgreSQL, Redis, the demo services, and the observability tools use private container networking. The frontend uses relative API URLs, so a separate frontend hosting account and cross-origin cookie configuration are unnecessary.

## Run locally

With Docker Desktop running, open a terminal at the repo root:

```sh
./deploy/init-env.sh local

docker compose --env-file .env.preview -p opspilot-release \
  -f docker-compose.yml -f compose.release.yml up -d --build --wait

docker compose --env-file .env.preview -p opspilot-release \
  -f docker-compose.yml -f compose.release.yml exec api \
  python -m app.accounts --invite --role operator
```

Open [http://localhost:8088](http://localhost:8088), choose **Create an account**, paste the one-time code, and set your username and password. Existing operators can sign in with their existing credentials. Passwords need at least 12 characters. In **Team access**, an operator can generate viewer signup codes to share privately. Each code expires after 24 hours and can be used once. Signup grants the role attached to the invite.

For an existing local install, add `DEMO_LAB_ENABLED=true` to `.env.preview` alongside `FAULT_INJECTION_ENABLED=true` and rerun Compose. The initializer preserves existing credentials and settings. The lab sends synthetic orders through the local demo services.

## Deploy on a DigitalOcean Droplet or Hetzner VM

1. **Create an Ubuntu server.** Add your SSH public key during provisioning. Use an SSH-enabled administrative user. Configure the cloud firewall to allow TCP 80 and 443 from the internet and SSH from your IP. Avoid publishing any additional Compose ports. [DigitalOcean Docker image](https://docs.digitalocean.com/products/marketplace/catalog/docker/) · [Hetzner server setup](https://docs.hetzner.com/cloud/servers/getting-started/creating-a-server/)
2. **Install Docker and Compose.** Use the provider’s Docker image or install Docker Engine and the Compose plugin on Ubuntu. Verify `docker compose version` reports 2.24.4 or later. Install Git and OpenSSL if your image omits them. [Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/)
3. **Point your domain at the server.** Create an A record such as `ops.example.com` with the server’s public IPv4 address. Add an AAAA record only if IPv6 works on your host. Caddy uses the hostname and reachable ports to obtain and renew certificates. [Caddy HTTPS setup](https://caddyserver.com/docs/quick-starts/https)
4. **Clone the app and start it.** Run these on the VM from the release checkout. Use the branch or tag containing the release files.

   ```sh
   git clone https://github.com/Anshuman-utd/OpsPilot.git
   cd OpsPilot
   git switch codex/opspilot-first-release
   ./deploy/init-env.sh production https://ops.example.com

   docker compose --env-file .env.release -p opspilot-release \
     -f docker-compose.yml -f compose.release.yml up -d --build --wait
   ```

   The initializer creates private database and Grafana secrets in `.env.release`. Keep the file backed up and outside source control. Set `GROQ_API_KEY` there only if you want Groq-backed RCA. The default investigation path uses deterministic RCA.
5. **Create the first account.** Generate an operator invite from the server and use it on your site’s signup form. Viewers can then join using codes from Team access.

   ```sh
   docker compose --env-file .env.release -p opspilot-release \
     -f docker-compose.yml -f compose.release.yml exec api \
     python -m app.accounts --invite --role operator

   curl -fsS https://ops.example.com/health/live
   ```

6. **Check the installation.** Sign in, open **System health** and **Services**, and confirm the API, PostgreSQL, Redis, and sample services are available. Use `docker compose … logs edge api worker beat` to diagnose startup failures. Replace the ellipsis with the same environment, project, and file arguments from step 4.

The production defaults keep fault injection, demo traffic, and the lab disabled. To host an interactive portfolio demo, use a separate instance with synthetic data and enable `FAULT_INJECTION_ENABLED`, `DEMO_ORDERS_ENABLED`, and `DEMO_LAB_ENABLED`. Invite viewers to inspect the demo and give operator invites only to people who should be able to launch faults and approve actions.

## Operate the release

- **Manage accounts.** On the host, `python -m app.accounts USER --role operator` through the API container creates or resets a named account and revokes its existing sessions. Use `--disable` to disable an account and revoke sessions. A role change also requires a new password through this command.
- **Back up before upgrading.** Export both PostgreSQL databases and store copies off-host. For the default platform database, use `docker compose … exec -T postgres pg_dump -U opspilot -Fc opspilot > opspilot.dump`. Repeat for `demo-postgres` with its configured username and database. Test restoration before relying on a backup.
- **Upgrade.** Pull the release branch, rebuild with the same project name and environment, and run `docker compose … exec -T api alembic check`. Inspect the migration and worker logs. Keep the existing volumes and credentials when rebuilding.
- **Investigate a failed action.** If the execution says its outcome is unknown, inspect the target before approving a new plan. The worker reconciles pending jobs and resumes verification without replaying interrupted dispatches.
- **Know the operating limits.** This release uses one host and local volumes. It monitors the included sample application and executes allowlisted demo controls. External production infrastructure needs service-specific integrations and permissions. Public hosting requires a server and DNS under your control.
