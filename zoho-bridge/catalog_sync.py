# Google Sheet ↔ product catalog sync for Settings → Product Catalog.
#
# The linked sheet (config_store domain "catalog_sync": {sheet_url, auto_sync})
# is re-read once a day at CATALOG_SYNC_HOUR:MINUTE IST while auto-sync is on,
# and on demand from the UI's "Sync now". A sync publishes a new catalog version
# ONLY when the products or prices actually changed, so a daily check of an
# unchanged sheet adds nothing to History. Linking a sheet or toggling auto-sync
# takes effect next run — no restart, no env flag.

import asyncio
from datetime import datetime, timedelta, timezone

import catalog_import
import config
import config_store
import product_catalog

IST = timezone(timedelta(hours=5, minutes=30))

# Last sync outcome, for the UI ("Last checked 06:30 — no changes"). In-memory:
# it only reports, nothing depends on it surviving a restart.
last_status: dict = {}


def settings() -> dict:
    doc = config_store.get_active_override(config_store.CATALOG_SYNC)
    return {"sheet_url": str(doc.get("sheet_url") or ""),
            "auto_sync": bool(doc.get("auto_sync")) and bool(doc.get("sheet_url"))}


def save_settings(sheet_url: str, auto_sync: bool, actor: str = "") -> None:
    cur = settings()
    if cur == {"sheet_url": sheet_url, "auto_sync": bool(auto_sync) and bool(sheet_url)}:
        return
    config_store.publish({"sheet_url": sheet_url, "auto_sync": bool(auto_sync)},
                         note="Google Sheet link" if sheet_url else "Sheet unlinked",
                         actor=actor, domain=config_store.CATALOG_SYNC)


def publish_catalog(doc: dict, note: str, actor: str) -> int:
    vid = config_store.publish(
        doc, note=note, actor=actor, domain=config_store.CATALOG,
        diff={k: v for k, v in catalog_import.diff(product_catalog.products(),
                                                   doc["products"]).items()
              if not k.endswith("_sample")})
    product_catalog.reload()
    return vid


async def sync_once(actor: str = "auto-sync", price_period: str = "") -> dict:
    """Read the linked sheet; publish only if something changed. Returns
    {ok, changed, version_id?, summary?, warnings?, error?} and records it."""
    url = settings()["sheet_url"]
    if not url:
        res = {"ok": False, "error": "No Google Sheet is linked."}
    else:
        try:
            parsed = catalog_import.parse_file(await catalog_import.fetch_sheet(url),
                                               "sheet.xlsx")
            summary = catalog_import.diff(product_catalog.products(), parsed["products"])
            changed = bool(summary["added"] or summary["removed"]
                           or summary["price_changed"] or summary["details_changed"]) \
                or product_catalog.meta().get("source") != "sheet"
            res = {"ok": True, "changed": changed, "summary": summary,
                   "warnings": parsed["warnings"]}
            if changed:
                doc = catalog_import.build_doc(parsed, "sheet", url, price_period)
                res["version_id"] = publish_catalog(doc, "Google Sheet sync", actor)
        except catalog_import.CatalogImportError as e:
            res = {"ok": False, "error": str(e)}
    last_status.clear()
    last_status.update({"at": datetime.now(timezone.utc).isoformat(), "by": actor,
                        "ok": res["ok"], "changed": res.get("changed", False),
                        "error": res.get("error", "")})
    print(f"[catalog-sync] {actor}: ok={res['ok']} changed={res.get('changed')} "
          f"{res.get('error', '')}")
    return res


def _seconds_until_next_run() -> float:
    now = datetime.now(IST)
    target = now.replace(hour=config.CATALOG_SYNC_HOUR, minute=config.CATALOG_SYNC_MINUTE,
                         second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_forever():
    """Daily loop. Always started; each run is a no-op unless a sheet is linked
    with auto-sync on (checked at run time, so the UI switch is live)."""
    print(f"[catalog-sync] daily check at {config.CATALOG_SYNC_HOUR:02d}:"
          f"{config.CATALOG_SYNC_MINUTE:02d} IST (runs only while auto-sync is on)")
    while True:
        await asyncio.sleep(_seconds_until_next_run())
        try:
            if settings()["auto_sync"]:
                await sync_once()
        except Exception as e:
            print(f"[catalog-sync] run error: {e}")
        await asyncio.sleep(60)  # clear the run minute so we don't re-fire immediately
