# BMS order-lookup client (the client's order-management system).
#
# Two read-only endpoints, discovered by probing (2026-07-10):
#   GET /api/get-orders-by-order-id/{id}/    → response is a SINGLE order dict
#   GET /api/get-orders-by-customer/{phone}/ → response is a LIST of orders
#
# Quirks the code below exists to absorb:
#   • Trailing slash is mandatory — without it the API 301-redirects, and
#     redirects can drop the Authorization header. URLs are built WITH it.
#   • "Not found" is HTTP 200 with {"status": "failed"} — check the status
#     field, never the HTTP code alone.
#   • OrderProduct is an ARRAY on the by-id endpoint but a single OBJECT on
#     the by-phone endpoint. _normalize_order() flattens both.
#   • DRF TokenAuthentication: the Authorization header value is passed
#     through VERBATIM from BMS_API_TOKEN (it already includes its scheme
#     prefix) — no "Bearer" or other prefix is added here.
#
# Best-effort like zoho.py: every public function returns None/[] on any
# failure and never raises — an order lookup must never break the webhook.

import asyncio

import httpx

import config


def _headers() -> dict:
    return {
        "Authorization": config.BMS_API_TOKEN,
        "Content-Type":  "application/json",
    }


async def _get(path: str, *, retries: int = 2) -> dict | None:
    """GET {base}{path} → parsed JSON body, or None on any failure.

    Retries a TRANSIENT failure (network error / timeout / 5xx) a couple of
    times before giving up: a flaky or momentarily-slow BMS must never make us
    wrongly tell a customer their order 'could not be verified'. A 4xx (genuine
    client error / not found) is returned as None immediately — no retry."""
    if not (config.BMS_API_BASE_URL and config.BMS_API_TOKEN):
        print("[bms] lookup skipped — BMS_API_BASE_URL/TOKEN not configured")
        return None
    url = f"{config.BMS_API_BASE_URL.rstrip('/')}{path}"
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(url, headers=_headers())
            if r.status_code < 300:
                return r.json()
            if r.status_code < 500:          # 4xx → genuine, don't retry
                print(f"[bms] GET {path} → HTTP {r.status_code}: {r.text[:200]!r}")
                return None
            # 5xx → transient server error, fall through to retry
            print(f"[bms] GET {path} → HTTP {r.status_code} "
                  f"(attempt {attempt + 1}/{retries + 1})")
        except Exception as e:
            print(f"[bms] GET {path} failed "
                  f"(attempt {attempt + 1}/{retries + 1}): {type(e).__name__}: {e}")
        if attempt < retries:
            await asyncio.sleep(0.5 * (attempt + 1))
    return None


def _normalize_order(entry: dict) -> dict | None:
    """Flatten one {OrderList, OrderProduct} record into the small dict the
    bridge passes around (notes, LLM grounding, sidebar later). Field names
    chosen for readability over fidelity — the raw record stays in BMS."""
    if not isinstance(entry, dict):
        return None
    ol = entry.get("OrderList") or {}
    if not ol:
        return None

    # OrderProduct: dict on by-phone, list on by-id. Either way → list.
    op = entry.get("OrderProduct") or []
    if isinstance(op, dict):
        op = [op]
    items = []
    for p in op:
        prod = p.get("Product") or {}
        items.append({
            "name":     prod.get("name") or "",
            "sku":      prod.get("sku") or "",
            "quantity": p.get("quantity") or "",
            "price":    p.get("product_price") or "",
            "image":    (prod.get("ProductImage") or {}).get("image") or "",
        })

    return {
        "order_id":        ol.get("id") or "",
        "order_number":    ol.get("custom_order_id") or "",   # customer-facing "D#75833"
        "channel_order":   ol.get("channel_order_id") or "",
        "status":          ol.get("order_status_name") or "",
        "created_date":    ol.get("created_date") or "",
        "delivery_date":   ol.get("delivery_date") or "",
        "payment_method":  ol.get("payment_method_name") or "",
        "gross_amount":    ol.get("gross_amount") or "",
        "paid_amount":     ol.get("paid_amount") or "",
        "customer_name":   ol.get("billing_name") or "",
        "customer_email":  ol.get("billing_email_address") or "",
        "customer_phone":  ol.get("billing_phone") or "",
        "delivery_city":   ol.get("delivery_city") or "",
        "delivery_state":  ol.get("delivery_state") or "",
        "delivery_address": ol.get("delivery_street_address") or "",
        "items":           items,
    }


async def get_order_by_id(order_id: str) -> dict | None:
    """Fetch one order by its numeric BMS id (customers quote it as
    'D#75833' or plain '75833' — both accepted). None when not found."""
    order_id = str(order_id).strip().upper().lstrip("D").lstrip("#")
    if not order_id.isdigit():
        return None
    body = await _get(f"/api/get-orders-by-order-id/{order_id}/")
    if not body or body.get("status") != "success":
        if body:
            print(f"[bms] order {order_id}: {body.get('message') or 'not found'}")
        return None
    return _normalize_order(body.get("response") or {})


async def get_orders_by_phone(phone: str, limit: int = 5) -> list[dict]:
    """Fetch a customer's orders by their 10-digit phone number, newest
    first, capped at `limit`. [] when none / on failure.

    CAUTION for callers: BMS returns whatever orders carry that phone —
    including orders where a DIFFERENT customer typed a junk number at
    checkout (probed: 9999999999 returns a real stranger's orders). Results
    are for the AGENT's eyes (private note); never auto-send them."""
    phone = str(phone).strip()
    if not (phone.isdigit() and len(phone) == 10):
        return []
    body = await _get(f"/api/get-orders-by-customer/{phone}/")
    if not body or body.get("status") != "success":
        if body:
            print(f"[bms] phone {phone}: {body.get('message') or 'no orders'}")
        return []
    raw = body.get("response") or []
    if isinstance(raw, dict):
        raw = [raw]
    orders = [o for o in (_normalize_order(e) for e in raw) if o]
    orders.sort(key=lambda o: o.get("created_date") or "", reverse=True)
    return orders[:limit]
