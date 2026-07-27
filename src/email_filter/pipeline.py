"""Triage one message: fetch -> heuristic -> embedding -> agent -> guardrails -> execute.

This implements the cascading hybrid architecture.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from googleapiclient.errors import HttpError

from .classifier import agent_triage
from .clients import gmail, slack, telegram
from .config import get_settings
from .policy import TriageResult, policy_for, REVIEW_POLICY, CategoryPolicy
from .guardrails import is_vip, in_quiet_hours
from .heuristics import rule_based_classify
from .seen import SeenStore
from .embeddings import get_embedding
from .rag_store import RagStore

log = logging.getLogger(__name__)

GMAIL_LINK = "https://mail.google.com/mail/u/0/#inbox/{message_id}"

def format_rag_context(similar_emails: list) -> str:
    if not similar_emails:
        return "No similar past emails found."
    lines = []
    for i, ctx in enumerate(similar_emails, 1):
        lines.append(f"Email {i}: From: {ctx.sender}, Subject: '{ctx.subject}'")
        lines.append(f"  -> Was categorized as: {ctx.category} (Human verified: {ctx.is_human_corrected})")
    return "\n".join(lines)


def triage(message_id: str) -> TriageResult:
    s = get_settings()
    result = TriageResult(message_id=message_id)

    # --- fetch --------------------------------------------------------
    msg = gmail.get_message(message_id)
    result.subject = msg.subject
    result.sender = msg.sender
    log.info("fetched %s | %s | %s", msg.id, msg.sender, msg.subject[:60])

    store = SeenStore(s.seen_db)
    rag_store = RagStore(Path(str(s.seen_db).replace('seen.sqlite', 'rag.sqlite')))
    today = date.today().isoformat()

    # We will populate these variables based on the active tier
    requested_tools = []
    
    # --- Tier 1: Heuristics -------------------------------------------
    category = rule_based_classify(msg)
    if category:
        result.category = category
        result.confidence = 1.0
        result.reason = "Heuristic match"
        result.classification_method = "heuristics"
        log.info("classified %s -> %s via heuristics", message_id, category)
        # Manually create tool calls that mirror the old policy behavior
        policy = policy_for(category)
        requested_tools.append({"name": "ClassifyAndLabel", "arguments": {"category": str(category), "reason": "Heuristics", "confidence": 1.0}})
        if policy.remove_labels: requested_tools.append({"name": "ArchiveEmail", "arguments": {}})
        if policy.notify_slack: requested_tools.append({"name": "SendAlert", "arguments": {"channel": "slack", "message": f"Heuristic matched: {category}"}})
        if policy.notify_telegram: requested_tools.append({"name": "SendAlert", "arguments": {"channel": "telegram", "message": f"Heuristic matched: {category}"}})
        
    else:
        # --- Tier 2: Embedding Shortcut -------------------------------
        embedding = get_embedding(f"From: {msg.sender}\nSubject: {msg.subject}\n\n{msg.body}")
        similar_emails = rag_store.find_similar_emails(embedding, top_k=3)
        
        shortcut_applied = False
        if similar_emails and similar_emails[0].similarity > 0.95 and similar_emails[0].is_human_corrected:
            shortcut_category = similar_emails[0].category
            result.category = shortcut_category
            result.confidence = 0.95
            result.reason = f"Semantic shortcut match with past email ({similar_emails[0].similarity:.2f})"
            result.classification_method = "embeddings"
            log.info("classified %s -> %s via embeddings shortcut", message_id, shortcut_category)
            shortcut_applied = True
            
            policy = policy_for(shortcut_category)
            requested_tools.append({"name": "ClassifyAndLabel", "arguments": {"category": str(shortcut_category), "reason": result.reason, "confidence": 0.95}})
            if policy.remove_labels: requested_tools.append({"name": "ArchiveEmail", "arguments": {}})
            
        if not shortcut_applied:
            # --- Tier 3 & 4: Agent & Tool Calling (with RAG Context) --
            rag_context = format_rag_context(similar_emails)
            tools, error = agent_triage(msg, rag_context)
            if error:
                result.errors.append(error)
                result.category = "unclassified"
                result.classification_method = "fallback"
                requested_tools = []
            elif tools:
                requested_tools = tools
                result.classification_method = "llm"
                # Extract ClassifyAndLabel for base state
                for t in tools:
                    if t["name"] == "ClassifyAndLabel":
                        args = t["arguments"]
                        result.category = args.get("category")
                        result.reason = args.get("reason", "")
                        result.confidence = args.get("confidence", 0.0)
                        log.info("classified %s -> %s (%.0f%%) via agent", message_id, result.category, result.confidence * 100)
                        
                        # Save memory for next time
                        rag_store.save_email_context(message_id, msg.subject, msg.sender, result.category, embedding)

    # --- Guardrails Layer ---------------------------------------------
    accepted_tools = []
    
    for t in requested_tools:
        name = t["name"]
        args = t["arguments"]
        
        if name == "ClassifyAndLabel":
            if result.confidence < s.confidence_floor:
                log.warning("Low confidence (%.0f%%) - flagging for review", result.confidence * 100)
                accepted_tools.append({"name": "ClassifyAndLabel", "arguments": {"category": "review_needed"}})
            else:
                accepted_tools.append(t)
                
        elif name == "ArchiveEmail":
            if is_vip(msg.sender):
                log.info("VIP sender %s - preventing archive", msg.sender)
            elif store.get_action_count("archive", today) >= s.max_archives_per_day:
                log.warning("Daily archive cap reached - preventing archive")
            else:
                accepted_tools.append(t)
                
        elif name == "SendAlert":
            if in_quiet_hours() and not (result.category == "urgent" and result.confidence >= 0.95):
                log.info("Quiet hours active - suppressing alerts")
            elif store.get_action_count("alert", today) >= s.max_alerts_per_day:
                log.warning("Daily alert cap reached - suppressing alerts")
            else:
                accepted_tools.append(t)
                
        elif name == "DraftReply":
            if result.confidence < s.confidence_floor:
                log.info("Low confidence - skipping draft generation")
            elif store.get_action_count("draft", today) >= s.max_drafts_per_day:
                log.warning("Daily draft cap reached - skipping draft")
            else:
                accepted_tools.append(t)
                
        elif name == "ExtractTasks":
            # Save tasks to SQLite
            for task_item in args.get("tasks", []):
                store.add_task(message_id, task_item.get("task"), task_item.get("deadline"), task_item.get("priority", "medium"))
                
        elif name == "MarkImportant":
            accepted_tools.append(t)

    # --- Execution Layer ----------------------------------------------
    add_labels = []
    remove_labels = []
    
    for t in accepted_tools:
        name = t["name"]
        args = t["arguments"]
        
        if name == "ClassifyAndLabel":
            cat = args.get("category", result.category)
            if cat == "review_needed":
                add_labels.append(REVIEW_POLICY.label)
            else:
                # Use policy mapping to get the right label name
                add_labels.append(policy_for(cat).label)
                
        elif name == "ArchiveEmail":
            remove_labels.append("INBOX")
            if not s.dry_run:
                store.increment_action_count("archive", today)
                
        elif name == "MarkImportant":
            add_labels.append("STARRED")
            
        elif name == "DraftReply":
            reply_text = args.get("reply_text")
            if reply_text and not s.dry_run:
                gmail.create_draft(msg.thread_id, msg.sender, msg.subject, reply_text)
                store.increment_action_count("draft", today)
                
        elif name == "SendAlert":
            channel = args.get("channel", "slack")
            if channel == "slack" and s.slack_enabled:
                if not s.dry_run:
                    result.notifications.append(_send_slack(result))
                store.increment_action_count("alert", today)
            elif channel == "telegram" and s.telegram_enabled:
                if not s.dry_run:
                    result.notifications.append(_send_telegram(result))
                store.increment_action_count("alert", today)

    # Execute Gmail Labels
    if add_labels or remove_labels:
        # Deduplicate
        add_labels = list(set(add_labels))
        remove_labels = list(set(remove_labels))
        
        if s.dry_run:
            log.info("[dry-run] %s: +%s -%s", message_id, add_labels, remove_labels)
            result.labels_added, result.labels_removed = add_labels, remove_labels
        else:
            try:
                gmail.modify_labels(message_id, add=add_labels, remove=remove_labels)
                result.labels_added, result.labels_removed = add_labels, remove_labels
                log.info("labelled %s: +%s -%s", message_id, add_labels, remove_labels)
            except HttpError as exc:
                log.exception("label modify failed for %s", message_id)
                result.errors.append(f"labels: {exc!r}")

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
    f = _alert_fields(result)
    try:
        slack.send(
            text=f"{f['category'].replace('_', ' ').title()}: {f['subject']}",
            blocks=slack.build_blocks(**f),
        )
    except Exception as exc:
        log.exception("slack notify failed for %s", result.message_id)
        result.errors.append(f"slack: {exc!r}")
        return {"channel": "slack", "status": "failed"}
    return {"channel": "slack", "status": "sent"}

def _send_telegram(result: TriageResult) -> dict:
    s = get_settings()
    f = _alert_fields(result)
    try:
        telegram.send(telegram.build_message(**f))
    except Exception as exc:
        log.exception("telegram notify failed for %s", result.message_id)
        result.errors.append(f"telegram: {exc!r}")
        return {"channel": "telegram", "status": "failed"}
    return {"channel": "telegram", "status": "sent"}
