# Client for the durian.in website review-reply API.
#
#   GET  /api/front/v1/review-reply/        → all reviews (excl. deleted/blocked)
#   POST /api/front/v1/review-reply/ {id, reply} → write/update a reply
#
# Auth is a static token in an Authorization header. Base URL + token come from
# config (WEBSITE_REVIEWS_API_BASE_URL / _API_TOKEN). Kept deliberately thin —
# the poller (website_reviews_poller.py) owns all the ingest logic.

import httpx

import config

_PATH = "/api/front/v1/review-reply/"


def _headers() -> dict:
    return {
        "Authorization": f"Token {config.WEBSITE_REVIEWS_API_TOKEN}",
        "Content-Type": "application/json",
    }


def _url() -> str:
    return f"{config.WEBSITE_REVIEWS_API_BASE_URL}{_PATH}"


def _normalize(raw: dict) -> dict:
    """Map the API's row onto the field names the poller/state use. Ratings and
    ids are coerced defensively — the API is external and may send strings."""
    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    reply = (raw.get("reply") or "").strip()
    return {
        "review_id": str(raw.get("id")),
        "product_id": raw.get("product_id"),
        "user_id": raw.get("user_id"),
        "title": (raw.get("title") or "").strip(),
        "comment": (raw.get("review") or "").strip(),
        "stars": _int(raw.get("rating")),
        "reply": reply,
        "already_replied": bool(reply) or bool(raw.get("replied")),
        "created": raw.get("created") or "",
        # `modified` moves when the review (or its reply) changes — the poller
        # uses it the way the Google poller uses updateTime, to detect edits.
        "update_time": raw.get("modified") or "",
    }


async def list_reviews() -> list[dict]:
    """Every non-deleted/blocked review, normalized. The API returns the full
    list (no since/pagination), so the poller dedups against its seen-state."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(_url(), headers=_headers())
        r.raise_for_status()
        body = r.json()
    if str(body.get("status")) != "1":
        raise RuntimeError(f"website reviews list failed: {body.get('msg')}")
    data = body.get("data") or []
    rows = data if isinstance(data, list) else [data]
    return [_normalize(row) for row in rows if row]


async def post_reply(review_id, comment: str) -> dict:
    """Write/update the reply on a review by id. Raises on a non-success body."""
    payload = {"id": int(review_id), "reply": comment}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(_url(), headers=_headers(), json=payload)
        r.raise_for_status()
        body = r.json()
    if str(body.get("status")) != "1":
        raise RuntimeError(f"website review reply failed [{review_id}]: {body.get('msg')}")
    return body.get("data") or {}
