# Daily in-process refresh of the availability snapshot (Phase 3).
#
# The client updates their 'Inventory Tracker' workbook daily. Point
# INVENTORY_XLSX at wherever that file lands on this box and this task rebuilds
# data/inventory.local.json from it every INVENTORY_SYNC_INTERVAL_HOURS, then
# hot-swaps it into the resolver — no bridge restart, and the committed
# data/inventory.json is left untouched so `git pull` never conflicts.
#
# Runs as an asyncio background task started at app startup (mirrors the reviews
# poller). It is defensive: if the workbook is missing or the build errors, it
# logs and keeps the last good snapshot — a failed refresh never wipes stock data
# (though the 48h staleness guard in inventory.py will eventually make answers
# fall back to "check with the team" if the file stops refreshing).

import asyncio
from pathlib import Path

import build_inventory
import config
import inventory

_LOCAL_OUT = Path(__file__).parent / "data" / "inventory.local.json"


async def _rebuild_once() -> bool:
    xlsx = (config.INVENTORY_XLSX or "").strip()
    if not xlsx or not Path(xlsx).exists():
        print(f"[inventory-sync] workbook {xlsx or '(unset)'} not found — "
              "keeping last snapshot")
        return False
    try:
        # openpyxl + JSON write are blocking; keep them off the event loop.
        await asyncio.to_thread(build_inventory.build, xlsx, "", str(_LOCAL_OUT))
        inventory.invalidate()
        print(f"[inventory-sync] rebuilt {_LOCAL_OUT.name} from {xlsx} "
              f"(generated_at {inventory.generated_at()})")
        return True
    except Exception as e:
        print(f"[inventory-sync] rebuild failed: {type(e).__name__}: {e} — "
              "keeping last snapshot")
        return False


async def run_forever() -> None:
    """Rebuild on startup, then once per interval. No-ops (never started) unless
    both the feature and a workbook path are configured."""
    if not config.INVENTORY_ENABLED:
        print("[inventory-sync] disabled (INVENTORY_ENABLED not true) — not started")
        return
    if not (config.INVENTORY_XLSX or "").strip():
        print("[inventory-sync] INVENTORY_XLSX not set — not started "
              "(using the committed snapshot as-is)")
        return
    interval = max(1, int(config.INVENTORY_SYNC_INTERVAL_HOURS)) * 3600
    print(f"[inventory-sync] started · every {config.INVENTORY_SYNC_INTERVAL_HOURS}h "
          f"· workbook {config.INVENTORY_XLSX}")
    while True:
        await _rebuild_once()
        await asyncio.sleep(interval)
