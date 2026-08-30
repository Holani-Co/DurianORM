#!/usr/bin/env python3
"""Read-only probe for the Deals field that selects Digital vs Offline.

Run on the production VM, where the live Zoho CRM credentials are available:

    python probe_blueprint_selector.py

Zoho chooses a Blueprint from record entry criteria; the API cannot select a
Blueprint by its display name. This reports picklist fields whose values contain
both "digital" and "offline", which are the likely entry-criteria selector.
"""

import asyncio

import zoho_crm


async def main() -> None:
    response = await zoho_crm._crm_request(
        "GET", "/settings/fields", params={"module": "Deals"}
    )
    matches = []
    for field in response.get("fields") or []:
        values = [
            str(value.get("display_value") or "")
            for value in field.get("pick_list_values") or []
        ]
        lowered = [value.lower() for value in values]
        if (
            any("digital" in value for value in lowered)
            and any("offline" in value for value in lowered)
        ):
            matches.append((field, values))

    if not matches:
        print("No Deals picklist contains both Digital and Offline values.")
        print("Check each Blueprint's entry criteria in Zoho CRM Setup and use ")
        print("that field's API name for ZOHO_CRM_BLUEPRINT_SELECTOR_FIELD.")
        return

    for field, values in matches:
        print(f"{field.get('field_label')} ({field.get('api_name')})")
        print("  values: " + ", ".join(values))


if __name__ == "__main__":
    asyncio.run(main())
