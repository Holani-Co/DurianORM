#!/usr/bin/env python3
# One-shot probe: can we create a Deal on the "Home Studio" layout, and with
# WHICH stage? The client's Home Studio layout shows no pipeline in the CRM UI
# — Zoho validates a deal's Stage against its layout, so instead of guessing
# we try it empirically: create a probe deal on that layout with a few
# candidate stages, report the first that works, then DELETE the probe deal.
#
# Run during go-live, once the real-org (.com) token is in .env:
#   venv/bin/python probe_home_studio_layout.py            # default candidates
#   venv/bin/python probe_home_studio_layout.py "My Stage" # explicit candidate
#
# Outcome → env:
#   works → ZOHO_CRM_HOME_STUDIO_LAYOUT="Home Studio"
#           ZOHO_CRM_HOME_STUDIO_STAGE="<reported stage>"   # if not the default
#   fails → leave the layout gate off; the client must configure the Home
#           Studio pipeline (Setup → Pipelines → Layout: Home Studio).

import asyncio
import sys

import config
import zoho_crm

LAYOUT = "Home Studio"


async def main():
    if not config.ZOHO_CRM_ENABLED:
        raise SystemExit("ZOHO_CRM_REFRESH_TOKEN not set — configure CRM first")

    layout_id = await zoho_crm.get_deal_layout_id(LAYOUT)
    if not layout_id:
        raise SystemExit(
            f"Layout {LAYOUT!r} not found (or token lacks "
            f"ZohoCRM.settings.layouts.READ). Nothing to probe.")
    print(f"{LAYOUT!r} layout id: {layout_id}")

    candidates = sys.argv[1:] or [
        config.ZOHO_CRM_DEAL_DEFAULT_STAGE,   # the Standard-layout entry stage
        "Qualification",                       # Zoho's built-in default
    ]
    for stage in candidates:
        record = {
            "Deal_Name":   "[PROBE] Home Studio layout test — safe to delete",
            "Stage":       stage,
            "Lead_Source": config.ZOHO_CRM_LEAD_SOURCE,
            "Layout":      {"id": layout_id},
        }
        try:
            resp = await zoho_crm._crm_request(
                "POST", "/Deals", json_body={"data": [record]})
            entry = (resp.get("data") or [{}])[0]
            if entry.get("code") == "SUCCESS":
                deal_id = str((entry.get("details") or {}).get("id") or "")
                print(f"✅ WORKS with stage {stage!r} (deal {deal_id})")
                try:
                    await zoho_crm._crm_request("DELETE", f"/Deals/{deal_id}")
                    print("   probe deal deleted.")
                except Exception as e:
                    print(f"   ⚠️ probe deal NOT deleted — remove {deal_id} "
                          f"manually: {e}")
                print(f"\n→ .env:\n  ZOHO_CRM_HOME_STUDIO_LAYOUT=\"{LAYOUT}\"")
                if stage != config.ZOHO_CRM_DEAL_DEFAULT_STAGE:
                    print(f"  ZOHO_CRM_HOME_STUDIO_STAGE=\"{stage}\"")
                return
            print(f"❌ stage {stage!r} rejected: {entry}")
        except Exception as e:
            print(f"❌ stage {stage!r} rejected: {e}")

    print("\nNo candidate stage worked → the Home Studio layout needs its "
          "pipeline configured in the CRM (Setup → Pipelines → Layout: Home "
          "Studio) before deals can be created on it. Leave "
          "ZOHO_CRM_HOME_STUDIO_LAYOUT unset until then.")


if __name__ == "__main__":
    asyncio.run(main())
