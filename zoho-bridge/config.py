# Centralised env loading. Fails fast at startup if anything required is missing.

import os
from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


# ── Zoho Desk ─────────────────────────────────────────────────────────────
ZOHO_CLIENT_ID     = _required("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = _required("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = _required("ZOHO_REFRESH_TOKEN")
ZOHO_ORG_ID        = _required("ZOHO_ORG_ID")
ZOHO_DEPARTMENT_ID = _required("ZOHO_DEPARTMENT_ID")
ZOHO_ACCOUNTS_URL  = os.environ.get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.in")
ZOHO_DESK_URL      = os.environ.get("ZOHO_DESK_URL", "https://desk.zoho.in")

# ── Chatwoot ──────────────────────────────────────────────────────────────
CHATWOOT_BASE_URL       = os.environ.get("CHATWOOT_BASE_URL", "http://localhost:3000")
CHATWOOT_API_TOKEN      = _required("CHATWOOT_API_TOKEN")
CHATWOOT_ACCOUNT_ID     = int(os.environ.get("CHATWOOT_ACCOUNT_ID", "1"))
CHATWOOT_WEBHOOK_SECRET = os.environ.get("CHATWOOT_WEBHOOK_SECRET", "")

# ── OpenAI (used for team classification) ─────────────────────────────────
OPENAI_API_KEY = _required("OPENAI_API_KEY")
OPENAI_MODEL   = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# ── Team routing (Chatwoot team IDs) ──────────────────────────────────────
# Find IDs with: curl -H "api_access_token: $TOKEN" http://localhost:3000/api/v1/accounts/1/teams
TEAM_IDS = {
    "legal":     int(_required("TEAM_ID_LEGAL")),
    "marketing": int(_required("TEAM_ID_MARKETING")),
    "hr":        int(_required("TEAM_ID_HR")),
    "support":   int(_required("TEAM_ID_SUPPORT")),
}


# ── Google Reviews (OPTIONAL) ──────────────────────────────────────────────
# All optional so the service still boots before GBP API access is granted.
# Set GOOGLE_REVIEWS_ENABLED=true once you have credentials + the inbox.
def _bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


GOOGLE_REVIEWS_ENABLED = _bool("GOOGLE_REVIEWS_ENABLED")
GOOGLE_CLIENT_ID       = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET   = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN   = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
GOOGLE_TOKEN_URL       = os.environ.get("GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token")
# Optional: pin a single GBP account id (e.g. "accounts/123"); blank = auto-discover all.
GBP_ACCOUNT_ID         = os.environ.get("GBP_ACCOUNT_ID", "")

# Chatwoot API-channel inbox that holds reviews (create it once — see setup guide).
REVIEWS_INBOX_ID       = int(os.environ.get("REVIEWS_INBOX_ID", "0") or 0)
# Team to route handed-off (negative) reviews to. Defaults to the support team.
REVIEWS_TEAM_ID        = int(os.environ.get("REVIEWS_TEAM_ID", "0") or 0) or TEAM_IDS.get("support", 0)

REVIEWS_POLL_INTERVAL_SECONDS = int(os.environ.get("REVIEWS_POLL_INTERVAL_SECONDS", "300"))
REVIEWS_AUTO_REPLY            = _bool("REVIEWS_AUTO_REPLY", "true")
REVIEWS_AUTO_REPLY_MIN_STARS  = int(os.environ.get("REVIEWS_AUTO_REPLY_MIN_STARS", "4"))


# ── Spam-classifier safeguards ────────────────────────────────────────────
# Defaults are conservative — the system biases toward NEVER losing a real
# customer (false-positives cost more than false-negatives).

def _csv(name: str, default: str = "") -> set[str]:
    raw = os.environ.get(name, default).strip()
    if not raw:
        return set()
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


# Inbox NAMES (lowercase, comma-separated) that bypass the classifier
# entirely. Sensitive inboxes where a mis-classification would cost real
# money (legal, complaints, escalations, abuse reports).
NEVER_SPAM_INBOXES = _csv(
    "NEVER_SPAM_INBOXES",
    "legal-notices,complaints,abuse-reports,escalations",
)

# Minimum classifier confidence (1-10) required to AUTO-SNOOZE a spam
# conversation. Below this, the message gets the "spam" label but stays in
# the open queue so an agent can verify. Default 8 = high bar.
SPAM_CONFIDENCE_THRESHOLD = int(os.environ.get("SPAM_CONFIDENCE_THRESHOLD", "8"))

# Prior NON-spam conversations a sender needs for the LOW-confidence-spam
# tiebreaker to downgrade their message to 'promotional' instead of
# leaving it labeled spam. NOT a bypass — high-confidence spam from a
# known sender still gets snoozed (compromised-account scenario).
WHITELIST_MIN_PRIOR_CONVERSATIONS = int(
    os.environ.get("WHITELIST_MIN_PRIOR_CONVERSATIONS", "1")
)

# Existing conversation ID to drop the spam-review digest into. Leave 0 to
# disable digest delivery (you can still hit /spam-digest manually).
SPAM_DIGEST_INBOX_ID = int(os.environ.get("SPAM_DIGEST_INBOX_ID", "0") or 0)

# Shared secret protecting the /spam-digest endpoint. The webhook route is
# already HMAC-verified via CHATWOOT_WEBHOOK_SECRET; this is a SEPARATE
# token for ops/cron endpoints we expose, since whoever schedules the
# digest doesn't have the webhook signing payload to sign with.
# When unset, /spam-digest refuses to run (fail-closed) rather than being
# silently open as it was pre-review.
BRIDGE_OPS_TOKEN = os.environ.get("BRIDGE_OPS_TOKEN", "")


# ── Priority-based SLA / auto-escalation ──────────────────────────────────
# When an agent flags a conversation 'urgent' or 'high', the bridge listens
# on Chatwoot's `conversation_updated` webhook and creates a Zoho Desk
# ticket with a dueDate computed from PRIORITY_SLA_HOURS.

def _parse_sla_map(raw: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        k, _, v = pair.partition(":")
        try:
            out[k.strip().lower()] = int(v.strip())
        except (TypeError, ValueError):
            continue
    return out


_DEFAULT_SLA = {"urgent": 1, "high": 4, "medium": 12, "low": 24}
PRIORITY_SLA_HOURS = {**_DEFAULT_SLA, **_parse_sla_map(os.environ.get("PRIORITY_SLA_HOURS", ""))}

# Priority levels that auto-escalate to Zoho. SINGLE source of truth — used
# by both the conversation_updated handler and the message_created Option-D
# decision (pre-review there were two separate sets, HIGH_PRIORITY_LEVELS
# and PRIORITY_ESCALATION_LEVELS, that could drift apart silently).
PRIORITY_ESCALATION_LEVELS = _csv("PRIORITY_ESCALATION_LEVELS", "urgent,high")
