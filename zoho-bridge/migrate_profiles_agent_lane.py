#!/usr/bin/env python3
# One-off: REBUILD every customer profile through the agent's own judgment.
#
# Before the agent-decided profile (see customer_profile.py header), a code
# lane wrote regex phones/pincodes, catalog-lookalike "interest" events, raw
# comments, conversation-state copies and CRM notes straight into profiles.
# The 2026-08-15 audit of the 55 prod profiles found 12 phantom interests
# ("will you get back to me" → TWILL bean bag; "very helpful" → AVERY
# recliner; a MetaMile pitch → EUROPA sofa), 7 suspect pincodes (bare
# six-digit strings fired one after another), a pincode stored as the string
# "Pincode 110054", 43 raw comment captures and 15 CRM notes.
#
# Fix: for each contact, render their whole conversation history as a
# transcript and let the AGENT decide what the profile should hold
# (social_agent.judge_history_for_profile — the same profile_updates
# contract and evidence gate the live turn uses). Correct facts survive with
# a quote and a reason; ghosts cannot pass the gate. Kept verbatim: existing
# quote-bearing (agent-lane) events, linked_contacts, consolidated history,
# the visualizer counter (moved to ops).
#
#   ./venv/bin/python migrate_profiles_agent_lane.py < ids.txt            # preview
#   ./venv/bin/python migrate_profiles_agent_lane.py --apply < ids.txt    # write
# ids.txt: one Chatwoot contact id per line (prod: from psql, see HANDOFF §11).

import asyncio
import json
import sys

import chatwoot
import customer_profile as cp
import social_agent

APPLY = "--apply" in sys.argv


def carry_over(prof: dict) -> dict:
    """Everything that was already agent-decided or is operational state."""
    new = cp.empty_profile()
    kept = [e for e in (prof.get("events") or []) if e.get("quote")]
    viz = [e.get("t") for e in (prof.get("events") or []) if e.get("kind") == "visualized"]
    new["events"] = sorted(kept, key=lambda e: (e.get("t") or "", e.get("msg") or 0))
    new["events_since_consolidation"] = len(kept)
    new["linked_contacts"] = list(prof.get("linked_contacts") or [])
    new["consolidated"] = prof.get("consolidated") or new["consolidated"]
    new["consolidated_at"] = prof.get("consolidated_at")
    if viz:
        new["ops"] = {"visualized_at": viz[-30:]}
    return new


def _fmt(prof: dict) -> str:
    ident, loc, com = prof.get("identity") or {}, prof.get("location") or {}, prof.get("commercial") or {}
    facts = []
    for label, d in (("phone", ident.get("phone")), ("pincode", loc.get("pincode")),
                     ("city", loc.get("city")), ("showroom", com.get("showroom"))):
        if d and d.get("value"):
            facts.append(f"{label}={d['value']}")
    evs = [f"{e.get('kind')}:{str(e.get('what') or '')[:28]}" for e in prof.get("events") or []]
    return f"[{', '.join(facts) or 'no facts'}] events={evs}"


async def rebuild_one(cid: int) -> tuple[dict, dict] | None:
    prof = await cp.load(cid)
    if not prof:
        return None
    contact = await chatwoot.get_contact(cid)
    name = (contact or {}).get("name") or f"contact {cid}"
    # The WHOLE history — this is a one-off; a fact given in the customer's
    # first conversation must not fall outside a window. (The live cold-start
    # path keeps its small budget; the agent sees the rest as it happens.)
    transcript = await cp.prior_transcript(cid, None, max_conversations=200,
                                           max_chars=60000)
    new = carry_over(prof)
    if transcript.strip():
        updates = await social_agent.judge_history_for_profile(name, transcript, None, "")
        cp.apply_updates(new, updates)
    return prof, new


async def main() -> None:
    ids = [int(x) for x in sys.stdin.read().split() if x.strip().isdigit()]
    tot = {"contacts": 0, "before_events": 0, "after_events": 0, "empty_after": 0}
    for cid in ids:
        res = await rebuild_one(cid)
        if not res:
            continue
        old, new = res
        tot["contacts"] += 1
        tot["before_events"] += len(old.get("events") or [])
        tot["after_events"] += len(new.get("events") or [])
        if not new.get("events") and not (new.get("identity") or new.get("location")):
            tot["empty_after"] += 1
        print(f"contact {cid}\n   before {_fmt(old)}\n   after  {_fmt(new)}")
        if APPLY:
            await cp.save(cid, new)
    print(json.dumps(tot), "(APPLIED)" if APPLY else "(preview — pass --apply to write)")


if __name__ == "__main__":
    asyncio.run(main())
