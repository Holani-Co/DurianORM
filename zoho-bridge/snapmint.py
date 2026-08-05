# Snapmint EMI-calculation client — product-wise EMI plans for the EMI flow.
#
# API (from the client doc, verified against its sample response):
#   GET {base}/api/interest_calculations/
#       ?price=<rupees>&subvention=<true|false>&skuid=<SKU>&merchant_id=<id>&udf1=
#   Response:
#     { "skuid", "min_interest_rate", "emi_available", "down_payment" (str),
#       "processing_fee",
#       "emis": [ {"emi"(str), "months", "total_payment"(str), "interest",
#                  "is_zero_percent"} ] }
#
# Notes grounded in the doc:
#   • Prices are in RUPEES (NOT paisa — that was the other vendor, Pinelab).
#   • The doc warns of DUPLICATE plans in `emis` → we dedup by tenure.
# Best-effort: any failure returns None so an EMI enquiry never breaks the
# webhook (same contract as bms.py).

import httpx

import config


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def get_emi(price, skuid: str, merchant_id=None,
                  subvention=None, udf1: str = "") -> dict | None:
    """Normalised EMI info for a (price, skuid), or None on any failure:
        { emi_available, down_payment, processing_fee, min_interest_rate,
          plans: [ {months, emi, total_payment, interest, zero_cost} ] }
    plans are deduped by tenure and sorted ascending."""
    base = (config.SNAPMINT_BASE_URL or "").rstrip("/")
    mid = merchant_id if merchant_id is not None else config.SNAPMINT_MERCHANT_ID
    rupees = _f(price)
    if not (base and mid and rupees and skuid):
        print("[snapmint] skipped — missing base_url / merchant_id / price / skuid")
        return None
    sub = config.SNAPMINT_SUBVENTION if subvention is None else bool(subvention)
    params = {"price": int(round(rupees)), "subvention": str(sub).lower(),
              "udf1": udf1 or "", "skuid": skuid, "merchant_id": mid}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(f"{base}/api/interest_calculations/", params=params)
        if r.status_code != 200:
            print(f"[snapmint] HTTP {r.status_code} for skuid={skuid!r}: {r.text[:200]!r}")
            return None
        data = r.json()
    except Exception as e:
        print(f"[snapmint] request failed for skuid={skuid!r}: {type(e).__name__}: {e}")
        return None

    plans, seen = [], set()
    for e in data.get("emis") or []:
        months = e.get("months")
        if months in seen:           # doc: duplicate plans appear — keep one per tenure
            continue
        seen.add(months)
        plans.append({
            "months": int(months) if months is not None else None,
            "emi": _f(e.get("emi")),
            "total_payment": _f(e.get("total_payment")),
            "interest": _f(e.get("interest")) or 0,
            "zero_cost": bool(e.get("is_zero_percent")),
        })
    plans.sort(key=lambda p: p["months"] or 0)

    return {
        "emi_available": bool(data.get("emi_available")),
        "down_payment": _f(data.get("down_payment")),
        "processing_fee": _f(data.get("processing_fee")) or 0,
        "min_interest_rate": _f(data.get("min_interest_rate")),
        "plans": plans,
    }
