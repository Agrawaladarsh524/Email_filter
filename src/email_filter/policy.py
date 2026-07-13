"""Categories, the LLM's output schema, and the category -> action policy table."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Category(StrEnum):
    URGENT = "urgent"
    ACTION_NEEDED = "action_needed"
    NEWSLETTER = "newsletter"
    SPAM = "spam"


class ActionItem(BaseModel):
    task: str = Field(description="What needs to be done, in one sentence")
    deadline: str | None = Field(description="Due date if mentioned, ISO format, or null")
    priority: str = Field(description="high, medium, or low")


class Classification(BaseModel):
    """Structured output from the classifier.

    Routing reads `category`, an enum -- not a substring of free text -- so a
    chatty model can't silently send mail down the wrong branch.
    """

    category: Category = Field(description="The single best-fitting category.")
    reason: str = Field(description="One sentence justifying the category.")
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 self-reported confidence.")
    summary: str = Field(default="", description="1-2 sentence summary of the email")
    action_items: list[ActionItem] = Field(
        default_factory=list,
        description="Concrete tasks the recipient needs to do. Empty if none."
    )


@dataclass(frozen=True)
class CategoryPolicy:
    """What each category does to the message, and who hears about it."""

    label: str  # Gmail label name; created on demand if absent.
    remove_labels: tuple[str, ...] = ()  # System label IDs, e.g. UNREAD / INBOX.
    notify_slack: bool = False
    notify_telegram: bool = False
    auto_draft: bool = False

    @property
    def notifies(self) -> bool:
        return self.notify_slack or self.notify_telegram


POLICIES: dict[Category, CategoryPolicy] = {
    Category.URGENT: CategoryPolicy(
        label="AI/Urgent",
        remove_labels=(),
        notify_slack=True,
        notify_telegram=True,
        auto_draft=True,
    ),
    Category.ACTION_NEEDED: CategoryPolicy(
        label="AI/Action",
        remove_labels=(),
        notify_slack=True,
        auto_draft=True,
    ),
    Category.NEWSLETTER: CategoryPolicy(
        label="AI/Newsletter",
        remove_labels=("INBOX",),  # archive
    ),
    Category.SPAM: CategoryPolicy(
        label="AI/Spam",
        remove_labels=("INBOX",),
    ),
}

# Used when the classifier errors out or returns something unusable: label it
# for review and leave the message untouched in the inbox.
FALLBACK_POLICY = CategoryPolicy(label="AI/Unclassified")
REVIEW_POLICY = CategoryPolicy(label="AI/ReviewNeeded")


def policy_for(category: Category | None) -> CategoryPolicy:
    return POLICIES.get(category, FALLBACK_POLICY) if category else FALLBACK_POLICY


@dataclass
class TriageResult:
    """What happened to one message. Returned by the pipeline, served by the API."""

    message_id: str
    subject: str = ""
    sender: str = ""
    category: Category | None = None
    classification_method: str = "llm"
    reason: str = ""
    confidence: float = 0.0
    labels_added: list[str] = field(default_factory=list)
    labels_removed: list[str] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "subject": self.subject,
            "sender": self.sender,
            "category": str(self.category) if self.category else None,
            "reason": self.reason,
            "confidence": self.confidence,
            "labels_added": self.labels_added,
            "labels_removed": self.labels_removed,
            "notifications": self.notifications,
            "errors": self.errors,
        }
