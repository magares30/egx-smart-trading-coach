"""Notify Telegram when paper exits close trades after a cloud report run."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_ALLOWED_CHAT_ID_ENV = "TELEGRAM_ALLOWED_CHAT_ID"


def _bot_token() -> str | None:
    token = os.environ.get(TELEGRAM_BOT_TOKEN_ENV, "").strip()
    return token or None


def _allowed_chat_id() -> str | None:
    chat_id = os.environ.get(TELEGRAM_ALLOWED_CHAT_ID_ENV, "").strip()
    return chat_id or None


def _send_telegram_message(*, token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(body) if body else {}
        return bool(parsed.get("ok"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as error:
        logger.warning("Paper close Telegram notify failed: %s", type(error).__name__)
        return False


def notify_paper_closes_from_report_payload(
    payload: dict[str, Any] | None,
) -> bool:
    """Send a Telegram alert when latest report metadata shows paper exits closed trades."""
    from core.paper_exit_execution import format_paper_exit_telegram_alert

    if not payload:
        return False

    metadata = payload.get("report_metadata") or {}
    if not isinstance(metadata, dict):
        return False

    message = format_paper_exit_telegram_alert(metadata)
    if not message:
        return False

    chat_id = _allowed_chat_id()
    if not chat_id:
        logger.info(
            "Paper close notify skipped: %s not set",
            TELEGRAM_ALLOWED_CHAT_ID_ENV,
        )
        return False

    token = _bot_token()
    if not token:
        logger.info(
            "Paper close notify skipped: %s not set",
            TELEGRAM_BOT_TOKEN_ENV,
        )
        return False

    sent = _send_telegram_message(token=token, chat_id=chat_id, text=message)
    if sent:
        closed_count = int(metadata.get("paper_exit_execution_closed_count") or 0)
        logger.info("Paper close Telegram notify sent: closed=%s", closed_count)
    return sent


def notify_paper_closes_from_latest_report() -> bool:
    """Load latest report from GCS/local and notify if paper exits closed trades."""
    from core.cloud_state_store import load_latest_report_json_payload

    payload = load_latest_report_json_payload()
    return notify_paper_closes_from_report_payload(payload)
