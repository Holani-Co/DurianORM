#!/usr/bin/env python3
# One-off: rebuild every customer profile from its AGENT-LANE events only.
#
# Before the agent-decided profile (see customer_profile.py header), a code
# lane wrote regex phones/pincodes, catalog-lookalike "interest" events,
# raw comments, conversation-state copies and CRM notes straight into
# profiles — the MetaMile "2-seater leather sofa" incident came from there.
# Those entries carry no customer quote; agent-lane entries always do.
#
# This keeps: quote-bearing events, linked_contacts, consolidated history,
# the visualizer counter (moved to ops), and identity/location facts ONLY
# where a same-valued quote-bearing event backs them. Everything else is
# dropped; the contact's earlier conversations are re-judged by the agent on
# their next message (cold start → prior transcript).
#
#   ./venv/bin/python migrate_profiles_agent_lane.py            # preview
#   ./venv/bin/python migrate_profiles_agent_lane.py --apply    # write

import asyncio
import json
import sys

import chatwoot
import customer_profile as cp

APPLY = "--apply" in sys.argv


def rebuild(prof: dict) -> tuple[dict, dict]:
    new = cp.empty_profile()
    stats = {"kept": 0, "dropped": 0}
    kept_events = []
    viz_at = []
    for e in prof.get("events") or []:
        if e.get("kind") == "visualized":
            viz_at.append(e.get("t"))
            continue
        if e.get("quote"):
            kept_events.append(e)
            stats["kept"] += 1
        else:
            stats["dropped"] += 1
    new["events"] = sorted(kept_events, key=lambda e: (e.get("t") or "", e.get("msg") or 0))
    new["events_since_consolidation"] = len(kept_events)
    new["linked_contacts"] = list(prof.get("linked_contacts") or [])
    new["consolidated"] = prof.get("consolidated") or new["consolidated"]
    new["consolidated_at"] = prof.get("consolidated_at")
    if viz_at:
        new["ops"] = {"visualized_at": viz_at[-30:]}
    # identity/location facts survive only with a quote-bearing correction
    # of the same value behind them (the agent said so); regex-derived ones go.
    quoted_values = {str(e.get("what") or "").lower() for e in kept_events}
    for sec in ("identity", "location", "commercial"):
        for fld, entry in (prof.get(sec) or {}).items():
            if not isinstance(entry, dict):
                continue
            val = str(entry.get("value") or "").lower()
            if val and any(val in q for q in quoted_values):
                new.setdefault(sec, {})[fld] = entry
    return new, stats


async def main() -> None:
    contacts = await chatwoot.search_contacts_with_attribute(cp.PROFILE_KEY) \
        if hasattr(chatwoot, "search_contacts_with_attribute") else None
    if contacts is None:
        # fall back to ids on stdin (one per line) — the prod run pipes them
        # from psql, which is simpler than paging the contacts API.
        ids = [int(x) for x in sys.stdin.read().split() if x.strip().isdigit()]
    else:
        ids = [c["id"] for c in contacts]
    total = {"contacts": 0, "kept": 0, "dropped": 0, "emptied": 0}
    for cid in ids:
        prof = await cp.load(cid)
        if not prof:
            continue
        new, st = rebuild(prof)
        total["contacts"] += 1
        total["kept"] += st["kept"]
        total["dropped"] += st["dropped"]
        if not new["events"]:
            total["emptied"] += 1
        print(f"contact {cid}: keep {st['kept']} drop {st['dropped']}"
              f"{' → empty (rebuilt on next message)' if not new['events'] else ''}")
        if APPLY:
            await cp.save(cid, new)
    print(json.dumps(total), "(APPLIED)" if APPLY else "(preview — pass --apply to write)")


if __name__ == "__main__":
    asyncio.run(main())
