
### §10.5 — Visualizer GA + live polish (2026-08-13 morning)

- Multipart 400 ROOT CAUSE found via live repro: _headers()'s JSON
  Content-Type overrode httpx's multipart boundary — EVERY bridge image
  attachment (offers, product photos, previews) had been 400ing silently
  in prod, masked by captions sending separately. Fixed: token-only header
  on multipart posts. This is why photos "worked" in tests (faked) but
  never delivered live.
- Skill-registration prod bug (helper def swallowed the @_skill decorator →
  TypeError) hotfixed; unit test now pins every handler to its _sk_*.
- Live polish per Vaibhav: pre-generation "about 2 minutes" wait note
  (code-authored, ai_auto_reply-stamped, model told not to repeat);
  ENGLISH-ONLY replies (Hinglish understood, never spoken — prompt, judge
  rubric, hinglish_light_touch hard assert).
- Rollout: VISUALIZER_CONTACT_ALLOWLIST emptied → EVERYONE (all agent-mode
  IG contacts), engine gpt-image-2, VISUALIZER_DAILY_CAP=10 — the 10 was
  set for Vaibhav's test day; REVISIT the cap (client design was 1/day →
  sales handoff). Ownership is assignee-based; ack sends can't trip it.
- Still open: board triage (stitched-reply judge class + comparison
  flicker), catalog byte-dupe cleanup, Spaces preview cache, engine
  verdict gpt vs gemini.
