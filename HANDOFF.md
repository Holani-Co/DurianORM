# Chatwoot Handoff — IComics/Kisnemanga

## Stack
- Self-hosted Chatwoot v4.14.0, Rails 7.1, Puma, Sidekiq, Overmind
- Ruby 3.4.4 via rbenv — always use `bundle exec rails` from the chatwoot dir
- Cloudflare tunnel (ephemeral trycloudflare.com URL) for Meta webhooks
- Postgres, Redis

## Inboxes
| Name | inbox_id | channel_type | Notes |
|------|----------|--------------|-------|
| iComics | 5 | Channel::FacebookPage | Facebook Page DMs |
| kisnemanga | 6 | Channel::FacebookPage | Instagram DMs (instagram_id set on channel) |

Both inboxes: `channel_id: 1`, `account_id: 1`

## Meta Webhook Flow
- **Instagram DMs** for both inboxes → `/webhooks/instagram` → `Webhooks::InstagramEventsJob` → `Instagram::Messenger::MessageText` → `Messages::Instagram::Messenger::MessageBuilder`
- **Instagram comments** → same endpoint, field=`comments` → `Instagram::CommentService`
- **Facebook DMs** → `/bot` (facebook-messenger gem) → `Webhooks::FacebookEventsJob` → `Integrations::Facebook::MessageCreator` → `Messages::Facebook::MessageBuilder`
- **Facebook comments** → `/bot` middleware (`FacebookCommentsMiddleware`) → `Webhooks::FacebookCommentJob` → `Facebook::CommentService`

## DM Bot (AgentBot)
**Implemented and set up.** Files:
- `lib/integrations/dm_bot/processor_service.rb` — welcome menu, option routing, OpenAI fallback, HANDOFF
- `app/controllers/internal/dm_bot_controller.rb` — receives AgentBot webhook, skips comment conversations
- `config/routes.rb` — `post 'dm_bot/webhook'` inside `namespace :internal`

**AgentBot record created in DB:** `AgentBot.find_by(name: 'DM Bot')`, id=1, outgoing_url=`http://localhost:3000/internal/dm_bot/webhook`

**AgentBotInbox records created** for both inboxes (status: active).

**Bot only fires on `pending` conversations.** New conversations auto-start as `pending` when `inbox.active_bot?` is true (which it now is). Existing `open` conversations need to be resolved first so the next DM creates a fresh `pending` conversation.

**OpenAI key** read from `InstallationConfig` where `name = 'CAPTAIN_OPEN_AI_API_KEY'`.

## Conversation Type System
`additional_attributes['type']` on Conversation determines routing:
| Value | Meaning |
|-------|---------|
| `instagram_direct_message` | Instagram DM |
| `instagram_comment` | Instagram post comment |
| `facebook_comment` | Facebook post comment |
| *(none)* | Facebook DM |

`SendReplyJob` routes outgoing replies based on this field — see `app/jobs/send_reply_job.rb`.

## Bugs Fixed This Session

### 1. DM messages landing in comment conversations
**Root cause:** `Instagram::CommentService#find_or_create_conversation` had no type filter — could contaminate DM conversations. `apply_comment_label` had no guard.  
**Fix:** `app/services/instagram/comment_service.rb` — `find_or_create_conversation` now filters `type = 'instagram_comment'`; `apply_comment_label` skips non-comment conversations.  
Same fix applied to `app/services/facebook/comment_service.rb`.

Also hardened `app/builders/messages/facebook/message_builder.rb` — `dm_conversations` helper excludes comment-type convos.

### 2. Replies not routing for `instagram_comment` on FacebookPage channel
**Root cause:** `SendReplyJob#send_on_facebook_page` had no case for `instagram_comment` — fell to `else` → `Facebook::SendOnFacebookService` (Facebook DM, wrong context).  
**Fix:** `app/jobs/send_reply_job.rb` — added `when 'instagram_comment' → Instagram::CommentReplyService`.

### 3. `Instagram::CommentReplyService` only worked for `Channel::Instagram`
**Root cause:** Used `channel.access_token` (Channel::Instagram method) and `graph.instagram.com` endpoint.  
**Fix:** `app/services/instagram/comment_reply_service.rb` — now uses `instagram_access_token` helper (returns `page_access_token` for FacebookPage, `access_token` for Instagram), endpoint changed to `graph.facebook.com/v22.0`.

### 4. Instagram DM send service on deprecated API
**Root cause:** `Instagram::Messenger::SendOnInstagramService` used `graph.facebook.com/v11.0/me/messages` (deprecated ~2023) and sent body as form-encoded instead of JSON.  
**Fix:** `app/services/instagram/messenger/send_on_instagram_service.rb` — updated to v22.0, added `Content-Type: application/json`, body sent as `.to_json`.

## Current State / Remaining Work
- Restart backend after all fixes: `overmind restart backend`
- **Resolve conversation #644** (Aditya Singh / kisnemanga) — it's a contaminated mix of old comment + DM messages; resolve it so the next DM creates a clean `pending` conversation and the bot fires
- Test bot end-to-end: send a fresh DM → should get welcome menu with 4 options
- Verify comment replies work: reply in a comment conversation → should post to Instagram comment thread
- Verify DM replies work: reply in a DM conversation → should send as Instagram DM

## Key Files Modified
```
app/builders/messages/facebook/message_builder.rb
app/services/instagram/comment_service.rb
app/services/facebook/comment_service.rb
app/jobs/send_reply_job.rb
app/services/instagram/comment_reply_service.rb
app/services/instagram/messenger/send_on_instagram_service.rb
lib/integrations/dm_bot/processor_service.rb        (new)
app/controllers/internal/dm_bot_controller.rb       (new)
config/routes.rb                                    (added internal namespace)
```

## Zoho Desk Bridge (sibling folder)

Small Python/FastAPI service at `../zoho-bridge/` that turns bot handoffs into
Zoho Desk tickets.

- Port: **8420** (uncommon, no clashes)
- Webhook URL to register in Chatwoot: `http://localhost:8420/chatwoot/webhook`
- Trigger: `conversation_status_changed` → `open` (i.e., DM bot called `bot_handoff!`)
- Full setup in `../zoho-bridge/README.md`

Start it:
```bash
cd ../zoho-bridge && source venv/bin/activate && uvicorn main:app --host 127.0.0.1 --port 8420
```

The localhost URL works because `lib/webhooks/trigger.rb#local_url?` bypasses
the SSRF filter for `localhost`/`127.0.0.1`.

## Useful Console Commands
```ruby
# Find bot and inboxes
bot = AgentBot.find_by(name: 'DM Bot')
fb_inbox  = Inbox.find(5)
ig_inbox  = Inbox.find(6)

# Check AgentBotInbox
AgentBotInbox.where(inbox: ig_inbox).first

# Check page token
Channel::FacebookPage.find_by(inbox: ig_inbox).page_access_token

# Check conversation type
Conversation.find(644).additional_attributes
```
