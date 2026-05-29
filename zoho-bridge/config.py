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
