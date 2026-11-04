# Google Business Profile (GBP) client: OAuth token cache + list locations,
# list reviews, post replies. Uses plain httpx + a refresh token — no Google
# client library needed.
#
# Reviews still live on the legacy "My Business API v4" endpoint
# (mybusiness.googleapis.com/v4). Accounts + locations are discovered via the
# newer v1 APIs. OAuth scope required: https://www.googleapis.com/auth/business.manage
#
# All calls are best-effort and raise RuntimeError on hard failures so the
# poller can log and continue.

import re
import time
import httpx

import config

_token_cache = {"value": None, "expires_at": 0.0}

# Star rating enum → int
_STARS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}

ACCOUNT_MGMT = "https://mybusinessaccountmanagement.googleapis.com/v1"
BUSINESS_INFO = "https://mybusinessbusinessinformation.googleapis.com/v1"
REVIEWS_V4 = "https://mybusiness.googleapis.com/v4"


# ── OAuth ─────────────────────────────────────────────────────────────────
async def get_access_token() -> str:
    """Return a cached Google access token, refreshing shortly before expiry."""
    if _token_cache["value"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["value"]
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            config.GOOGLE_TOKEN_URL,
            data={
                "client_id":     config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "refresh_token": config.GOOGLE_REFRESH_TOKEN,
                "grant_type":    "refresh_token",
            },
        )
        r.raise_for_status()
        data = r.json()
        if "access_token" not in data:
            raise RuntimeError(f"Google token refresh failed: {data}")
        _token_cache["value"]      = data["access_token"]
        _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
        return _token_cache["value"]


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict:
    token = await get_access_token()
    r = await client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
    if r.status_code == 401:                       # stale token → refresh once
        _token_cache["value"] = None
        token = await get_access_token()
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
    if r.status_code >= 300:
        raise RuntimeError(f"GBP GET {url} failed [{r.status_code}]: {r.text}")
    return r.json()


# ── Discovery ──────────────────────────────────────────────────────────────
async def list_account_ids() -> list[str]:
    """Return GBP account resource ids, e.g. ['accounts/123456789']."""
    async with httpx.AsyncClient(timeout=20) as client:
        data = await _get(client, f"{ACCOUNT_MGMT}/accounts")
        return [a["name"] for a in data.get("accounts", [])]


async def list_locations(account_name: str) -> list[dict]:
    """Return locations under an account. Each: {id, title}. Handles paging."""
    out, page_token = [], None
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            params = {"readMask": "name,title", "pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            data = await _get(client, f"{BUSINESS_INFO}/{account_name}/locations", params)
            for loc in data.get("locations", []):
                # loc["name"] looks like "locations/123" → keep the bare id too
                loc_id = loc["name"].split("/")[-1]
                out.append({"id": loc_id, "name": loc["name"], "title": loc.get("title", loc_id)})
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return out


# ── Reviews ─────────────────────────────────────────────────────────────────
async def list_reviews(account_id: str, location_id: str, page_size: int = 50,
                       summary_out: dict | None = None) -> list[dict]:
    """
    Return recent reviews for a location, newest first. Each normalized to:
      {review_id, stars(int), comment(str), reviewer(str), create_time,
       has_reply(bool), reply_path(str), photos(list[str])}
    account_id / location_id are bare numeric ids (no 'accounts/' prefix).

    When `summary_out` is given, the location's official aggregates from the
    same response are written into it: summary_out["average_rating"] (float)
    and summary_out["total_review_count"] (int). The return value is unchanged.
    """
    url = f"{REVIEWS_V4}/accounts/{account_id}/locations/{location_id}/reviews"
    async with httpx.AsyncClient(timeout=20) as client:
        data = await _get(client, url, {"pageSize": page_size, "orderBy": "updateTime desc"})
    if summary_out is not None:
        summary_out["average_rating"] = data.get("averageRating")
        summary_out["total_review_count"] = int(data.get("totalReviewCount") or 0)
    out = []
    for rv in data.get("reviews", []):
        rid = rv.get("reviewId") or rv.get("name", "").split("/")[-1]
        out.append({
            "review_id":  rid,
            "stars":      _STARS.get(rv.get("starRating", ""), 0),
            "comment":    (rv.get("comment") or "").strip(),
            "reviewer":   (rv.get("reviewer") or {}).get("displayName", "Google user"),
            "create_time": rv.get("createTime", ""),
            # updateTime changes when the customer EDITS their review (same
            # reviewId). The poller compares it against the stored value to
            # detect edits and re-surface them.
            "update_time": rv.get("updateTime", ""),
            "has_reply":  bool(rv.get("reviewReply")),
            "reply_path": f"accounts/{account_id}/locations/{location_id}/reviews/{rid}",
            "photos":     review_photo_urls(rv),
        })
    return out


# Photos the customer attached to a review. Google sends only a small
# thumbnail link per item (`=k-no` sizing suffix, ~20 KB); swapping the
# suffix for `=s0` serves the original upload.
MAX_REVIEW_PHOTOS = 10
_MAX_PHOTO_BYTES = 10 * 1024 * 1024


def _full_size(url: str) -> str:
    tail = url.rsplit("/", 1)[-1]
    return (re.sub(r"=[^/=]*$", "", url) if "=" in tail else url) + "=s0"


def review_photo_urls(rv: dict) -> list[str]:
    urls = []
    for item in rv.get("reviewMediaItems") or []:
        url = item.get("googleUrl") or item.get("thumbnailUrl") or ""
        if url.startswith("https://"):
            urls.append(_full_size(url))
    return urls[:MAX_REVIEW_PHOTOS]


async def download_review_photos(urls: list[str]) -> list[tuple[str, bytes, str]]:
    """Fetch review photos as (filename, bytes, content_type) for a Chatwoot
    attachment. Best-effort: a photo that fails to download, isn't an image or
    is oversized is skipped, never raised."""
    out = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for i, url in enumerate(urls[:MAX_REVIEW_PHOTOS], 1):
            try:
                r = await client.get(url)
                ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
                if r.status_code != 200 or not ctype.startswith("image/") \
                        or len(r.content) > _MAX_PHOTO_BYTES:
                    print(f"[reviews] photo {i} skipped [{r.status_code} {ctype} "
                          f"{len(r.content)}B]")
                    continue
                ext = {"image/png": "png", "image/webp": "webp"}.get(ctype, "jpg")
                out.append((f"review-photo-{i}.{ext}", r.content, ctype))
            except Exception as e:
                print(f"[reviews] photo {i} download failed: {e}")
    return out


async def post_reply(reply_path: str, comment: str) -> dict:
    """PUT a reply onto a review. reply_path = accounts/x/locations/y/reviews/z."""
    url = f"{REVIEWS_V4}/{reply_path}/reply"
    token = await get_access_token()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.put(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"comment": comment},
        )
        if r.status_code == 401:
            _token_cache["value"] = None
            token = await get_access_token()
            r = await client.put(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"comment": comment},
            )
        if r.status_code >= 300:
            raise RuntimeError(f"GBP reply PUT failed [{r.status_code}]: {r.text}")
        return r.json()
