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
# CHATWOOT_BASE_URL is the address the bridge USES INTERNALLY to call the
# Chatwoot API. On a single-VM deployment this is `http://localhost:3000`
# (faster, no SSL handshake, no DNS) and that's the default.
#
# CHATWOOT_PUBLIC_URL is the USER-FACING address — what agents type into
# their browser to reach the dashboard (e.g., `https://orm.durianos.in`).
# This is the URL embedded in deep-links the bridge writes into other
# systems — the "Open conversation in Chatwoot" link in Zoho ticket
# descriptions, future webhook callback URLs, etc. It MUST be the public
# URL — a localhost link inside a Zoho ticket is useless to anyone who
# isn't SSH'd into the VM.
#
# Falls back to CHATWOOT_BASE_URL if unset, so existing dev setups where
# localhost IS the public address (e.g. running everything on a laptop)
# keep working without a config change.
CHATWOOT_BASE_URL       = os.environ.get("CHATWOOT_BASE_URL", "http://localhost:3000")
CHATWOOT_PUBLIC_URL     = os.environ.get("CHATWOOT_PUBLIC_URL", CHATWOOT_BASE_URL)
CHATWOOT_API_TOKEN      = _required("CHATWOOT_API_TOKEN")
CHATWOOT_ACCOUNT_ID     = int(os.environ.get("CHATWOOT_ACCOUNT_ID", "1"))
CHATWOOT_WEBHOOK_SECRET = os.environ.get("CHATWOOT_WEBHOOK_SECRET", "")

# ── OpenAI (used for team classification) ─────────────────────────────────
OPENAI_API_KEY = _required("OPENAI_API_KEY")
OPENAI_MODEL   = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# ── Document extraction (bills / receipts / order screenshots) ────────────
# Kill-switch for the attachment/bill extraction pipeline. Vision calls on
# images are the priciest LLM calls the bridge makes (~$0.002-0.01 each vs
# ~$0.0002 for a classification) — gated by attachment presence / a regex
# text filter, but if costs ever surprise you, flip this off and restart.
def _bool_early(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


DOC_EXTRACTION_ENABLED = _bool_early("DOC_EXTRACTION_ENABLED", "true")
# Must be a VISION-capable model (gpt-4o-mini / gpt-4o). Defaults to the
# shared OPENAI_MODEL, override independently if you ever split models.
DOC_EXTRACTION_MODEL = os.environ.get("DOC_EXTRACTION_MODEL", OPENAI_MODEL)

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

# How long (minutes) to suppress duplicate priority-escalation tickets for
# the same conversation. Two roles:
#
#  1. INFINITE-LOOP GUARD (mandatory): when the bridge creates a Zoho ticket
#     it writes custom_attributes back to the conversation, which fires
#     ANOTHER conversation_updated webhook within milliseconds. Without this
#     cooldown the handler would create a fresh ticket on every loop
#     iteration — the bug fixed in PR #8.
#
#  2. DEDUP WINDOW (intended): if an agent bumps priority high → urgent
#     within the window, don't fire a second ticket — the original one is
#     still actively being worked. Outside the window (e.g. conversation
#     resolved months ago, now re-opened + flagged urgent), a fresh ticket
#     IS what we want.
#
# Default 60 minutes — generous enough to handle role 1 reliably and tight
# enough that genuine re-escalations weeks/months later land a new ticket.
# Setting this below ~1 minute risks re-enabling the loop (Chatwoot can
# retry webhooks for up to that long); guard against it with max(1, ...).
PRIORITY_ESCALATION_COOLDOWN_MINUTES = max(
    1, int(os.environ.get("PRIORITY_ESCALATION_COOLDOWN_MINUTES", "60"))
)

# ── Human-in-the-loop Zoho ticket approval ─────────────────────────────────
# When true (DEFAULT), the bridge NEVER auto-creates a Zoho ticket. Every
# escalation path (categorizer complaint/legal, priority bump, manual handoff,
# Option-D signal) instead pauses and asks an agent to Approve / Attach-to-
# existing / Reject via the Pending Ticket Decision panel in the sidebar.
# Set ZOHO_TICKET_REQUIRE_APPROVAL=false to restore fully-automatic ticket
# creation (e.g. once out of the prod-test phase).
ZOHO_TICKET_REQUIRE_APPROVAL = _bool("ZOHO_TICKET_REQUIRE_APPROVAL", "true")
