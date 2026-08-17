"""Safety and guardrails logic for the pipeline."""

from datetime import datetime, time
from fnmatch import fnmatch
from zoneinfo import ZoneInfo
import logging

from email_filter.config import get_settings

log = logging.getLogger(__name__)

def in_quiet_hours() -> bool:
    """Check if the current time is within configured quiet hours."""
    s = get_settings()
    now = datetime.now(ZoneInfo(s.quiet_hours_timezone))
    start = time.fromisoformat(s.quiet_hours_start)
    end = time.fromisoformat(s.quiet_hours_end)
    
    if start > end:  # crosses midnight (e.g. 22:00 -> 07:00)
        return now.time() >= start or now.time() < end
    return start <= now.time() < end

def is_vip(sender: str) -> bool:
    """Check if the sender is in the VIP allowlist."""
    s = get_settings()
    return any(fnmatch(sender.lower(), pattern.lower()) for pattern in s.vip_senders)
