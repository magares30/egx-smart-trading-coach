# EGX Smart Trading Coach — Cloud Run Deployment Notes

This document prepares the Telegram bot for Google Cloud Run deployment.
It does **not** deploy anything automatically.

## Service overview

- Container entrypoint: `python main.py --telegram-bot`
- Health endpoints: `/` and `/health` on `PORT` (default `8080`)
- Report source in V1: latest JSON under `data/reports/` inside the container
- Paper trading only; no broker APIs; no real execution

## Required environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token from BotFather. Set in Cloud Run env or Secret Manager. Never commit it. |
| `TELEGRAM_ALLOWED_CHAT_ID` | No | Restrict bot replies to one chat id. If unset, all chats are allowed and a warning is logged. |
| `PORT` | Auto on Cloud Run | HTTP port for health checks. Cloud Run sets this automatically. Default locally: `8080`. |
| `PYTHONUNBUFFERED` | Optional | Already set in the Dockerfile (`1`) so logs flush immediately. |

## Local run (unchanged)

```bash
export TELEGRAM_BOT_TOKEN="your-token-here"
export TELEGRAM_ALLOWED_CHAT_ID="123456789"   # optional
python main.py --telegram-bot
```

Report workflow remains separate:

```bash
python main.py --egx-workflow report --data-provider tradingview --scanner-universe full-market --top-candidates 10 --min-score 75
```

## Cloud Run readiness (V1)

- Dockerfile uses `python:3.12-slim`
- `.dockerignore` excludes local caches, secrets, and report JSON/TXT artifacts
- Health server starts in a background thread before Telegram polling
- Missing `TELEGRAM_BOT_TOKEN` exits with a clear error and does not print the token
- If no saved report JSON exists in the container, the bot replies in Arabic that no report is available yet

## Build image locally (manual)

```bash
docker build -t egx-telegram-bot .
```

## Deploy to Cloud Run (manual placeholders)

Replace placeholders before running:

```bash
gcloud config set project YOUR_GCP_PROJECT_ID

gcloud builds submit --tag gcr.io/YOUR_GCP_PROJECT_ID/egx-telegram-bot

gcloud run deploy egx-telegram-bot \
  --image gcr.io/YOUR_GCP_PROJECT_ID/egx-telegram-bot \
  --region YOUR_GCP_REGION \
  --platform managed \
  --allow-unauthenticated \
  --set-secrets TELEGRAM_BOT_TOKEN=telegram-bot-token:latest
```

Optional for development only (avoid shell history leaks in production):

```bash
  --set-env-vars TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
```

Recommended for production:

- Store `TELEGRAM_BOT_TOKEN` in Secret Manager instead of plain env vars
- Set `TELEGRAM_ALLOWED_CHAT_ID` to restrict access
- Use `--min-instances 1` only if you need always-on polling behavior

Example region placeholder: `europe-west1`

## Security notes

- Do not commit `.env`, tokens, or credentials
- Do not log `TELEGRAM_BOT_TOKEN`
- Cloud Run service account should have only the permissions it needs

## Not included yet (future patches)

- Firebase integration
- Firestore report storage
- Cloud Storage report sync
- Auto-refresh worker / scheduled report generation inside Cloud Run
- Webhook mode for Telegram (current V1 uses polling)
- Moving report generation into the cloud service

## V1 report storage limitation

In this patch, the container does not ship local `data/reports/*.json` files.
After deployment, run the report workflow separately and copy/upload reports in a later patch, or mount/sync storage when Cloud Storage support is added.

Until then, the bot will respond normally but report-based menu answers will say no saved report is available.
