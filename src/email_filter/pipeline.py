"""Triage one message: fetch -> classify -> label -> notify.

This is the whole flow. It is deliberately a plain function: the steps are
linear, the branch at the end is a table lookup, and nothing loops back. A
workflow engine would add indirection without adding capability.
"""

from __future__ import annotations

import logging

from googleapiclient.errors import HttpError

from .classifier import classify
from .clients import gmail, slack, telegram
from .config import get_settings
from .policy import TriageResult, policy_for, REVIEW_POLICY
from .guardrails import is_vip, in_quiet_hours
from .heuristics import rule_based_classify
from .drafter import generate_draft
from .seen import SeenStore
from datetime import date

log = logging.getLogger(__name__)

GMAIL_LINK = "https://mail.google.com/mail/u/0/#inbox/{message_id}"


def triage(message_id: str) -> TriageResult:
    s = get_settings()
    result = TriageResult(message_id=message_id)

    # --- fetch --------------------------------------------------------
    msg = gmail.get_message(message_id)
    result.subject = msg.subject
    result.sender = msg.sender
    log.info("fetched %s | %s | %s", msg.id, msg.sender, msg.subject[:60])

    store = SeenStore(s.seen_db)
    today = date.today().isoformat()

    # --- classify -----------------------------------------------------
    category = rule_based_classify(msg)
    if category:
        result.category = category
        result.confidence = 1.0
        result.reason = "Heuristic match"
        result.classification_method = "heuristics"
        log.info("classified %s -> %s via heuristics", message_id, category)
    else:
        classification, error = classify(msg)
        if error:
            result.errors.append(error)
        if classification:
            result.category = classification.category
            result.reason = classification.reason
            result.confidence = classification.confidence
            result.classification_method = "llm"
            log.info(
                "classified %s -> %s (%.0f%%) %s",
                message_id,
                classification.category,
                classification.confidence * 100,
                classification.reason,
            )
            for task in classification.action_items:
                store.add_task(message_id, task.task, task.deadline, task.priority)

    policy = policy_for(result.category)

    # --- guardrails ---------------------------------------------------
    if result.classification_method == "llm" and result.confidence < s.confidence_floor:
        log.warning("Low confidence (%.0f%%) for %s - flagging for review", result.confidence * 100, message_id)
        policy = REVIEW_POLICY
    
    if policy.remove_labels and is_vip(msg.sender):
        log.info("VIP sender %s - preventing archive for %s", msg.sender, message_id)
        policy = policy._replace(remove_labels=())

    if policy.remove_labels:
        if store.get_action_count("archive", today) >= s.max_archives_per_day:
            log.warning("Daily archive cap reached - preventing archive for %s", message_id)
            policy = policy._replace(remove_labels=())
        elif not s.dry_run:
            store.increment_action_count("archive", today)

    if policy.notifies and in_quiet_hours():
        if not (result.category == "urgent" and result.confidence >= 0.95):
            log.info("Quiet hours active - suppressing alerts for %s", message_id)
            policy = policy._replace(notify_slack=False, notify_telegram=False)

    if policy.notifies:
        if store.get_action_count("alert", today) >= s.max_alerts_per_day:
            log.warning("Daily alert cap reached - suppressing alerts for %s", message_id)
            policy = policy._replace(notify_slack=False, notify_telegram=False)
        elif not s.dry_run:
            store.increment_action_count("alert", today)

    # --- draft --------------------------------------------------------
    if getattr(policy, "auto_draft", False) and result.confidence >= s.confidence_floor:
        if store.get_action_count("draft", today) >= s.max_drafts_per_day:
            log.warning("Daily draft cap reached - skipping draft for %s", message_id)
        else:
            log.info("Generating draft for %s", message_id)
            if not s.dry_run:
                draft = generate_draft(msg)
                if draft:
                    gmail.create_draft(msg.thread_id, msg.sender, msg.subject, draft.reply_text)
                    store.increment_action_count("draft", today)


    # --- label --------------------------------------------------------
    add = [policy.label]
    remove = list(policy.remove_labels)
    if s.dry_run:
        log.info("[dry-run] %s: +%s -%s", message_id, add, remove)
        result.labels_added, result.labels_removed = add, remove
    else:
        try:
            # One modify call takes both adds and removes.
            gmail.modify_labels(message_id, add=add, remove=remove)
            result.labels_added, result.labels_removed = add, remove
            log.info("labelled %s: +%s -%s", message_id, add, remove)
        except HttpError as exc:
            log.exception("label modify failed for %s", message_id)
            result.errors.append(f"labels: {exc!r}")

    # --- notify -------------------------------------------------------
    if policy.notify_slack:
        result.notifications.append(_send_slack(result))
    if policy.notify_telegram:
        result.notifications.append(_send_telegram(result))

    return result


def _alert_fields(result: TriageResult) -> dict:
    return {
        "category": str(result.category) if result.category else "unclassified",
        "subject": result.subject or "(no subject)",
        "sender": result.sender or "(unknown)",
        "reason": result.reason,
        "confidence": result.confidence,
        "link": GMAIL_LINK.format(message_id=result.message_id),
    }


def _send_slack(result: TriageResult) -> dict:
    s = get_settings()
    if not s.slack_enabled:
        return {"channel": "slack", "status": "skipped:unconfigured"}
    if s.dry_run:
        log.info("[dry-run] slack <- %s", result.subject[:60])
        return {"channel": "slack", "status": "skipped:dry_run"}

    f = _alert_fields(result)
    try:
        slack.send(
            text=f"{f['category'].replace('_', ' ').title()}: {f['subject']}",
            blocks=slack.build_blocks(**f),
        )
    except Exception as exc:  # noqa: BLE001 -- a down channel must not fail triage
        log.exception("slack notify failed for %s", result.message_id)
        result.errors.append(f"slack: {exc!r}")
        return {"channel": "slack", "status": "failed"}

    return {"channel": "slack", "status": "sent"}


def _send_telegram(result: TriageResult) -> dict:
    s = get_settings()
    if not s.telegram_enabled:
        return {"channel": "telegram", "status": "skipped:unconfigured"}
    if s.dry_run:
        log.info("[dry-run] telegram <- %s", result.subject[:60])
        return {"channel": "telegram", "status": "skipped:dry_run"}

    f = _alert_fields(result)
    try:
        telegram.send(telegram.build_message(**f))
    except Exception as exc:  # noqa: BLE001
        log.exception("telegram notify failed for %s", result.message_id)
        result.errors.append(f"telegram: {exc!r}")
        return {"channel": "telegram", "status": "failed"}

    return {"channel": "telegram", "status": "sent"}
