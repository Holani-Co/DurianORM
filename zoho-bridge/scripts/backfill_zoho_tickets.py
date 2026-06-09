#!/usr/bin/env python3
"""
One-shot migration: move the legacy `zoho_ticket` (singular) custom-attribute
into the `zoho_tickets` array on every conversation, then drop the singular key.

Run ONCE after deploying the multi-ticket array support introduced in PR
"feat(zoho-bridge): Chatwoot ↔ Zoho deep-link + multi-ticket history per
conversation". The change to main.py stops WRITING the singular key on new
tickets; this script cleans up old conversations that still have it.

Safe to re-run — idempotent. Conversations that no longer have a `zoho_ticket`
key are skipped; conversations whose legacy ticket is already inside the array
are still de-duped and have the singular key removed.

Usage (on the VM, from the zoho-bridge directory):

    # Preview without writing anything
    python3 scripts/backfill_zoho_tickets.py --dry-run

    # Actually migrate
    python3 scripts/backfill_zoho_tickets.py

Exit code 0 on success, 1 if any conversation failed (other conversations are
still processed — failures don't abort the run).
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Allow `python3 scripts/backfill_zoho_tickets.py` from the zoho-bridge dir
# AND `python3 backfill_zoho_tickets.py` from inside scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import config  # noqa: E402


# ── HTTP helpers ──────────────────────────────────────────────────────────
def _headers() -> dict:
    return {
        "api_access_token": config.CHATWOOT_API_TOKEN,
        "Content-Type":     "application/json",
    }


def _conv_url(suffix: str = "") -> str:
    return (
        f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/"
        f"{config.CHATWOOT_ACCOUNT_ID}/conversations{suffix}"
    )


async def list_conversations(client: httpx.AsyncClient):
    """Yield every conversation in the account, deduped by id.

    Chatwoot's /conversations endpoint requires BOTH a `status` and an
    `assignee_type` filter to be exhaustive: passing just one returns only a
    slice. We iterate the cross product (4 statuses × 2 assignment buckets =
    8 paginated walks) and dedupe by conversation id in case a conv flips
    status mid-run. This is O(N) calls per conversation regardless — the
    Chatwoot API doesn't expose a single "give me everything" filter, so the
    matrix walk is the official idiom here.

    Pagination: stops when a page comes back with zero items. We don't trust
    `data.meta.all_count` because some Chatwoot caches return stale totals.
    """
    seen: set[int] = set()
    for status in ("open", "resolved", "pending", "snoozed"):
        for assignee_type in ("assigned", "unassigned"):
            page = 1
            while True:
                r = await client.get(
                    _conv_url(),
                    headers=_headers(),
                    params={
                        "page":          page,
                        "status":        status,
                        "assignee_type": assignee_type,
                    },
                )
                if r.status_code >= 300:
                    print(f"[backfill] list status={status} "
                          f"assignee={assignee_type} page={page} failed: "
                          f"{r.status_code} {r.text[:200]}")
                    break  # move to next status/assignee combo

                body = r.json() or {}
                data = body.get("data") or {}
                payload = data.get("payload") or []
                if not payload:
                    break

                for conv in payload:
                    cid = conv.get("id")
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    yield conv
                page += 1


async def fetch_conversation(client: httpx.AsyncClient, conv_id: int) -> dict:
    """Fetch a single conversation by id — we need the FULL custom_attributes
    object, which the list endpoint sometimes truncates / omits depending on
    the Chatwoot version."""
    r = await client.get(_conv_url(f"/{conv_id}"), headers=_headers())
    if r.status_code >= 300:
        raise RuntimeError(f"GET conv {conv_id} failed [{r.status_code}]: {r.text}")
    return r.json() or {}


async def replace_custom_attributes(client: httpx.AsyncClient,
                                    conv_id: int, attrs: dict) -> None:
    """OVERWRITE the conversation's custom_attributes column with `attrs`.

    Distinct from the bridge's merge_custom_attributes — that one preserves
    existing keys, which means it can never DELETE a key. The backfill
    explicitly needs deletion semantics (removing `zoho_ticket`), so we send
    the full new dict and let it replace the column."""
    r = await client.post(
        _conv_url(f"/{conv_id}/custom_attributes"),
        headers=_headers(),
        json={"custom_attributes": attrs},
    )
    if r.status_code >= 300:
        raise RuntimeError(
            f"POST custom_attributes {conv_id} failed "
            f"[{r.status_code}]: {r.text}"
        )


# ── Core migration logic ──────────────────────────────────────────────────
def _ticket_key(t: dict) -> str:
    """Stable identity for dedup: prefer Zoho's ticket id, fall back to
    ticket number. Anything without either is treated as a unique entry."""
    return str((t or {}).get("id") or (t or {}).get("number") or id(t))


def _merge_legacy_into_array(legacy: dict, tickets: list[dict]) -> list[dict]:
    """Insert the legacy singular ticket into the array if it's not already
    there. Position: APPEND (treat the legacy entry as historical, not the
    latest) — newer entries from real future writes will be inserted at the
    head as usual.
    """
    if not isinstance(legacy, dict) or not legacy.get("id"):
        return tickets
    legacy_key = _ticket_key(legacy)
    if any(_ticket_key(t) == legacy_key for t in tickets):
        return tickets
    return tickets + [legacy]


async def migrate_conversation(client: httpx.AsyncClient,
                               conv_summary: dict,
                               dry_run: bool) -> str:
    """Return a status string: 'migrated', 'skipped_no_legacy',
    'skipped_unchanged', or 'failed'."""
    conv_id = conv_summary.get("id")
    if not conv_id:
        return "skipped_no_legacy"

    # Refetch — list endpoint can omit nested keys in custom_attributes
    # depending on the Chatwoot version, especially for resolved/snoozed convs.
    try:
        full = await fetch_conversation(client, conv_id)
    except Exception as e:
        print(f"[fail] conv {conv_id}: refetch failed: {e}")
        return "failed"

    attrs = dict(full.get("custom_attributes") or {})
    legacy = attrs.get("zoho_ticket")
    if not legacy:
        return "skipped_no_legacy"

    tickets = list(attrs.get("zoho_tickets") or [])
    new_tickets = _merge_legacy_into_array(legacy, tickets)

    # Build the post-migration attrs: array set, singular dropped.
    new_attrs = {k: v for k, v in attrs.items() if k != "zoho_ticket"}
    new_attrs["zoho_tickets"] = new_tickets

    if new_attrs == attrs:
        # Nothing actually changed (shouldn't happen given legacy was set,
        # but defensive).
        return "skipped_unchanged"

    if dry_run:
        print(f"[dry-run] conv {conv_id}: would migrate "
              f"(legacy id={legacy.get('id')}, "
              f"array size {len(tickets)} → {len(new_tickets)}, "
              f"drops singular key)")
        return "migrated"

    try:
        await replace_custom_attributes(client, conv_id, new_attrs)
    except Exception as e:
        print(f"[fail] conv {conv_id}: write failed: {e}")
        return "failed"

    print(f"[ok] conv {conv_id}: migrated "
          f"(array size now {len(new_tickets)})")
    return "migrated"


# ── Entry point ───────────────────────────────────────────────────────────
async def main(dry_run: bool) -> int:
    counts = {
        "migrated":          0,
        "skipped_no_legacy": 0,
        "skipped_unchanged": 0,
        "failed":            0,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        async for conv in list_conversations(client):
            result = await migrate_conversation(client, conv, dry_run)
            counts[result] = counts.get(result, 0) + 1

    print()
    print("=== backfill summary ===")
    for k, v in counts.items():
        print(f"  {k:<20s} {v}")
    if dry_run:
        print("  (dry-run — nothing was written)")

    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill legacy zoho_ticket (singular) → zoho_tickets array.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(dry_run=args.dry_run)))
