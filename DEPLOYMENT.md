# EGX Smart Trading Coach — Cloud Run Deployment Notes

This document prepares the Telegram bot for Google Cloud Run deployment.
It does **not** deploy anything automatically.

## Service overview

- Container entrypoint: `python main.py --telegram-bot`
- Health endpoints: `/` and `/health` on `PORT` (default `8080`)
- On-demand report button in Telegram: `🔄 حدّث التقرير دلوقتي`
- Report source: latest JSON under `data/reports/` inside the container runtime
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
- If no saved report JSON exists yet, use `🔄 حدّث التقرير دلوقتي` from Telegram
- On-demand report command inside the container:
  `python main.py --egx-workflow report --data-provider tradingview --scanner-universe full-market --top-candidates 10 --min-score 75`

## Optional TA-Lib in Cloud Run

- TA-Lib is optional. Reports must continue with TradingView technical fields and fallback metadata if TA-Lib is unavailable.
- The Dockerfile attempts to install the Python `TA-Lib` wheel from `requirements-talib.txt` and verifies `import talib` during image build.
- If the optional install/import fails, the image still builds and runtime reports show `TA-Lib: FALLBACK ⚠️ talib package not installed`.
- After deploying a new image, run:

```bash
python main.py --egx-cloud-readiness-check
```

Expected when TA-Lib is present:

```text
TA-Lib runtime: ACTIVE ✅
```

If unavailable, readiness remains non-fatal and prints:

```text
TA-Lib runtime: FALLBACK ⚠️ reason: ...
```

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
- Use `--min-instances 1` for Telegram polling and on-demand local reports in V1

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

Reports generated from `🔄 حدّث التقرير دلوقتي` are saved inside the Cloud Run container under:

- `data/reports/`
- `data/real/`
- `storage/`

These files are **temporary/ephemeral**. They may disappear when the Cloud Run instance restarts or scales.

Persistent Cloud Storage / Firestore sync will come in a later patch.

Until persistent storage is added:

- Keep `--min-instances 1` if you want reports to survive longer on one warm instance
- Expect report menus to reset after cold starts until the user presses `🔄 حدّث التقرير دلوقتي` again
