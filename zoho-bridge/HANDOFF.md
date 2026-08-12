# HANDOFF — Agent Mode (Instagram AI) + Test Harness

**Written 2026-08-13 by the session that built agent-mode v1.** The next agent
continues from here; the immediate next phase is **more comprehensive test
cases** (see §8). Read this whole file before touching anything.

---

## 0 · State in one paragraph

Agent mode — a skills-based AI loop replacing the legacy gate/drafter chain for
Instagram DMs + comments — is **built, scenario-tested on two models, and
entirely UNCOMMITTED and UNDEPLOYED**. `git status` shows all of it as new /
modified working-tree files on `main`. Nothing has shipped; prod still runs the
legacy pipeline (which also lives in this repo and must not regress). Latest
verified numbers: **gpt-5.6-luna 62/63 scenarios, deepseek-v4-flash 53/56**
(hard assertions; the review UI at `tests/agent/runs/2026-08-13/review.html`
shows every transcript as a phone screen).

## 1 · Working with Vaibhav (the user) — read first

- **Plan-first.** He interrupted mid-implementation twice early on: present the
  plan / findings, get an explicit yes, only then edit files. After a clear
  "go" he switches to rapid-fire build direction and expects you to keep pace.
  (Memory file: `plan-first-approval` in the Claude project memory.)
- **Structure over band-aids.** His words: don't bolt a rule onto a failure —
  "define the behaviour such that it doesn't happen in a more fundamental
  way". Every accuracy win today came from that: identity framing, procedure
  steps, mechanical confidence, protocol repair — not regex nannies. When he
  DID want determinism removed (a figure-grounding guardrail I started), he
  said so explicitly. Ask which mode a fix should be when unclear.
- He adds requirements **mid-turn** (new messages while you work) — fold them
  in without restarting. He kills waits ("if it gets killed, it gets killed").
- Cost-aware: test iterations budgeted at ≤ $0.50 (actual full runs ≈ $0.03).
- Wants to SEE results: the phone-screen review HTML is the review medium he
  asked for; regenerate + resend it after significant runs.
- Product decisions he has already made — do not relitigate: professional-
  minimal voice (no emoji), English + light Hinglish ("ji"/"bilkul"),
  escalation-first (order status / dealer / bulk / collabs / discounts →
  human; unclear intent → ONE clarifying question then escalate), serve every
  product on every IG account (vertical only routes the deal), pincode-first
  location, Indian price notation, capability intro only on fresh no-intent
  contact, visualizer gated (enquiry → 1/day → sales team).

## 2 · What was built (all in `zoho-bridge/`)

| File | What it is |
|---|---|
| `social_agent.py` | The agent: 9-skill registry (single source of truth → runtime tools + SKILLS.md), Responses-API loop, guardrails, system prompt, turn budget, provider override. |
| `customer_profile.py` | Per-contact event-log memory on the Chatwoot CONTACT record (`custom_attributes.durian_profile`): two-lane learning, quote-gate, fold rules, ≤500-token render, consolidation, soft-linking, CRM lookup. Injectable clock (`set_now`). |
| `product_catalog.py` | + synonym layer (l-shaped→corner, N-seater→NSTR, tv→t v), bigram family matching, `require_family` mode, `search_families` grouping. |
| `product_images.py` + `build_product_images.py` + `data/product_images.json` | Listing links + variant photos from durian.in's image sitemap (960 products → 236 families, 899 matched). Lookup fallbacks: sibling extension (BENJAMIN CORNER→BENJAMIN CORNER-I), first-token bucket with corner≈sectional. Rebuild monthly. |
| `chatwoot.py` | + `get_contact` / `update_contact_attributes` / `search_contacts`. |
| `llm_client.py` | GPT-5.x shim for the LEGACY pipeline: pins no-thinking (`floor_effort`: ≥5.4 → "none", else "minimal") and translates `max_tokens`→`max_completion_tokens`. Makes the future legacy model swap to Luna a pure env change. |
| `config.py` | All `SOCIAL_AGENT_*` + `VISUALIZER_*` + `GEMINI_*` flags (see §6). |
| `main.py` | Wiring: agent branch in the social DM path (BEFORE the `pending_*` state machines — agent owns eligible convos end-to-end) + comment branch + `_agent_deal_creator` injection at module bottom. Also the approved legacy tweak: `_retail_showroom_ask` now leads with the pincode ask. |
| `SKILLS.md` | GENERATED from the registry — never hand-edit; `social_agent.write_skills_md()`; a test fails when stale. |
| `tests/agent/` | The harness (§4). |

### The 9 skills
`search_products` (family-grouped, INR-formatted prices, listing `link`,
retry-once-then-honest) · `get_emi_plans` (Snapmint; MANDATORY before any EMI
statement; labels `emi-enquiry`) · `find_showrooms` (pincode-first; store card
with phone + map link; nudges route commitment) · `route_to_showroom` (THE
furniture write: `retail_deal_owner` + `phase2_category` + labels + auto-deal
checklist → `_create_crm_deal`, 409/422 defer to the human button) ·
`register_enquiry` (doors/FHC write: `deal_customer_details` + `deal-ready`) ·
`share_product_images` (client's rule: 1 photo/variant ≤3, single variant→2;
once per product per conv; link required in text with any price) ·
`visualize_in_room` (gated scaffold, §7) · `share_offer` (once/conv, DECLINED-
tag block, tag→flat→priority pick) · `escalate_to_human` + the `finish`
contract (mechanical confidence + mandatory `learned[]`).

### Prompt architecture — why it wins (don't regress it)
The system prompt is not a rule list. Its load-bearing parts, each added to fix
a measured failure class:
1. **Identity**: "YOU HOLD NO PRODUCT KNOWLEDGE… skills are your only senses"
   — killed answer-from-memory.
2. **Procedure** READ → FETCH (fact-class→owning-skill table) → ACT → COMPOSE
   → finish — killed EMI-without-tool (6/6 both models after).
3. **Mechanical confidence** (start 92, fixed subtractions; nothing subtracted
   ⇒ send) — killed "perfect reply, carded anyway".
4. **finish = two equal duties** (confidence + `learned[]` memory) — fixed
   dropped declines/corrections.
5. **Loop-side protocol transport**: prose-instead-of-finish → one repair
   round-trip; steps exhausted → one forced-finish call (tools=[finish]);
   `max_output_tokens=4000` because thinking models spend reasoning from the
   same budget. These fixed DeepSeek's entire "did the work, said nothing"
   class (42→53).

### Guardrails (code, after finish, each demotes send→card)
stored-phone mask (last-4 only unless customer typed it this thread) · re-ask
detector · link allowlist (`durian.in, snapmint.com, maps.app.goo.gl, goo.gl,
maps.google.com, google.com`) · plain-text/emoji scrub (keeps 📞 🗺️ store-card
glyphs) · comment rules (no prices/phones, ≤350 chars) · confidence ≥ 80 ·
turn budget (converge directive at 5 customer msgs; handoff at 8: farewell
once, `agent_handoff_notified`, cards only) · human-override standdown (any
human public reply → `agent_mode_standdown` forever) · fail-safe: any
exception → `agent-needed` labels + note, never silence, never a raw error.

## 3 · Decisions log (with the why)

- **Model**: `gpt-5.6-luna` (user's choice; Luna = cheapest 5.6 tier,
  $0.20/$1.20 per M). OpenAI project allowlist now includes 5.6 (user enabled;
  propagation FLAPPED ~50% for ~30 min — retry handles `does not have access`
  as transient). Responses API because **5.6 chat-completions cannot combine
  tools with thinking**; reasoning effort "low" (`SOCIAL_AGENT_REASONING`).
  Escalation ladder: effort→medium, then tier→terra, before touching code.
- **DeepSeek comparison** (`deepseek-v4-flash:0731` via Ollama cloud
  `https://ollama.com/v1`, key in `.env` as `OLLAMA_API_KEY`): Ollama speaks
  the Responses API natively; `reasoning_effort` maps to DeepSeek's think
  modes (none/default/high). Judge always grades on OpenAI regardless
  (`JUDGE_MODEL`, default gpt-5.4-nano) for fair cross-model comparison.
- **Report contract (do not break)**: auto-sends carry
  `content_attributes.source="ai_auto_reply"` (EXACT string — two report
  builders filter on it), plus `via:"agent_mode"`, `confidence`,
  `short_code:"agent_reply"`, `ai_trace`. Labels the reports/exports need:
  `emi-enquiry`, `retail-routed`, `deal-ready`, `deal-created` (written by the
  deal core, not us), `agent-needed(-instagram)`.
- **Auto-deal**: checklist in code (intent + phone + owner routed + no
  `crm_deal_id`), calls the same `_create_crm_deal` as the panel button; email
  channel already auto-created deals, so this is precedented.
- **24h-window follow-up sweeper**: DESIGNED, NOT BUILT (ship ~a week after
  the live trial). Goal = re-engagement; a reply resets the window. Rules
  agreed: quiet ≥4h, window open, unfinished business from profile, nothing
  DECLINED, max 1 nudge/window, IST business hours, nudges must end in one
  easy question; outcomes to profile; outside window → WhatsApp lane (later).
- **Identity**: soft-link by exact phone/email only, NEVER auto-merge, never
  reveal cross-account knowledge (prompt + rendered warning).
- Product images: variant rule is the client's (2 photos of single variant /
  1 each for ≤3 variants); listing link mandatory with any named price.

## 4 · Test harness — the thing you'll extend

Layout: `tests/agent/{conftest.py, test_scenarios.py, test_units.py,
suites/*.yaml, runs/<date>/, build_review.py}`.

**Engine** (`conftest.py`): `FakeChatwoot` records every attr/label/message/
offer/team write; injectable clock (`BASE_NOW = 2026-08-12 15:00 IST`,
`customer_profile.set_now`); step offsets `-26d`, `-4d 2h`, `+5m`; fakes:
Snapmint (plans = price/6 & price/9 zero-cost + price×1.09/12), CRM fixtures,
deal creator (`TEST-DEAL-1`), `_generate_room_preview` → fake URL. Real LLM
calls are the ONLY unfaked thing. Per-scenario `config:` overrides any
`config.py` attr. Bot history in fixtures MUST carry
`who: durian` (auto-stamped `source: ai_auto_reply`) or it trips the
human-standdown guard; `who: human_agent` deliberately trips it.

**Scenario YAML schema** (everything optional except id+script):
```yaml
- id: my_case
  surface: comment            # default dm
  inbox: duriandoor           # default durianfurniture_official
  contact: {id: 900, name: X}
  profile: {…customer_profile shape…}     # seeded memory
  profile_bulk_events: 45                 # synthetic aged events
  history: [{at: "-1h", who: customer|durian|human_agent, text: …,
             content_attributes: {…}}]    # same conversation, earlier
  prior_conversations: [{comment: true, labels: […], attrs: {…},
                         messages: […]}]  # other conversations (memory tests)
  offers: [{id, caption, image_url, active, priority, tags: […]}]
  crm: {contact: {id: CRM-77}, deals: [{Deal_Name: …}]}
  linked_contacts: [{id, name}]
  config: {VISUALIZER_ENABLED: true}      # any config attr
  script:
    - {at: "0m", text: "hi", photo: true, new_conversation: false,
       content_attributes: {shared_post_caption: "…", image_type: ig_post}}
  expect:
    handled: agent_sent|agent_card|agent_escalated|human_owns_conversation|…
    sent_contains: [regex…]        sent_not_contains: [regex…]
    card_contains: [...]           anywhere_contains: [...]
    no_public_send: true
    attrs: {retail_deal_owner.location: "Delhi - Kirti Nagar", x: "*"}
    labels: [...]                  labels_absent: [...]
    tools_called: ["a|b", …]       tools_not_called: [...]
    no_pii_phone: "9560150835"
    deal_created: true             deal_not_created: true
    deal_created_max: 1
    send_attr: {source: ai_auto_reply}
    offer_sent: true | offer_sent_max: 1 | offer_caption_contains: Dining
    images_sent_min: 2
    profile_has_event: [{kind: declined, what_contains: emi}]
    profile_lacks_event: [...]     profile_event_count: [{kind: visualized, count: 1}]
    profile_consolidated: true     profile_linked: true
    judge: {min: 3}                # omit for hard-asserts-only
```

**Run commands** (ALWAYS `cd zoho-bridge` first — cwd drifts, `./venv/bin/`
paths break silently; zsh eats `===` in echo strings):
```bash
# full suite, Luna, judge on:
./venv/bin/python -m pytest tests/agent -q -n 3
# subset / no judge / transcripts:
SCENARIOS=14_product_media,decline_recorded SKIP_JUDGE=1 \
DUMP_TRANSCRIPTS=tests/agent/runs/$(date +%F)/t_luna.jsonl \
./venv/bin/python -m pytest tests/agent/test_scenarios.py -q -k "not skills_md" -n 3
# DeepSeek (key already in .env; export it):
SOCIAL_AGENT_MODEL="deepseek-v4-flash:0731" \
SOCIAL_AGENT_BASE_URL="https://ollama.com/v1" \
SOCIAL_AGENT_API_KEY="$OLLAMA_API_KEY" …same pytest…
# review page (dedupes reruns, keeps latest per (model, id)):
python3 tests/agent/build_review.py            # newest runs/<date>/
```
`DUMP_TRANSCRIPTS` APPENDS — `rm` the jsonl before a run you'll publish.
Cost: full run ≈ $0.03–0.05, ~2 min at `-n 3` (higher n → 200k-TPM 429s).

**Judge**: OpenAI-only, business-rules-aware (rubric in conftest teaches it:
comment price-redirects, visualizer gating, capability intro, offer grounding,
escalation-by-design are CORRECT). Tool trail is passed as ground truth.

**Suites** (01–14): regression (the 4 original prod bugs) · memory writes ·
memory reads/time/consolidation · deal flow + report contract · skills
grounding · templates/voice · comments · adversarial/injection · turn budget ·
offers · CRM lookup · cross-account · greeting logic · product media +
visualizer gating. Units (`test_units.py`) are free — extend them for any new
pure function.

## 5 · Current results & flake profile

- Luna full: **62/63** latest (only `ambiguous_clarify_once`); DeepSeek 53/56
  (its 3 = empty-output under Ollama burst throttling — they pass singly).
- Known flickers (~1 in 3 runs, all fail SAFE): `decline_recorded` (learned[]
  skipped), `ambiguous_clarify_once`, link-inclusion on price asks,
  `post_split_price_ask`, DS `bare_hi` calibration. These are the top
  stabilization targets — prefer structural fixes (see the prompt-architecture
  list for the style).
- History: every run + transcript jsonl + review.html archived under
  `tests/agent/runs/2026-08-13/` (13 evolution logs). Keep archiving.

## 6 · Config reference (env, all in `config.py`)

`SOCIAL_AGENT_ENABLED` (false) · `SOCIAL_AGENT_MODEL` (gpt-5.6-luna) ·
`SOCIAL_AGENT_REASONING` (low) · `SOCIAL_AGENT_CHANNELS` (instagram) ·
`SOCIAL_AGENT_CONTACT_ALLOWLIST` (ids/names, empty=all) ·
`SOCIAL_AGENT_MAX_STEPS` (6) · `SOCIAL_AGENT_CONVERGE_AFTER` (5) ·
`SOCIAL_AGENT_HANDOFF_AFTER` (8) · `SOCIAL_AGENT_AUTO_DEAL` (true) ·
`SOCIAL_AGENT_HANDOFF_TEAM_ID` (0) · `SOCIAL_AGENT_BASE_URL` /
`SOCIAL_AGENT_API_KEY` (provider override) · `VISUALIZER_ENABLED` (false) ·
`VISUALIZER_DAILY_CAP` (1 — **Vaibhav said he'll change it "tomorrow"**) ·
`GEMINI_API_KEY` (empty — WAITING ON VAIBHAV) · `GEMINI_IMAGE_MODEL`
(gemini-3.1-flash-image placeholder). Legacy knobs that also gate the agent:
`SOCIAL_AUTO_SEND_ENABLED`, `SOCIAL_AUTO_SEND_MIN_CONFIDENCE` (80),
`OFFERS_ENABLED` (true on prod).

## 7 · Open work, in priority order

1. **More comprehensive test cases** (Vaibhav's stated next phase) — ideas
   agreed or implied: deeper Hinglish/Hindi threads; multi-product
   conversations; every escalation category ×(clear/ambiguous); STRICT
   template near-verbatim checks; profile privacy probes (cross-account leak
   attempts); consolidation over months; offer expiry boundaries; invalid→
   corrected pincode; 409/422 deal paths; human takeover mid-flow; webhook
   re-fire idempotency as scenarios; visualizer once key lands. Keep ≤$0.50/run
   (judge sampling if needed). Every new behavior → also a phone-screen
   transcript he can review.
2. **Gemini key lands** → implement `_generate_room_preview` (room photo +
   `product_images.variants(fam)[0]["images"][0]` + placement prompt →
   composite; upload for a Chatwoot-fetchable URL — `send_offer_message`
   downloads a URL, or add a bytes-upload helper in `chatwoot.py`). Then live
   quality trial ~20 real rooms, both image models if he wants (he compared
   GPT Image 2 vs Nano Banana pricing; ~₹4–6/preview either way).
3. Flake stabilization (§5 list) — structural, verify on BOTH models.
4. Commit + branch (nothing committed; conventional commits, no AI
   references per repo rules) → dark-launch deploy: flags into server
   `/root/DurianORM/zoho-bridge/.env`, allowlist `projectvaibhav` (contact
   1589; his other test identities: 2353 Vaibhav Holani, 2355 house_of_holani,
   2512 suhaniiii — all share phone 9560150835), `redeploy.sh`, live IG test.
5. Later (designed, unbuilt): follow-up sweeper (§3) · WhatsApp lane ·
   customer-photo vision understanding · Langfuse tracing for the agent loop
   (bare client today; judge/consolidation ARE traced) · `history.jsonl`
   run-metrics hook · legacy pipeline model swap to Luna (shim ready — verify
   classifier quality via Langfuse after flipping `OPENAI_MODEL`) · Facebook
   channel flip (`SOCIAL_AGENT_CHANNELS`).

## 8 · Gotchas that cost this session real time

- **cwd drifts between Bash calls** — always `cd` absolute into `zoho-bridge`.
- OpenAI: 5.6 chat+tools needs Responses API; `reasoning_effort:"minimal"`
  REJECTED by 5.4+ (use "none"); `max_tokens` rejected by gpt-5.x chat
  (renamed); **reasoning models reject `temperature`** (that's why scenario
  variance exists — use pytest reruns or structure, not temp).
- Model-access edits propagate unevenly for ~minutes (403 "does not have
  access" flaps — retried in `_responses_with_retry`).
- Org limit ~200k TPM: keep `-n 3`; the prompt is deliberately slim (templates
  block capped at 14×250 chars — don't fatten it).
- Thinking models: reasoning spends from `max_output_tokens` — keep 4000.
- The engine's judge false-negatives were fixed by giving it business rules +
  the tool trail; if a new suite's correct behavior gets judge-1s, extend the
  rubric's business-rules block, don't lower `min`.
- durian.in slugs: `esmeralda2`, `lewis…sectional` (no "corner"), BENJAMIN vs
  BENJAMIN CORNER-I ambiguity — matching quirks live in
  `build_product_images.py` + `product_images.variants` fallbacks.
- `.env` files are untracked but PRECIOUS (server creds + OLLAMA key) — never
  commit, never delete.
- Deal-flow consumers (reports/exports/CRM panel) key on EXACT attr strings —
  see §3 report contract before renaming anything.

## 9 · Where everything lives

- Repo: `/Users/vaibhavholani/development/business/durian_projects/DurianORM`
  (branch `main`, dirty). Bridge: `zoho-bridge/`. Venv: `zoho-bridge/venv`
  (Python 3.11; pytest, pyyaml, pytest-xdist, pytest-rerunfailures installed).
- Prod server: `ssh root@168.144.78.165`, repo at `/root/DurianORM`, deploy
  via `redeploy.sh` (memory: `orm-live-server` at durian_projects level).
- Test artifacts: `zoho-bridge/tests/agent/runs/2026-08-13/`.
- Local dev stack (Chatwoot): OrbStack Docker (pg 5434 / redis 6380) +
  `overmind start -f Procfile.local` in `chatwoot/` — running as of handoff.
- Client-facing docs: `zoho-bridge/SKILLS.md` (generated).
