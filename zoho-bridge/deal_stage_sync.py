# Daily deal-stage sync. Once a day (before the 9 AM ORM report) it reads each
# open ORM deal's CURRENT Zoho Stage and writes it onto the deal's Chatwoot
# conversation (custom attribute `crm_deal_stage`), so the live stage is visible
# in the ORM and the daily report. Read-only in Zoho; CRM is never modified.
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import chatwoot
import config
import zoho_crm

IST = ZoneInfo("Asia/Kolkata")


def _seconds_until_next_run() -> float:
    now = datetime.now(IST)
    target = now.replace(hour=config.DEAL_STAGE_SYNC_HOUR,
                         minute=config.DEAL_STAGE_SYNC_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def sync_once() -> dict:
    """One sweep: refresh crm_deal_stage on every active deal conversation."""
    convs = await chatwoot.list_deal_conversations(config.DEAL_STAGE_SYNC_MAX_AGE_DAYS)
    updated = skipped = errors = 0
    for conv in convs:
        attrs = conv.get("custom_attributes") or {}
        deal_id = str(attrs.get("crm_deal_id") or "")
        # Terminal deals are frozen — no point re-reading a closed stage.
        if not deal_id or attrs.get("crm_deal_stage") in config.ZOHO_CRM_DEAL_CLOSED_STAGES:
            skipped += 1
            continue
        try:
            stage = await zoho_crm.get_deal_stage(deal_id)
        except Exception as e:  # get_deal_stage is best-effort, but belt-and-braces
            errors += 1
            print(f"[stage-sync] read failed for deal {deal_id}: {e}")
            stage = None
        if stage and stage != attrs.get("crm_deal_stage"):
            try:
                await chatwoot.merge_custom_attributes(int(conv["id"]), {"crm_deal_stage": stage})
                updated += 1
            except Exception as e:
                errors += 1
                print(f"[stage-sync] write failed for conv {conv.get('id')}: {e}")
        else:
            skipped += 1
        await asyncio.sleep(config.DEAL_STAGE_SYNC_PAUSE_SECONDS)
    summary = {"deals": len(convs), "updated": updated, "skipped": skipped, "errors": errors}
    print(f"[stage-sync] sweep done: {summary}")
    return summary


async def run_forever():
    """Background loop. Boot-safe: no-ops if disabled."""
    if not config.DEAL_STAGE_SYNC_ENABLED:
        print("[stage-sync] disabled (DEAL_STAGE_SYNC_ENABLED not true) — not started")
        return
    print(f"[stage-sync] started · daily at "
          f"{config.DEAL_STAGE_SYNC_HOUR:02d}:{config.DEAL_STAGE_SYNC_MINUTE:02d} IST")
    while True:
        await asyncio.sleep(_seconds_until_next_run())
        try:
            await sync_once()
        except Exception as e:
            print(f"[stage-sync] sweep error: {e}")
        await asyncio.sleep(60)  # clear the run minute so we don't re-fire immediately
