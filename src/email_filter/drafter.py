"""LLM-based generation of draft replies."""

from __future__ import annotations

import logging
from pydantic import BaseModel, Field

from .classifier import _client
from .clients.gmail import GmailMessage
from .config import get_settings

log = logging.getLogger(__name__)

DRAFT_PROMPT = """You are a professional executive assistant. 
Draft a brief reply to the following email.

Rules:
- Keep it under 3 sentences.
- Match the formality level of the incoming email.
- If you don't have enough information to fully respond, acknowledge receipt and say you'll follow up.
- Never make commitments or promises on behalf of the user.
- Output ONLY the draft reply text."""

class DraftReply(BaseModel):
    reply_text: str = Field(description="The draft reply text")
    needs_human_info: bool = Field(description="True if the reply needs info only the user has")

def generate_draft(msg: GmailMessage) -> DraftReply | None:
    s = get_settings()
    try:
        completion = _client().beta.chat.completions.parse(
            model=s.llm_model,
            temperature=s.llm_temperature + 0.2, # slightly more creative for drafting
            messages=[
                {"role": "system", "content": DRAFT_PROMPT},
                {"role": "user", "content": f"From: {msg.sender}\nSubject: {msg.subject}\nBody: {msg.body}"},
            ],
            response_format=DraftReply,
        )
        return completion.choices[0].message.parsed
    except Exception as exc:
        log.exception("draft generation failed for %s", msg.id)
        return None
