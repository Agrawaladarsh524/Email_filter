"""Gmail API wrapper: OAuth, message fetch/parse, label management."""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from html import unescape
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..config import get_settings

log = logging.getLogger(__name__)

# modify == read + label. gmail.readonly cannot apply labels; gmail.labels
# alone cannot read bodies. This is the narrowest scope that does both.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINE_RE = re.compile(r"\n{3,}")


@dataclass
class GmailMessage:
    id: str
    thread_id: str
    subject: str
    sender: str
    recipient: str
    body: str
    received_at: str
    label_ids: list[str]


def _authorize() -> Credentials:
    s = get_settings()
    creds: Credentials | None = None

    if s.google_token_file.exists():
        creds = Credentials.from_authorized_user_file(str(s.google_token_file), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not s.google_credentials_file.exists():
            raise FileNotFoundError(
                f"Missing OAuth client file at {s.google_credentials_file}. "
                "Create a Desktop OAuth client in Google Cloud Console, download "
                "it, and save it there."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(s.google_credentials_file), SCOPES
        )
        creds = flow.run_local_server(port=0)

    s.google_token_file.parent.mkdir(parents=True, exist_ok=True)
    s.google_token_file.write_text(creds.to_json())
    return creds


@lru_cache(maxsize=1)
def get_service():
    """Built once per process; the client is thread-unsafe, so keep it to one caller."""
    return build("gmail", "v1", credentials=_authorize(), cache_discovery=False)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def list_message_ids(query: str, limit: int) -> list[str]:
    resp = (
        get_service()
        .users()
        .messages()
        .list(userId=get_settings().gmail_user_id, q=query, maxResults=limit)
        .execute()
    )
    return [m["id"] for m in resp.get("messages", [])]


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")


def _walk_parts(payload: dict[str, Any]) -> tuple[str, str]:
    """Depth-first search for the best text/plain and text/html bodies."""
    plain, html = "", ""
    stack = [payload]
    while stack:
        part = stack.pop()
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            if mime == "text/plain" and not plain:
                plain = _decode(data)
            elif mime == "text/html" and not html:
                html = _decode(data)
        stack.extend(part.get("parts", []))
    return plain, html


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    return unescape(text)


def _normalize(text: str) -> str:
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINE_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def get_message(message_id: str) -> GmailMessage:
    s = get_settings()
    raw = (
        get_service()
        .users()
        .messages()
        .get(userId=s.gmail_user_id, id=message_id, format="full")
        .execute()
    )

    payload = raw.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

    plain, html = _walk_parts(payload)
    body = _normalize(plain or _html_to_text(html)) or raw.get("snippet", "")
    if len(body) > s.body_char_limit:
        body = body[: s.body_char_limit] + "\n...[truncated]"

    return GmailMessage(
        id=raw["id"],
        thread_id=raw.get("threadId", ""),
        subject=headers.get("subject", "(no subject)"),
        sender=headers.get("from", "(unknown)"),
        recipient=headers.get("to", ""),
        body=body,
        received_at=headers.get("date", ""),
        label_ids=raw.get("labelIds", []),
    )


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------

_label_cache: dict[str, str] = {}


def _refresh_label_cache() -> None:
    resp = (
        get_service()
        .users()
        .labels()
        .list(userId=get_settings().gmail_user_id)
        .execute()
    )
    _label_cache.clear()
    for label in resp.get("labels", []):
        _label_cache[label["name"]] = label["id"]


def ensure_label(name: str) -> str:
    """Resolve a label name to its ID, creating the label if it doesn't exist."""
    if not _label_cache:
        _refresh_label_cache()
    if name in _label_cache:
        return _label_cache[name]

    try:
        created = (
            get_service()
            .users()
            .labels()
            .create(
                userId=get_settings().gmail_user_id,
                body={
                    "name": name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
    except HttpError as exc:
        # 409: another worker created it between our lookup and our create.
        if exc.resp.status == 409:
            _refresh_label_cache()
            return _label_cache[name]
        raise

    _label_cache[name] = created["id"]
    return created["id"]


def modify_labels(
    message_id: str, add: list[str] | None = None, remove: list[str] | None = None
) -> dict[str, Any]:
    """Apply adds and removes in one request.

    The n8n flow used two nodes for this; the API takes both in a single
    modify call, so splitting it would only cost an extra round-trip.
    """
    body: dict[str, list[str]] = {}
    if add:
        body["addLabelIds"] = [ensure_label(n) for n in add]
    if remove:
        # System labels (INBOX, UNREAD) are their own IDs.
        body["removeLabelIds"] = [
            n if n.isupper() else ensure_label(n) for n in remove
        ]
    if not body:
        return {}

    return (
        get_service()
        .users()
        .messages()
        .modify(userId=get_settings().gmail_user_id, id=message_id, body=body)
        .execute()
    )


# --------------------------------------------------------------------------
# Drafts
# --------------------------------------------------------------------------

def create_draft(thread_id: str, recipient: str, subject: str, reply_text: str) -> dict[str, Any]:
    """Creates a draft reply to an email thread."""
    from email.message import EmailMessage
    
    message = EmailMessage()
    message.set_content(reply_text)
    message["To"] = recipient
    message["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    
    draft_body = {
        "message": {
            "raw": encoded_message,
            "threadId": thread_id
        }
    }
    
    return (
        get_service()
        .users()
        .drafts()
        .create(userId=get_settings().gmail_user_id, body=draft_body)
        .execute()
    )
