#!/usr/bin/env python3
"""One-off: fill Business Verticals on EXISTING open ORM deals that have none.

New Standard-layout deals are created with Business Verticals (Furniture / Doors
/ Dealership / Franchise). Deals created before that change carry none, and the
client's B2C Stages Blueprint never takes a deal whose Business Verticals is
empty — they sit in no Blueprint (conv 11899, D-555209). This script finds them
through the ORM and writes the same value a new deal would get. It also restores
the per-channel Enquiry Source on deals still showing the bare "DurianORM" left
by the broken ZOHO_CRM_SOURCE_FIELD line (Oct 28 – Nov 4 2026).

Run on the VM from zoho-bridge/ (it reads the bridge's own .env):

    venv/bin/python backfill_deal_verticals.py                 # last 30 days, list only
    venv/bin/python backfill_deal_verticals.py --days 60       # a wider window
    venv/bin/python backfill_deal_verticals.py --apply         # write the fields

--days counts conversation activity (0 = every ORM deal ever). Deals are read
from Zoho 100 at a time. Skips Home Studio deals, closed deals and any deal whose
Business Verticals is already filled (never overwrites the team's own value).
After --apply it reads each deal's Blueprint back, so the output shows whether
Zoho moved it in.
"""

import argparse
import asyncio

import chatwoot
import config
import main
import zoho_crm

_BATCH = 100


async def _deals(ids: list[str], fields: list[str]) -> dict:
    """{deal_id: record} for ids, read in batches (a deleted deal is just absent)."""
    out = {}
    for i in range(0, len(ids), _BATCH):
        chunk = ids[i:i + _BATCH]
        try:
            r = await zoho_crm._crm_request(
                "GET", "/Deals", params={"ids": ",".join(chunk), "fields": ",".join(fields)})
        except Exception as e:
            print(f"could not read deals {i + 1}–{i + len(chunk)}: {str(e)[:80]}")
            continue
        for d in r.get("data") or []:
            out[str(d.get("id"))] = d
        print(f"  read {min(i + _BATCH, len(ids))}/{len(ids)} deals", flush=True)
    return out


async def _blueprint(deal_id: str) -> str:
    try:
        r = await zoho_crm._crm_request("GET", f"/Deals/{deal_id}/actions/blueprint")
        return (((r or {}).get("blueprint") or {}).get("process_info") or {}).get("name") or "?"
    except Exception as e:
        return "no Blueprint" if "RECORD_NOT_IN_PROCESS" in str(e) else f"? ({str(e)[:60]})"


async def run(apply: bool, days: int) -> None:
    bv_field = config.ZOHO_CRM_BUSINESS_VERTICALS_FIELD
    src_field = config.ZOHO_CRM_SOURCE_FIELD
    if not bv_field:
        print("ZOHO_CRM_BUSINESS_VERTICALS_FIELD is empty — nothing to do.")
        return
    convs = await chatwoot.list_deal_conversations(max_age_days=days)
    by_deal = {str((c.get("custom_attributes") or {}).get("crm_deal_id") or ""): c
               for c in convs}
    by_deal.pop("", None)
    window = f"active in the last {days} days" if days else "of any age"
    print(f"{len(by_deal)} ORM deals {window}. Reading them from Zoho…")
    deals = await _deals(list(by_deal), ["Layout", "Stage", bv_field, "Lead_Source"])

    todo = []
    for deal_id, conv in by_deal.items():
        deal = deals.get(deal_id)
        if not deal:
            continue
        layout = (deal.get("Layout") or {}).get("name") or ""
        stage = str(deal.get("Stage") or "")
        if layout != "Standard" or stage.startswith("Closed") or deal.get(bv_field):
            continue
        custom = conv.get("custom_attributes") or {}
        category = (custom.get("email_category_v2") or {}).get("category") \
            or custom.get("phase2_category") or ""
        owner = custom.get("retail_deal_owner") or {}
        verticals = main._deal_business_verticals(custom, category, layout, owner)
        fields = {bv_field: verticals} if verticals else {}
        if src_field and deal.get("Lead_Source") == config.ZOHO_CRM_LEAD_SOURCE:
            source = main._enquiry_source_for(conv)
            if source:
                fields[src_field] = source
        if fields:
            todo.append((conv, deal_id, stage, fields))

    print(f"\n{len(todo)} open Standard deals have no Business Verticals:\n")
    for conv, deal_id, stage, fields in todo:
        print(f"conv {conv.get('id')} · deal {deal_id} · {stage} → {fields}")
    if not apply:
        print(f"\nList only — nothing changed. Re-run with --apply to write these "
              f"{len(todo)} deals.")
        return

    done = failed = 0
    for conv, deal_id, stage, fields in todo:
        # Business Verticals and Enquiry Source in separate updates, so one
        # refused value never costs the other.
        results = [await zoho_crm.update_deal(deal_id, {k: v})
                   for k, v in fields.items()]
        if all(results):
            done += 1
            print(f"conv {conv.get('id')} · deal {deal_id} ✓ now in: {await _blueprint(deal_id)}")
        else:
            failed += 1
            print(f"conv {conv.get('id')} · deal {deal_id} ✗ part of this update was "
                  f"refused — see the [crm] line above")
    print(f"\nUpdated {done}, failed {failed}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write the fields (default: list only)")
    ap.add_argument("--days", type=int, default=30,
                    help="only deals whose conversation was active in the last N days (0 = all)")
    a = ap.parse_args()
    asyncio.run(run(apply=a.apply, days=a.days))
