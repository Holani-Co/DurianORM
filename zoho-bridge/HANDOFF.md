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

---

## 10 · Addendum — image-coverage phase (2026-08-13, later session)

Done this session (all still UNCOMMITTED, on top of everything above):

- **Front-view rule confirmed & pinned**: the sitemap (hence
  `data/product_images.json`) preserves the site gallery order, so
  `images[0]` per variant IS the front view. `share_set()` always leads with
  it; unit tests pin exact URLs.
- **`share_product_images` is now variant-aware** (Vaibhav's decisions):
  `variant` hint (colour/size words; grey≈gray, 2≈two) makes that variant
  lead and the listing link follow it; `compare=true` → exactly one front
  view per product; `resend=true` ONLY on an explicit customer re-ask
  (re-sends allowed then); already-shared family + new `variant` → targeted
  top-up (≤2 photos of just that variant, never repeating a sent URL —
  tracked in conv attr `product_images_sent`; `product_images_shared`
  semantics unchanged). >3 variants → first 3 in site order + skill note
  telling the model to invite colour-specific asks. Comment surface →
  skill refuses, invites to DM. Junk sitemap variant names ('2') fall back
  to the family name as caption. SKILLS.md regenerated.
- **Loop repair extended**: finish() with EMPTY reply now gets ONE repair
  round-trip via the tool-result channel (DeepSeek sends photos then
  finishes wordless — "did the work, said nothing" v2, fixed 5 scenarios).
  Photo-skill success note now carries "write your reply, include this
  link: <url>" — result-channel pressure that weak models actually follow.
- **Suite 15** (`15_image_coverage.yaml`, 16 scenarios): front-view URL
  pinning, variant targeting (incl. Hinglish seater), comparisons (fresh +
  after-share), re-send boundaries, top-up, post-split, EMI+photos,
  no-photos honesty (AMANDA), comment/escalation/greeting negatives.
  Harness: `offer_sends` records `image_url`; new expects
  `images_sent_contains/max/not_contains`, `images_unique`; review.html
  renders real product photos in the phone bubbles (`.pimg`).
- **Judge upgraded to `gpt-5.6-terra`** (Vaibhav's call, env-overridable) +
  judge now sees surface, profile facts, last 3 profile events, and history;
  rubric gained photo policy, no-photos honesty, full greeting decision tree
  (intent → no preamble!), DM store cards. Judge 429 backoff now 7×/exp —
  score-0-on-starvation was failing runs.
- **Gemini key landed** in `.env` (`GEMINI_API_KEY`); visualizer still dark.
  Image-gen cache design agreed: hot cache on disk, long tail on the Spaces
  bucket, daily cron for stale source images (memory:
  `image-phase-decisions`).
- **Latest results**: Luna 79/79 scenarios (+18 units + skills_md fresh),
  DeepSeek suite-15 16/16 (its 3 old-suite misses are yesterday's throttle
  artifacts). review.html: 151 transcripts, photos rendered.
- **Watch**: `comparison_after_share` flickered once on the fairmont LINK
  (image sends were right) — known link-inclusion class. `t_luna_final` /
  `t_ds_images` jsonls are consolidated into `transcripts_luna/deepseek`
  (build_review reads ONLY those two names).
- **Next**: image generation flow (Gemini) per §7.2 + Spaces cache above —
  Vaibhav reviews the rendered HTML first.

### §10.1 — Visualizer flow (same day, later)

- **Flow built per Vaibhav's spec** (all UNCOMMITTED): `visualize_in_room`
  now takes `variant` + `placement`; code-enforced question protocol —
  need_variant (colour list in the note) and need_placement, at most these
  TWO questions ever; his obviousness rule in code: `_analyze_room` (Gemini
  flash on the room photo) → exactly one same-type piece = "replace it",
  no question. Vacuous placements ("in my room") are stripped in code —
  models harvest them. CALL-FIRST contract in the skill description AND
  prompt: the model must never pre-ask colour/placement (only the skill
  sees the room; Luna asked preemptively without it ~1 in 3 until this).
- **Generation is REAL now**: `_generate_room_preview` → Gemini
  `GEMINI_IMAGE_MODEL` via REST/httpx (no new deps), returns bytes;
  delivered by new `chatwoot.send_image_bytes` (multipart, same two-message
  split as offers). `GEMINI_ANALYSIS_MODEL` (flash) added to config.
  Fail-safes: analysis failure → ask the generic placement question;
  generation/delivery failure → honest unavailable + showroom.
- **Eagerness**: context gains "VISUALIZER PASS FREE TODAY" (enabled + cap
  unused, DMs only) + prompt: offer ONCE when a specific product is in play,
  pre-enquiry (his call — the offer pulls the enquiry).
- **Capture**: `visualized` event carries "FAM — variant — placement";
  `visualizer_request` conv attr for the sales team.
- **Suite 17** (8 scenarios, 2× stable): eager offer / cap-silent / variant
  question / obvious-replace / placement question / two-questions-max /
  all-details-one-message. Harness: `room_analysis:` scenario key seeds the
  vision verdict; `send_image_bytes` in the FakeChatwoot patch list;
  **profiles deep-copied at seeding** (YAML anchors shared the dict —
  one scenario's visualized event leaked into the next and tripped the cap).
- **Trial ready, blocked on room files**: `tests/agent/visualizer_trial.py`
  — per room: flash picks the variant for the room (his auto-pick call) →
  vets the 2 reference photos → placement by the obviousness rule →
  compose → `runs/<date>/viz_trial/viz_review.html` (room | reference |
  composite). Rooms go in `tests/agent/rooms/` (waiting on Vaibhav's 3
  photos). ~4 Gemini calls/room, ≈₹5–6 each.

### §10.2 — Generation live: dual engine + the swatch discovery

- **Both engines now compose** via `_compose_preview` (shared by prod skill
  and trial): `VISUALIZER_ENGINE` = "gpt-image-2" (OpenAI key, works today;
  images/edits, size=auto quality=medium — it does NOT take input_fidelity)
  or "gemini" (`GEMINI_IMAGE_MODEL` gemini-3.1-flash-image / Nano Banana —
  Vaibhav enabled paid tier 2026-08-13; free tier is limit:0 for ALL image
  models, and `_gemini_generate` fails fast on that, retries only real
  429/5xx). Analysis model pinned `gemini-3.5-flash` (3.1-flash doesn't
  exist).
- **THE SWATCH DISCOVERY** (vet stage caught it, exactly Vaibhav's fear):
  VERONICA's sitemap gallery is 100% fabric SWATCHES, no product shots.
  Real photos exist only in the Unbxd family-feed rows. And only in BEIGE —
  so colour variants ride in as a THIRD reference image (the swatch) with
  an "upholster exactly in this fabric" prompt clause. Trial vet pool =
  sitemap variant images + Unbxd(variant query) + Unbxd(bare family), ≤7,
  flash picks best full-product shot + judges colour match; unusable pool →
  compose SKIPPED (never paste a swatch into a room).
- **Trial results 2026-08-13**: 3 rooms × 2 engines = 6/6 composites,
  variant auto-picked per room (canary yellow / brown sepia / ice grey —
  flash matched each room's palette), placement per the obviousness rule.
  Review: `runs/2026-08-13/viz_trial/viz_review_full.html` (self-contained,
  sent to Vaibhav). Awaiting his engine/quality verdict.
- **NOT yet in the prod skill**: the pooled vet + swatch flow lives only in
  the trial; `visualize_in_room` still takes share_set's front image (a
  swatch for Veronica-class families!). Port after Vaibhav's verdict —
  ~one flash vet call inside the skill, cap 1/day bounds cost.
- Full board after all visualizer changes: **115/116** (decline_recorded
  flake only). review.html now carries 169 transcripts incl. suite 17.

### §10.3 — Vet + swatch ported into the prod skill ("do it")

- `visualize_in_room` now calls `_pick_reference(fam, prefer)` instead of
  taking share_set's front image raw: pool = sitemap variant gallery +
  Unbxd storefront images (variant + bare-family queries, ≤7), one flash
  vet call picks the best FULL-product shot and judges colour match.
  Swatch-only family → honest `unavailable` denial ("only fabric swatches
  on file"), showroom offer — never composites a swatch. Colour mismatch →
  the variant's swatch rides into `_generate_room_preview(...,
  swatch_url=)` as the third reference image. Vet failure fails CLOSED.
- Harness: `_pick_reference` faked deterministically; `reference_vet:`
  scenario key overrides (usable/matches_colour); `preview_calls` recorded;
  new expect `preview_swatch_used`. Suite 17 grew to 10 scenarios
  (`viz_swatch_family_honest`, `viz_colour_swatch_composite`) — 13/13
  viz scenarios pass judge-off, 10/10 suite-17 with terra judge.
- Engine verdict from Vaibhav: NOT yet given — `VISUALIZER_ENGINE` stays
  gpt-image-2; flip via env after he compares the trial page.
- Before dark-launch: run the FULL board once more (last full run 115/116
  predates this port), then §7.4 commit/deploy steps still apply.
