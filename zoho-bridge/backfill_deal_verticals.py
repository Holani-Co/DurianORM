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

    venv/bin/python backfill_deal_verticals.py            # list only, changes nothing
    venv/bin/python backfill_deal_verticals.py --apply    # write the fields

Skips Home Studio deals, closed deals and any deal whose Business Verticals is
already filled (never overwrites the team's own value). After --apply it reads
each deal's Blueprint back, so the output shows whether Zoho moved it in.
"""

import asyncio
import sys

import chatwoot
import config
import main
import zoho_crm


async def _current(deal_id: str, fields: list[str]) -> dict:
    r = await zoho_crm._crm_request("GET", f"/Deals/{deal_id}",
                                    params={"fields": ",".join(fields)})
    return (r.get("data") or [{}])[0]


async def _blueprint(deal_id: str) -> str:
    try:
        r = await zoho_crm._crm_request("GET", f"/Deals/{deal_id}/actions/blueprint")
        return (((r or {}).get("blueprint") or {}).get("process_info") or {}).get("name") or "?"
    except Exception as e:
        return "no Blueprint" if "RECORD_NOT_IN_PROCESS" in str(e) else f"? ({str(e)[:60]})"


async def run(apply: bool) -> None:
    bv_field = config.ZOHO_CRM_BUSINESS_VERTICALS_FIELD
    src_field = config.ZOHO_CRM_SOURCE_FIELD
    if not bv_field:
        print("ZOHO_CRM_BUSINESS_VERTICALS_FIELD is empty — nothing to do.")
        return
    convs = await chatwoot.list_deal_conversations(max_age_days=0)
    print(f"{len(convs)} conversations with a deal.\n")
    done = failed = 0
    for conv in convs:
        custom = conv.get("custom_attributes") or {}
        deal_id = str(custom.get("crm_deal_id") or "")
        if not deal_id:
            continue
        try:
            deal = await _current(deal_id, ["Layout", "Stage", bv_field, "Lead_Source"])
        except Exception as e:
            print(f"conv {conv.get('id')} · deal {deal_id} · could not read deal: {str(e)[:80]}")
            continue
        layout = (deal.get("Layout") or {}).get("name") or ""
        stage = str(deal.get("Stage") or "")
        if layout != "Standard" or stage.startswith("Closed") or deal.get(bv_field):
            continue
        category = (custom.get("email_category_v2") or {}).get("category") \
            or custom.get("phase2_category") or ""
        owner = custom.get("retail_deal_owner") or {}
        verticals = main._deal_business_verticals(custom, category, layout, owner)
        fields = {bv_field: verticals} if verticals else {}
        if src_field and deal.get("Lead_Source") == config.ZOHO_CRM_LEAD_SOURCE:
            source = main._enquiry_source_for(conv)
            if source:
                fields[src_field] = source
        print(f"conv {conv.get('id')} · deal {deal_id} · {stage}\n    → {fields}")
        if not apply or not fields:
            continue
        # Business Verticals and Enquiry Source in separate updates, so one
        # refused value never costs the other.
        results = [await zoho_crm.update_deal(deal_id, {k: v})
                   for k, v in fields.items()]
        if all(results):
            done += 1
            print(f"    ✓ now in: {await _blueprint(deal_id)}")
        else:
            failed += 1
            print("    ✗ part of this update was refused — see the [crm] line above")
    if apply:
        print(f"\nUpdated {done}, failed {failed}.")
    else:
        print("\nList only — nothing changed. Re-run with --apply to write these fields.")


if __name__ == "__main__":
    asyncio.run(run(apply="--apply" in sys.argv[1:]))
