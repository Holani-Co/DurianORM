#!/usr/bin/env python3
"""One-off: mark EXISTING WhatsApp-campaign deals the way new ones are marked.

New deals from a WhatsApp campaign lead (a click-to-WhatsApp ad, or a reply to
one of our own WhatsApp campaigns) are created with Integration Source =
ZOHO_CRM_CAMPAIGN_INTEGRATION_SOURCE plus the campaign columns. Deals created
before that change carry neither. This script finds them through the ORM and
writes the same fields — plus the per-channel Lead Source (Enquiry Source) when
ZOHO_CRM_SOURCE_FIELD is set.

Run on the VM from zoho-bridge/ (it reads the bridge's own .env):

    venv/bin/python backfill_campaign_deals.py            # list only, changes nothing
    venv/bin/python backfill_campaign_deals.py --apply    # write the fields

Never overwrites an Integration Source that already holds another value.
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


async def run(apply: bool) -> None:
    convs = await chatwoot.list_deal_conversations(max_age_days=0)
    rows = [(c, main._whatsapp_campaign(c)) for c in convs]
    rows = [(c, camp) for c, camp in rows if camp]
    print(f"{len(convs)} conversations with a deal; {len(rows)} are WhatsApp campaign leads.\n")
    integ_field = config.ZOHO_CRM_INTEGRATION_SOURCE_FIELD
    done = skipped = failed = 0
    for conv, camp in rows:
        deal_id = str((conv.get("custom_attributes") or {}).get("crm_deal_id") or "")
        fields = main._campaign_deal_fields(camp)
        blueprint = str((conv.get("custom_attributes") or {}).get("fhc_blueprint") or "").lower()
        integ = main._campaign_integration_source(conv, blueprint)
        if config.ZOHO_CRM_SOURCE_FIELD:
            source = main._enquiry_source_for(conv)
            if source:
                fields[config.ZOHO_CRM_SOURCE_FIELD] = source
        note = ""
        if integ:
            try:
                existing = str((await _current(deal_id, [integ_field])).get(integ_field) or "")
            except Exception as e:
                existing, note = "", f"(could not read deal: {str(e)[:80]})"
            if existing and existing != integ:
                note = f"keeps Integration Source {existing!r}"
            else:
                fields[integ_field] = integ
        print(f"conv {conv.get('id')} · deal {deal_id} · {camp['medium']}"
              f"{' · ' + camp['name'][:50] if camp.get('name') else ''}\n"
              f"    → {fields} {note}")
        if not apply:
            continue
        if not fields:
            skipped += 1
            continue
        if await zoho_crm.update_deal(deal_id, fields):
            done += 1
        else:
            failed += 1
            print("    ✗ update failed — see the [crm] line above")
    if apply:
        print(f"\nUpdated {done}, failed {failed}, nothing to write {skipped}.")
    else:
        print("\nList only — nothing changed. Re-run with --apply to write these fields.")


if __name__ == "__main__":
    asyncio.run(run(apply="--apply" in sys.argv[1:]))
