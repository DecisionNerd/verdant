# Self-hosted Trigger.dev

Verdant's digest pipeline runs via the Compose `worker` job queue out of the box.
To also bring up the official Trigger.dev v4 platform (dashboard + durable runners):

```bash
./infra/trigger/up.sh
```

## Default login (local)

No account creation required:

1. Open http://localhost:18704
2. Enter **`verdant@example.com`** and continue with magic link
3. In local mode (`NODE_ENV=development`) Trigger signs you in immediately — no email delivery

That address is auto-promoted to admin via `ADMIN_EMAILS`.

## Optional project key

If you want Verdant workers to talk to Trigger tasks (not required for the local job queue):

1. Create a project in the dashboard
2. Copy the secret key into Verdant `.env` as `TRIGGER_SECRET_KEY`
3. Keep `TRIGGER_API_URL=http://localhost:18704`

Task definitions live in `/trigger` (`digest-document`, `run-eval-experiment`).
