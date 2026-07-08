#!/usr/bin/env python3
# One-off probe: report the real type of the Deals "Enquiry Source" / Lead_Source
# field in the LIVE CRM, so we know whether writing "DurianORM" is safe or would
# fail with INVALID_DATA (strict picklist without that option).
#
# Run on the VM (which has the prod .com CRM creds), inside the bridge venv:
#   python probe_lead_source.py

import asyncio
import zoho_crm


TARGET = {"lead_source", "enquiry source", "enquiry_source"}


async def main():
    # Module fields metadata — one call, read-only.
    resp = await zoho_crm._crm_request("GET", "/settings/fields",
                                       params={"module": "Deals"})
    fields = resp.get("fields", [])
    hits = [f for f in fields
            if (f.get("api_name", "").lower() in TARGET
                or (f.get("field_label", "").lower() in TARGET))]
    if not hits:
        print("No field named Lead_Source / Enquiry Source found on Deals.")
        print("All picklist/text fields with 'source' in the name:")
        for f in fields:
            name = f"{f.get('field_label')} ({f.get('api_name')})"
            if "source" in name.lower():
                print(f"  - {name}: data_type={f.get('data_type')}")
        return

    for f in hits:
        dt = f.get("data_type")
        print(f"FIELD: {f.get('field_label')}  (api_name={f.get('api_name')})")
        print(f"  data_type   : {dt}")
        print(f"  mandatory   : {f.get('system_mandatory') or f.get('required')}")
        if dt == "picklist":
            values = [pv.get("display_value") for pv in (f.get("pick_list_values") or [])]
            print(f"  PICKLIST — writing anything NOT in this list fails with INVALID_DATA:")
            for v in values:
                print(f"      • {v}")
            has_durian = any((v or "").strip().lower() == "durianorm" for v in values)
            has_chatwoot = any((v or "").strip().lower() == "chatwoot" for v in values)
            print(f"  >>> 'DurianORM' present? {has_durian}   'Chatwoot' present? {has_chatwoot}")
            if not has_durian:
                print("  >>> ACTION: add 'DurianORM' as a picklist option in Zoho, "
                      "OR keep ZOHO_CRM_LEAD_SOURCE=Chatwoot until you do.")
        else:
            print("  >>> FREE-TEXT field — 'DurianORM' is accepted as-is, no Zoho "
                  "setup needed. Safe to deploy PR #77.")


if __name__ == "__main__":
    asyncio.run(main())
