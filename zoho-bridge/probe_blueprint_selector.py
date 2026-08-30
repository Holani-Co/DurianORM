#!/usr/bin/env python3
"""Read-only probe for the Deals field that selects Digital vs Offline.

Run on the production VM, where the live Zoho CRM credentials are available:

    python probe_blueprint_selector.py

Zoho chooses a Blueprint from record entry criteria; the API cannot select a
Blueprint by its display name. Durian's Blueprint entry criteria use the field
label "Integration Source", so this reports that field's API name and type.
"""

import asyncio

import zoho_crm


async def main() -> None:
    response = await zoho_crm._crm_request(
        "GET", "/settings/fields", params={"module": "Deals"}
    )
    matches = []
    for field in response.get("fields") or []:
        label = str(field.get("field_label") or "").strip().lower()
        api_name = str(field.get("api_name") or "").strip().lower()
        values = [
            str(value.get("display_value") or "")
            for value in field.get("pick_list_values") or []
        ]
        if label == "integration source" or api_name == "integration_source":
            matches.append((field, values))

    if not matches:
        print("No Deals field labelled Integration Source was found.")
        print("Check its API name in Zoho CRM Setup > Developer Space > APIs.")
        return

    for field, values in matches:
        print(f"{field.get('field_label')} ({field.get('api_name')})")
        print(f"  type: {field.get('data_type')}")
        if values:
            print("  allowed values: " + ", ".join(values))


if __name__ == "__main__":
    asyncio.run(main())
