"""Fast rule-based classification."""

from email_filter.clients.gmail import GmailMessage
from email_filter.policy import Category

# Known newsletters, marketing patterns
NEWSLETTER_SENDERS = ["*@medium.com", "*@substack.com", "newsletter@*", "marketing@*", "noreply@*"]

def rule_based_classify(msg: GmailMessage) -> Category | None:
    """Fast deterministic classification before hitting the LLM."""
    
    sender_lower = msg.sender.lower()
    
    # Check newsletter patterns
    from fnmatch import fnmatch
    if any(fnmatch(sender_lower, pattern.lower()) for pattern in NEWSLETTER_SENDERS):
        return Category.NEWSLETTER
        
    body_lower = msg.body.lower()
    if "unsubscribe" in body_lower and ("newsletter" in sender_lower or "noreply" in sender_lower):
        return Category.NEWSLETTER
        
    return None
