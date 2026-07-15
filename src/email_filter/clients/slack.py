"""Slack incoming-webhook alerting."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import get_settings

log = logging.getLogger(__name__)

_EMOJI = {
    "urgent": ":rotating_light:",
    "action_needed": ":memo:",
    "newsletter": ":newspaper:",
    "spam": ":wastebasket:",
}


def build_blocks(
    category: str, subject: str, sender: str, reason: str, confidence: float, link: str
) -> list[dict[str, Any]]:
    emoji = _EMOJI.get(category, ":email:")
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} {category.replace('_', ' ').title()}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*From*\n{sender}"},
                {"type": "mrkdwn", "text": f"*Confidence*\n{confidence:.0%}"},
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{subject}*\n{reason}"}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open in Gmail"},
                    "url": link,
                }
            ],
        },
    ]


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def send(text: str, blocks: list[dict[str, Any]] | None = None) -> None:
    s = get_settings()
    if not s.slack_enabled:
        raise RuntimeError("Slack webhook URL is not configured")

    payload: dict[str, Any] = {"text": text}
    if blocks:
        payload["blocks"] = blocks

    resp = httpx.post(s.slack_webhook_url, json=payload, timeout=10.0)
    resp.raise_for_status()
