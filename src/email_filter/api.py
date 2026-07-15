"""FastAPI service: background poller + control/observability endpoints."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel

from .clients import gmail
from .config import get_settings
from .pipeline import triage
from .seen import SeenStore

log = logging.getLogger(__name__)


class PollReport(BaseModel):
    matched: int
    new: int
    processed: int
    results: list[dict[str, Any]]


class TriageRequest(BaseModel):
    message_id: str
    force: bool = False  # re-run even if already seen


class HealthReport(BaseModel):
    status: str
    gmail: bool
    slack_configured: bool
    telegram_configured: bool
    dry_run: bool
    poll_interval_seconds: int
    last_poll_at: str | None
    total_processed: int


_state: dict[str, Any] = {"last_poll_at": None, "store": None, "poller": None}


def _store() -> SeenStore:
    if _state["store"] is None:
        _state["store"] = SeenStore(get_settings().seen_db)
    return _state["store"]


def run_cycle() -> PollReport:
    """One poll: list, drop already-seen, triage each new message."""
    s = get_settings()
    store = _store()

    ids = gmail.list_message_ids(s.gmail_query, s.max_messages_per_poll)
    new_ids = store.filter_new(ids)
    log.info("poll: %d matched, %d new", len(ids), len(new_ids))

    results = []
    for message_id in new_ids:
        try:
            result = triage(message_id)
        except Exception:  # noqa: BLE001 -- one bad message must not stop the cycle
            log.exception("triage failed for %s; leaving unmarked for retry", message_id)
            continue
        # Marked only after a completed run, so a crash leaves it retryable.
        store.mark(message_id, str(result.category) if result.category else None)
        results.append(result.as_dict())

    _state["last_poll_at"] = datetime.now(timezone.utc).isoformat()
    return PollReport(
        matched=len(ids), new=len(new_ids), processed=len(results), results=results
    )


async def _poll_forever() -> None:
    s = get_settings()
    log.info("poller started | every %ds | query: %s", s.poll_interval_seconds, s.gmail_query)
    while True:
        try:
            # Blocking Google/OpenAI clients -- keep them off the event loop.
            await asyncio.to_thread(run_cycle)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- keep the loop alive
            log.exception("poll cycle failed; retrying next interval")
        await asyncio.sleep(s.poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    _store()  # create the SQLite schema up front
    if s.enable_background_poller:
        _state["poller"] = asyncio.create_task(_poll_forever())
    yield
    task = _state.get("poller")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    log.info("poller stopped")


app = FastAPI(
    title="Smart-Mail Alert",
    description="Automated Gmail triage: LLM classification, labelling, multi-channel alerts.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthReport)
def health() -> HealthReport:
    s = get_settings()
    try:
        gmail.get_service()
        gmail_ok = True
    except Exception:  # noqa: BLE001
        gmail_ok = False

    return HealthReport(
        status="ok" if gmail_ok else "degraded",
        gmail=gmail_ok,
        slack_configured=s.slack_enabled,
        telegram_configured=s.telegram_enabled,
        dry_run=s.dry_run,
        poll_interval_seconds=s.poll_interval_seconds,
        last_poll_at=_state["last_poll_at"],
        total_processed=_store().count(),
    )


@app.post("/poll", response_model=PollReport)
def poll_now() -> PollReport:
    """Force a poll cycle immediately instead of waiting for the interval."""
    return run_cycle()


@app.post("/triage")
def triage_one(req: TriageRequest) -> dict[str, Any]:
    """Triage a single message by ID -- useful for testing prompt changes."""
    if not req.force and not _store().filter_new([req.message_id]):
        raise HTTPException(409, f"{req.message_id} already processed; pass force=true")
    try:
        result = triage(req.message_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"triage failed: {exc!r}") from exc
    _store().mark(req.message_id, str(result.category) if result.category else None)
    return result.as_dict()


@app.get("/processed")
def processed(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """Recent triage history, newest first."""
    return {"items": _store().recent(limit)}


@app.get("/stats")
def stats() -> dict[str, Any]:
    """Counts per category -- how the inbox actually breaks down."""
    return {"total": _store().count(), "by_category": _store().counts_by_category()}
