# Chatwoot ↔ Zoho Desk + Team Auto-Routing Bridge

Small Python/FastAPI service that does **two things** for Chatwoot:

1. **Team auto-assignment** — When the first incoming message of a new
   conversation arrives (email, IG DM, FB comment, webchat, anything),
   classify it via OpenAI into one of: `legal | marketing | hr | support`,
   and assign the matching Chatwoot Team.
2. **Zoho ticketing** — When the DM bot hands off a conversation to a human
   (status `pending → open`), create a Zoho Desk ticket with the transcript.

Runs on **port 8420**.

---

## Project layout

```
zoho-bridge/
├── main.py          # FastAPI app + webhook dispatcher (~100 lines)
├── config.py        # Env loading, fails fast on missing values
├── classifier.py    # OpenAI team classifier (single-word output, falls back to 'support')
├── chatwoot.py      # Chatwoot Application API client (assign team, add label)
├── zoho.py          # Zoho OAuth + ticket creation
├── requirements.txt
├── .env.example
├── .env             # YOUR secrets (gitignore this)
└── venv/            # python venv (gitignore this)
```

Adding a new handler later (e.g., Slack notification on resolve)?
→ Drop a function in `main.py` and add it to the `HANDLERS` dict. Done.

---

## 1. One-time setup

### 1a. Python deps

```bash
cd "/Users/adityasingh/Desktop/GHT/Durian ORM/zoho-bridge"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 1b. Zoho Desk credentials

In Zoho API Console (https://api-console.zoho.in):

1. **Add Client** → **Self Client** (purple icon).
2. **Client Secret** tab → copy **Client ID** + **Client Secret**.
3. **Generate Code** tab → scope = `Desk.tickets.CREATE,Desk.contacts.CREATE,Desk.contacts.READ`, 10 min duration → copy the code.
4. Exchange for refresh token:
   ```bash
   curl -X POST "https://accounts.zoho.in/oauth/v2/token" \
     -d "code=PASTE_CODE_HERE" \
     -d "client_id=PASTE_CLIENT_ID" \
     -d "client_secret=PASTE_CLIENT_SECRET" \
     -d "grant_type=authorization_code"
   ```
   → copy `refresh_token` (long-lived).
5. **Org ID:** Zoho Desk → Setup → Developer Space → API.
6. **Department ID:** Setup → General → Departments → URL has `/dept/<id>`.

### 1c. Chatwoot teams

In Chatwoot (http://localhost:3000) as admin:

1. **Settings → Teams → Add new** — create four teams with these exact names:
   - `Legal`
   - `Marketing`
   - `HR`
   - `Support`
2. Add at least one agent to each (for demo, can be the same user).

### 1d. Chatwoot API token

1. Top-right profile picture → **Profile Settings**.
2. Scroll to bottom → **Access Token** → copy.
3. (Optional but cleaner for production: create a dedicated bot agent in
   **Settings → Agents** and use that agent's token instead of your own.)

### 1e. Find your team IDs

After creating the teams, grab their numeric IDs:

```bash
curl -H "api_access_token: PASTE_TOKEN_HERE" \
     http://localhost:3000/api/v1/accounts/1/teams
```

Output is a JSON array — note the `id` for each name.

### 1f. OpenAI API key

https://platform.openai.com/api-keys → create new key → copy. Defaults to
`gpt-4o-mini` (cheap, fast, plenty good for classification).

### 1g. `.env` file

```bash
cp .env.example .env
```

Fill in all values. The bridge will refuse to start if any required var is missing.

### 1h. (Optional) Zoho ticket custom field

Setup → Customization → Layouts and Fields → Tickets → add a Single Line field
named exactly `cf_chatwoot_conversation_id`. Lets you search Zoho tickets by
Chatwoot conversation. Skip if not needed — Zoho silently ignores the `cf` block.

### 1i. Register the Chatwoot webhook

1. Chatwoot → **Settings → Integrations → Webhooks → + New**.
2. **URL:** `http://localhost:8420/chatwoot/webhook`
3. **Subscribed Events:** check BOTH:
   - ✅ **Message Created** (for team classification)
   - ✅ **Conversation Status Changed** (for Zoho ticket on handoff)
4. Save.

> Localhost works because of the `local_url?` patch in
> `chatwoot/lib/webhooks/trigger.rb`. Don't revert that file.

---

## 2. Running

```bash
cd "/Users/adityasingh/Desktop/GHT/Durian ORM/zoho-bridge"
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8420
```

Health check:
```bash
curl http://localhost:8420/health
# {"ok":true}
```

In the background:
```bash
nohup uvicorn main:app --host 127.0.0.1 --port 8420 > bridge.log 2>&1 &
```

Stop: `pkill -f "uvicorn main:app"`

Auto-start with overmind — add to `chatwoot/Procfile.dev`:
```
zoho_bridge: cd ../zoho-bridge && source venv/bin/activate && uvicorn main:app --host 127.0.0.1 --port 8420
```

---

## 3. Testing end-to-end

### Team auto-assignment
1. Email the inbox: subject `Copyright takedown notice`, body `Please remove our image immediately.`
2. Within ~1 second the conversation should appear assigned to the **Legal** team in Chatwoot.
3. Try `I want to apply for a job` → HR. `Brand collaboration` → Marketing. `Where's my order?` → Support.

### Zoho handoff
1. DM the IG account, pick "👤 Talk to a Human" from the bot menu.
2. Bridge logs: `POST /chatwoot/webhook → 200 {"created": true, ...}`.
3. New ticket in Zoho Desk with the full transcript and the Chatwoot team name appended.

---

## 4. How filtering works

**Team classification** runs ONLY when:
- Event is `message_created`
- Message is incoming (from customer, not agent/bot)
- It's the **first** incoming message in the conversation
- Conversation has no team assigned yet

**Zoho ticket** is created ONLY when:
- Event is `conversation_status_changed`
- New status is `open` (which is what `bot_handoff!` triggers)

Both filters are at the top of their handlers in `main.py` — easy to tweak.

---

## 5. Customizing

| Want to... | Edit... |
|---|---|
| Change routing rules (what triggers Legal vs HR) | `classifier.py` → `SYSTEM_PROMPT` |
| Add a 5th team (e.g., `finance`) | `classifier.py` (add to prompt + `VALID_TEAMS`), `config.py` (add `TEAM_ID_FINANCE`), `.env` |
| Use a smarter / different OpenAI model | `.env` → `OPENAI_MODEL=gpt-4o` |
| Also tag conversations with a label | Call `chatwoot.add_label(conv_id, team_key)` in `handle_message_created` |
| Send Zoho ticket to different department by team | In `zoho.py` `_build_ticket_body`, map team name → `departmentId` |
| Trigger Zoho ticket on resolve too | `main.py` → `handle_status_changed`, accept `resolved` in addition to `open` |
| Add Slack notifications | New `slack.py` client + new handler in `main.py` + add to `HANDLERS` dict |

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `RuntimeError: Missing required env var: ...` at startup | Fill the missing value in `.env` |
| `Zoho token refresh failed: invalid_code` | Auth code expired (10 min) — regenerate |
| `Zoho ticket create failed [403] INVALID_OAUTH_SCOPE` | Refresh token missing scope — regenerate with all 3 scopes |
| `Chatwoot assign_team failed [401]` | `CHATWOOT_API_TOKEN` wrong — re-copy from Profile Settings |
| `Chatwoot assign_team failed [404]` | Wrong `CHATWOOT_ACCOUNT_ID` (usually `1` on self-hosted single-account installs) |
| Bridge gets request but ignores it | Check the response `reason` — usually `not_first_incoming` or `team_already_set`, both are normal |
| All conversations classified as `support` | Either the LLM call is failing (check stdout), or `OPENAI_API_KEY` is invalid |
| Classifier picks wrong team consistently | Edit `SYSTEM_PROMPT` in `classifier.py` — add examples or tighten rules |
| Chatwoot log: `Invalid webhook URL ... no public ip` | `lib/webhooks/trigger.rb` patch reverted — restore the `local_url?` method |
| Tickets created twice | Subscribed to too many events — only `Conversation Status Changed` and `Message Created` should be checked |

---

## 7. Architecture notes

- **All external clients are async (`httpx.AsyncClient`)** — no blocking I/O.
- **Token cache is in-memory** (Zoho access token). On restart the bridge does
  one refresh call (~200ms) and is good for the next hour. If you scale to
  multiple replicas, move this to Redis.
- **OpenAI failures degrade gracefully** to `support`. Classification never
  blocks a conversation.
- **Zoho failures return HTTP 200** to Chatwoot to prevent retry storms.
  Errors go to stdout — pipe `bridge.log` to your log aggregator in prod.
- **No DB**, no state. The bridge can be killed and restarted any time
  without losing data — Chatwoot is the source of truth.

---

## 8. Files to add to `.gitignore`

If/when this folder becomes a git repo:

```
.env
venv/
bridge.log
__pycache__/
*.pyc
```
