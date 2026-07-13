"""LLM classification, via the OpenAI SDK's structured-output parsing."""

from __future__ import annotations

import logging
from functools import lru_cache

from openai import OpenAI

from .clients.gmail import GmailMessage
from .config import get_settings
from .policy import Classification

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You triage a user's inbox. Assign each email exactly one category.

urgent
  Time-critical and personally directed at the user. A real person or system
  needs a response within hours: outages, security alerts, interview or meeting
  scheduling for an imminent date, a direct question from a manager or client,
  payment or account failures. Bias toward urgent only when a delay causes harm.

action_needed
  The user must do something, but not today. Forms to fill, documents to review
  or sign, invitations awaiting RSVP, non-blocking follow-ups, bills not yet due.

newsletter
  Bulk or subscribed content the user opted into: digests, marketing from
  services they use, product announcements, social notifications, receipts and
  automated confirmations that need no action.

spam
  Unsolicited bulk mail, phishing, scams, cold sales outreach the user never
  asked for.

Judge by content, not by sender reputation alone. A newsletter with an alarming
subject line is still a newsletter. State your reason in one sentence, and give
an honest confidence -- use a low value when the email is genuinely ambiguous.
Also provide a 1-2 sentence summary of the email, and extract any concrete
action items or deadlines the user needs to complete."""

USER_TEMPLATE = """From: {sender}
To: {recipient}
Date: {received_at}
Subject: {subject}

{body}"""


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    s = get_settings()
    return OpenAI(api_key=s.openai_api_key or None, timeout=30.0, max_retries=2)


def classify(msg: GmailMessage) -> tuple[Classification | None, str | None]:
    """Return (classification, error). Exactly one is non-None."""
    s = get_settings()
    try:
        completion = _client().beta.chat.completions.parse(
            model=s.llm_model,
            temperature=s.llm_temperature,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": USER_TEMPLATE.format(
                        sender=msg.sender,
                        recipient=msg.recipient,
                        received_at=msg.received_at,
                        subject=msg.subject,
                        body=msg.body,
                    ),
                },
            ],
            response_format=Classification,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            return None, "classify: model returned no parsable output"
        return parsed, None
    except Exception as exc:  # noqa: BLE001 -- one bad email must not stop the run
        log.exception("classification failed for %s", msg.id)
        return None, f"classify: {exc!r}"
