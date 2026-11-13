# OpsPilot workspace

The Next.js workspace provides sign-in, incident triage, investigations, remediation review, and service health.

For local development, start the FastAPI application and its dependencies using the root README. Then run:

```sh
npm ci
npm run dev -- --hostname 127.0.0.1
```

Next.js forwards API requests to `http://127.0.0.1:8000` by default. Set `OPSPILOT_API_ORIGIN` in `.env.local` to change that upstream. When testing authentication, configure the backend's `PUBLIC_ORIGIN` to match the address you use in the browser.

The [deployment guide](../docs/DEPLOYMENT.md) covers the production build, same-origin proxy, and named accounts.
