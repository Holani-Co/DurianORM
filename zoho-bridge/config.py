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
