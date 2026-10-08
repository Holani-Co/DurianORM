# Build the unified store registry (data/store_registry.json) from the client's
# store-locator XLSX export, then overlay the richer "Master ORM Store List".
# One row per store, one complete schema across all verticals — the FLOOR that
# store_locator reads (UI overrides merge on top).
#
# Decisions baked in (see docs/store-locator-spec.md):
#   - Active stores only.
#   - A store's verticals = its "Verticle" PLUS any vertical in its "Service"
#     column (a furniture store that also services doors counts for doors).
#   - FHC owner ids are blank in this export → merged from routing_rules.yaml
#     (crm_owner_routing_homestudio) by matching the studio location.
#   - map_url generated from lat/lon (the Street View column is mostly empty).
#
# Master ORM overlay (second pass): the old export is the ROUTING spine (it has
# lat/lon, a clean pincode column and ZOHO owner ids); the Master list is the
# richer DISPLAY source (real goo.gl map links, holiday, parking, store type,
# area, floors, escalator, current address/timing/manager/phone/email). The two
# are joined by a REVIEWED mapping (data/master_store_map.json: master name →
# registry id) so customer-facing cards are never silently mis-joined. Master
# rows with no registry match are added as NEW stores, geocoded from the pincode
# embedded in their address where one is present (else parked without coords).
# "Coming soon!" rows are excluded from the live floor.
#
# Re-run whenever the client sends a fresh export or Master list.

import json
import re
from pathlib import Path

import openpyxl
import yaml

import pincode_resolver   # stdlib-only geocoder (no config/env) — pincode → lat/lon

_HERE = Path(__file__).parent
_XLSX = _HERE / "data" / "store_locator_export2026_03_18_11_22_08_242251 (1).xlsx"
_MASTER = _HERE / "data" / "Master ORM Store List.xlsx"
_MASTER_MAP = _HERE / "data" / "master_store_map.json"
_OUT = _HERE / "data" / "store_registry.json"
_ROUTING = _HERE / "routing_rules.yaml"

# Sheet label → internal vertical token used across the codebase.
_VERT = {"furniture": "furniture", "door": "doors", "doors": "doors",
         "full home customisation": "fhc", "fhc": "fhc"}

# Master ORM list columns, BY POSITION — the sheet has DUPLICATE headers (two
# "Vertical" at 2 & 5, two "Week Off" at 18 & 20), so hdr.index() would grab the
# wrong column. Positions are stable across the client's exports.
_M = {"vert": 2, "service": 3, "name": 4, "store_type": 6, "area": 7,
      "floors": 8, "addr": 9, "city": 10, "timing": 11, "phone": 12,
      "email": 13, "holiday": 14, "map": 15, "manager": 17, "parking": 21,
      "escalator": 22}

# Display fields the Master list owns — overlaid onto a matched registry row,
# and the editable set the Settings → Stores screen exposes for these.
_MASTER_DISPLAY_FIELDS = ("card_name", "address", "city", "timing", "phone",
                          "email", "manager", "map_url", "holiday", "parking",
                          "store_type", "area", "floors", "escalator")


def _vert_tokens(*cells) -> list[str]:
    """Normalise a Verticle cell and/or a Service cell into internal tokens."""
    out: list[str] = []
    for cell in cells:
        for part in re.split(r"[,/;]", str(cell or "")):
            key = part.strip().lower()
            tok = _VERT.get(key)
            if tok and tok not in out:
                out.append(tok)
    return out


def _slug(name: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s or fallback


def _map_url(lat, lon):
    if lat in (None, "") or lon in (None, ""):
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def _load_fhc_owners() -> dict:
    """Studio location-fragment → owner_id, from crm_owner_routing_homestudio.
    Keyed by a distinctive fragment ('kirti nagar') so we can match the sheet's
    longer store names."""
    try:
        rules = yaml.safe_load(_ROUTING.read_text()) or {}
    except Exception as e:
        print(f"[registry] could not read routing_rules.yaml: {e}")
        return {}
    homestudio = rules.get("crm_owner_routing_homestudio") or {}
    owners = {}
    for loc, info in homestudio.items():
        oid = str((info or {}).get("owner_id") or "").strip()
        if not oid:
            continue
        frag = loc.split("-")[-1].strip().lower()   # "Delhi - Kirti Nagar" → "kirti nagar"
        owners[frag] = oid
    return owners


def _mcell(r, key: str) -> str:
    i = _M[key]
    return str(r[i]).strip() if i < len(r) and r[i] not in (None, "") else ""


def _master_phone(r) -> str:
    # openpyxl reads the number cell as a float, e.g. '7226878335.0'.
    raw = _mcell(r, "phone")
    return raw[:-2] if re.fullmatch(r"\d+\.0", raw) else raw


def _master_manager(r) -> str:
    # "Manager Details" is "Name\nphone[\nemail]" — the display name is line 1.
    first = next((ln.strip() for ln in _mcell(r, "manager").splitlines() if ln.strip()), "")
    return first


def _extract_pincode(addr: str) -> str:
    # Last standalone 6-digit run (first digit 1-9) — the pincode usually trails
    # an Indian address. Returns "" when none is present.
    hits = re.findall(r"(?<!\d)([1-9]\d{5})(?!\d)", addr or "")
    return hits[-1] if hits else ""


def _master_display(r) -> dict:
    """The customer-facing / editable display fields from one Master row (only
    the non-empty ones, so an overlay never blanks a good registry value)."""
    name = _mcell(r, "name")
    mp = _mcell(r, "map")
    d = {
        "card_name": name.replace("Durian ", "").strip() or name,
        "address": _mcell(r, "addr").strip(),
        "city": _mcell(r, "city"),
        "timing": _mcell(r, "timing"),
        "phone": _master_phone(r),
        "email": _mcell(r, "email"),
        "manager": _master_manager(r),
        "holiday": _mcell(r, "holiday"),
        "parking": _mcell(r, "parking"),
        "store_type": _mcell(r, "store_type").lower(),
        "area": _mcell(r, "area"),
        "floors": _mcell(r, "floors"),
        "escalator": _mcell(r, "escalator"),
        # Prefer the Master's real goo.gl / google place link over the registry's
        # generated maps/search URL; both hosts are already agent-allowlisted.
        "map_url": mp if mp.startswith("http") else "",
    }
    return {k: v for k, v in d.items() if v}


def _merge_master(stores: list[dict]) -> list[dict]:
    """Second pass: overlay the Master ORM list's display fields onto matched
    registry stores (join via the reviewed map), and append Master-only stores
    as NEW entries, geocoding the pincode embedded in their address."""
    try:
        mapping = (json.loads(_MASTER_MAP.read_text()) or {}).get("mapping") or {}
    except Exception as e:  # noqa: BLE001
        print(f"[registry] master map unreadable ({e}) — skipping Master overlay")
        return stores
    try:
        ws = openpyxl.load_workbook(_MASTER, data_only=True)["Master ORM Store List"]
    except Exception as e:  # noqa: BLE001
        print(f"[registry] Master list unreadable ({e}) — skipping Master overlay")
        return stores

    by_id = {s["id"]: s for s in stores}
    existing_slugs = set(by_id)
    overlaid = new_added = parked = coming = unknown = 0
    for r in list(ws.iter_rows(values_only=True))[1:]:
        name = _mcell(r, "name")
        if not name:
            continue                                  # phantom blank row
        if "coming" in _mcell(r, "holiday").lower():
            coming += 1
            continue                                  # not open yet → off the floor
        disp = _master_display(r)
        rid = mapping.get(name)
        if rid:
            if rid in by_id:
                by_id[rid].update(disp)               # display overlay, routing kept
                overlaid += 1
            else:
                unknown += 1                           # stale map entry → ignore
            continue
        # Master-only → NEW store. Route it off the pincode in its address.
        verts = _vert_tokens(_mcell(r, "vert"), _mcell(r, "service"))
        if not verts:
            continue
        pin = _extract_pincode(disp.get("address", ""))
        loc = pincode_resolver.coords(pin) if pin else None
        sid = _slug(name, f"master-{new_added}")
        while sid in existing_slugs:
            sid += "-2"
        existing_slugs.add(sid)
        store = {
            "id": sid, "name": name,
            "verticals": verts,
            "primary_vertical": (_vert_tokens(_mcell(r, "vert")) or verts)[0],
            "store_type": disp.get("store_type", ""),
            "city": disp.get("city", ""), "location": "", "state": "",
            "address": disp.get("address", ""),
            "pincode": pin,
            "lat": loc[0] if loc else None,
            "lon": loc[1] if loc else None,
            "manager": disp.get("manager", ""), "phone": disp.get("phone", ""),
            "email": disp.get("email", ""), "timing": disp.get("timing", ""),
            "facility": "", "map_url": disp.get("map_url", ""), "image_url": "",
            "crm_owner_id": "",
            "holiday": disp.get("holiday", ""), "parking": disp.get("parking", ""),
            "area": disp.get("area", ""), "floors": disp.get("floors", ""),
            "escalator": disp.get("escalator", ""),
        }
        stores.append(store)
        new_added += 1
        if not loc:
            parked += 1
    print(f"[registry] master overlay: {overlaid} display-updated, {new_added} new "
          f"({parked} parked w/o coords), {coming} coming-soon excluded, "
          f"{unknown} stale-map ignored")
    return stores


def build() -> list[dict]:
    wb = openpyxl.load_workbook(_XLSX, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(h).strip() if h is not None else "" for h in rows[0]]

    def idx(name):
        return hdr.index(name)

    cN, cPh, cAddr, cCity, cLoc, cState, cPin, cLat, cLon, cMgr, cEmail, \
        cFac, cTime, cType, cStreet, cSlug, cVert, cImg2, cOwner, cSvc, cStatus = (
            idx("Name"), idx("Phone Number"), idx("Adderss"), idx("City"),
            idx("Location"), idx("State"), idx("Pincode"), idx("Latitude"),
            idx("Longitude"), idx("Manager Name"), idx("Store Email"),
            idx("Store Facility"), idx("Store Timing"), idx("Store Type"),
            idx("Street View"), idx("Slug"), idx("Verticle"),
            idx("Store Image Link"), idx("ZOHO Owner ID"), idx("Service"),
            idx("Status"))

    fhc_owners = _load_fhc_owners()
    out, skipped_inactive, fhc_merged, no_owner = [], 0, 0, 0

    for n, r in enumerate(rows[1:], start=2):
        if not any(c not in (None, "") for c in r):
            continue
        if str(r[cStatus]).strip().lower() != "active":
            skipped_inactive += 1
            continue
        verticals = _vert_tokens(r[cVert], r[cSvc])
        if not verticals:
            continue
        name = str(r[cN]).strip()
        try:
            lat = float(r[cLat]); lon = float(r[cLon])
        except (TypeError, ValueError):
            lat = lon = None
        owner = str(r[cOwner]).strip() if r[cOwner] not in (None, "") else ""
        # FHC rows carry no owner in this export → merge from routing_rules.
        if not owner and "fhc" in verticals:
            low = name.lower()
            for frag, oid in fhc_owners.items():
                if frag and frag in low:
                    owner = oid
                    fhc_merged += 1
                    break
        if not owner:
            no_owner += 1
        out.append({
            "id": _slug(r[cSlug] or name, f"store-{n}"),
            "name": name,
            "card_name": name.replace("Durian ", "").strip(),
            "verticals": verticals,
            "primary_vertical": _vert_tokens(r[cVert])[0] if _vert_tokens(r[cVert]) else verticals[0],
            "store_type": str(r[cType] or "").strip().lower(),
            "city": str(r[cCity] or "").strip(),
            "location": str(r[cLoc] or "").strip(),
            "state": str(r[cState] or "").strip(),
            "address": str(r[cAddr] or "").strip(),
            "pincode": re.sub(r"\D", "", str(r[cPin] or "")) or "",
            "lat": lat, "lon": lon,
            "manager": str(r[cMgr] or "").strip(),
            "phone": str(r[cPh] or "").strip(),
            "email": str(r[cEmail] or "").strip(),
            "timing": str(r[cTime] or "").strip(),
            "facility": str(r[cFac] or "").strip(),
            "map_url": (str(r[cStreet]).strip() if r[cStreet] not in (None, "")
                        else _map_url(lat, lon)),
            "image_url": str(r[cImg2] or "").strip(),
            "crm_owner_id": owner,
        })

    print(f"[registry] export spine: {len(out)} active stores "
          f"(skipped {skipped_inactive} inactive; FHC owners merged {fhc_merged}; "
          f"no owner id {no_owner})")
    out = _merge_master(out)
    _OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"[registry] wrote {len(out)} stores → {_OUT.name}")
    return out


if __name__ == "__main__":
    build()
