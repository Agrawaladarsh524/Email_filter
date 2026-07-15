"""Telegram Bot API alerting."""

from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import get_settings

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"

_ESCAPE = str.maketrans({"<": "&lt;", ">": "&gt;", "&": "&amp;"})


def escape(text: str) -> str:
    """Escape for Telegram's HTML parse mode."""
    return text.translate(_ESCAPE)


def build_message(
    category: str, subject: str, sender: str, reason: str, confidence: float, link: str
) -> str:
    return (
        f"<b>{escape(category.replace('_', ' ').upper())}</b>\n\n"
        f"<b>{escape(subject)}</b>\n"
        f"From: {escape(sender)}\n\n"
        f"{escape(reason)}\n"
        f"<i>Confidence: {confidence:.0%}</i>\n\n"
        f'<a href="{link}">Open in Gmail</a>'
    )


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def send(text: str) -> None:
    s = get_settings()
    if not s.telegram_enabled:
        raise RuntimeError("Telegram bot token / chat ID is not configured")

    resp = httpx.post(
        API.format(token=s.telegram_bot_token),
        json={
            "chat_id": s.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
