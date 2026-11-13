# Deploy OpsPilot

The first release targets a single Linux host with Docker Engine, Compose 2.24.4 or newer, and a DNS name. Caddy serves the dashboard and API from one origin and manages HTTPS for the configured domain. The Compose release gives the database, broker, demo services, and telemetry stack private container networking. Alloy reads rotated service logs from a shared volume.

## Install

1. Point your DNS record at the server and allow TCP 80 and 443 through its firewall.
2. Clone the repository. From the repository root, prepare the deployment environment.

   ```sh
   umask 077
   cp deploy/release.env.example .env.release
   chmod 600 .env.release
   ```

3. Edit `.env.release`. Set `OPSPILOT_ORIGIN` to your HTTPS origin and generate three distinct database and Grafana passwords using `openssl rand -hex 24`. Hex values can be embedded directly in the internal database connection URLs. The production configuration requires an HTTPS origin.
4. Build and start the stack, then provision your first named operator. The migration service runs before the API starts. Use the same project name and environment file for subsequent commands.

   ```sh
   docker compose --env-file .env.release -p opspilot-release \
     -f docker-compose.yml -f compose.release.yml up -d --build --wait

   docker compose --env-file .env.release -p opspilot-release \
     -f docker-compose.yml -f compose.release.yml exec api \
     python -m app.accounts alice --role operator
   ```

5. Open the configured HTTPS origin and sign in. Check **System health** and **Services**. Use the same account command with `--role viewer` to provision read-only access. The command prompts for passwords and accepts `--disable` to revoke an account and its sessions. Re-running it with a new password also revokes existing sessions.

The public endpoints are the workspace and a minimal liveness probe. Incident data, telemetry summaries, and mutations use authenticated sessions. Operators can write and viewers can read. Fault injection and automated remediation are off in the release defaults. Enable them only in an isolated demo deployment. The release uses Docker Compose to run FastAPI, PostgreSQL, Redis, and Celery together.

## Local authenticated preview

Use a separate project name and fresh volumes. In a copy of the release environment, set these values alongside the three generated passwords.

```dotenv
OPSPILOT_ENV=development
OPSPILOT_ORIGIN=http://localhost:8088
EDGE_SITE_ADDRESS=http://localhost
EDGE_HTTP_BIND=127.0.0.1:8088
EDGE_HTTPS_BIND=127.0.0.1:8448
```

Run the same Compose and account commands with that environment file, then visit `http://localhost:8088`. To run the full smoke test on a disposable instance, set `FAULT_INJECTION_ENABLED=true` and pipe `deploy/smoke.py` into `docker compose … exec -T api python -`. The test creates synthetic orders, an operator account, and incident records.

For the existing local preview, create your account with:

```sh
docker compose --env-file .env.preview -p opspilot-release \
  -f docker-compose.yml -f compose.release.yml exec api \
  python -m app.accounts operator --role operator
```

## Operate and upgrade

- Back up both PostgreSQL databases and store a copy away from the host. For example, `docker compose … exec -T postgres pg_dump -U opspilot -Fc opspilot > opspilot.dump` creates a platform dump with the default database name. Repeat for the demo database with its configured credentials and test restoration.
- Before upgrading, take a fresh backup. Rebuild with the same Compose project and environment file. Inspect `docker compose … logs migrate` and run `docker compose … exec -T api alembic check`. Use database backups for rollbacks that include incompatible migrations.
- Inspect worker and Beat logs if incidents stop updating. The worker periodically requeues old pending work and resumes stalled verification. An action interrupted during dispatch is marked failed with an unknown outcome. Inspect the demo service before proposing another action.
- Plan monitoring and disk capacity for PostgreSQL, Redis, and telemetry data. The single-host release has one instance of each backing service and local volumes.

References [Docker Compose overrides](https://docs.docker.com/reference/compose-file/merge/) · [Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
