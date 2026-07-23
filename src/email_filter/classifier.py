"""LLM agent via OpenAI tool calling."""

from __future__ import annotations

import logging
import json
from functools import lru_cache
from typing import Any

from openai import OpenAI
from openai import pydantic_function_tool
from pydantic import BaseModel, Field

from .clients.gmail import GmailMessage
from .config import get_settings

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
subject line is still a newsletter.

Use your tools to process the email. You must ALWAYS use `ClassifyAndLabel`.
Depending on the context, you may also use other tools like `ArchiveEmail`, `DraftReply`, etc.

[PERSONAL MEMORY / RAG CONTEXT]
{rag_context}
"""

USER_TEMPLATE = """From: {sender}
To: {recipient}
Date: {received_at}
Subject: {subject}

{body}"""

# Tool schemas
class ClassifyAndLabel(BaseModel):
    """Classifies the email and assigns a label."""
    category: str = Field(description="The single best-fitting category (urgent, action_needed, newsletter, spam).")
    reason: str = Field(description="One sentence justifying the category.")
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 self-reported confidence.")
    summary: str = Field(default="", description="1-2 sentence summary of the email")

class DraftReply(BaseModel):
    """Creates a draft reply to the email."""
    reply_text: str = Field(description="The text content of the reply.")

class SendAlert(BaseModel):
    """Sends a notification alert to the user."""
    channel: str = Field(description="The channel to send the alert to: 'slack' or 'telegram'.")
    message: str = Field(description="The alert message to send.")

class ArchiveEmail(BaseModel):
    """Archives the email (removes it from the inbox). Use for unimportant or resolved emails."""
    pass

class MarkImportant(BaseModel):
    """Marks the email as important / starred. Use for highly critical emails."""
    pass

class TaskItem(BaseModel):
    task: str
    deadline: str | None
    priority: str

class ExtractTasks(BaseModel):
    """Extracts concrete action items and deadlines from the email."""
    tasks: list[TaskItem] = Field(description="List of tasks extracted from the email.")

@lru_cache(maxsize=1)
def _client() -> OpenAI:
    s = get_settings()
    return OpenAI(api_key=s.openai_api_key or None, timeout=30.0, max_retries=2)

def agent_triage(msg: GmailMessage, rag_context: str = "") -> tuple[list[dict[str, Any]] | None, str | None]:
    """Return (list of tool calls, error). Exactly one is non-None."""
    s = get_settings()
    try:
        tools = [
            pydantic_function_tool(ClassifyAndLabel),
            pydantic_function_tool(DraftReply),
            pydantic_function_tool(SendAlert),
            pydantic_function_tool(ArchiveEmail),
            pydantic_function_tool(MarkImportant),
            pydantic_function_tool(ExtractTasks)
        ]
        
        system_content = SYSTEM_PROMPT.format(rag_context=rag_context if rag_context else "No past memories found.")
        
        completion = _client().chat.completions.create(
            model=s.llm_model,
            temperature=s.llm_temperature,
            messages=[
                {"role": "system", "content": system_content},
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
            tools=tools,
            tool_choice="required"
        )
        
        message = completion.choices[0].message
        
        if not message.tool_calls:
            return None, "agent_triage: model returned no tool calls"
            
        parsed_tools = []
        for tool_call in message.tool_calls:
            try:
                args = json.loads(tool_call.function.arguments)
                parsed_tools.append({
                    "name": tool_call.function.name,
                    "arguments": args
                })
            except Exception as e:
                log.warning(f"Failed to parse tool call arguments for {tool_call.function.name}: {e}")
                
        return parsed_tools, None
    except Exception as exc:
        log.exception("agent triage failed for %s", msg.id)
        return None, f"agent_triage: {exc!r}"
