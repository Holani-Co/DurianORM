# Build the unified store registry (data/store_registry.json) from the client's
# store-locator XLSX export. One row per store, one complete schema across all
# verticals — the FLOOR that store_locator reads (UI overrides merge on top).
#
# Decisions baked in (see docs/store-locator-spec.md):
#   - Active stores only.
#   - A store's verticals = its "Verticle" PLUS any vertical in its "Service"
#     column (a furniture store that also services doors counts for doors).
#   - FHC owner ids are blank in this export → merged from routing_rules.yaml
#     (crm_owner_routing_homestudio) by matching the studio location.
#   - map_url generated from lat/lon (the Street View column is mostly empty).
#
# Re-run whenever the client sends a fresh export.

import json
import re
from pathlib import Path

import openpyxl
import yaml

_HERE = Path(__file__).parent
_XLSX = _HERE / "data" / "store_locator_export2026_03_18_11_22_08_242251 (1).xlsx"
_OUT = _HERE / "data" / "store_registry.json"
_ROUTING = _HERE / "routing_rules.yaml"

# Sheet label → internal vertical token used across the codebase.
_VERT = {"furniture": "furniture", "door": "doors", "doors": "doors",
         "full home customisation": "fhc", "fhc": "fhc"}


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

    _OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"[registry] wrote {len(out)} active stores → {_OUT.name} "
          f"(skipped {skipped_inactive} inactive; FHC owners merged {fhc_merged}; "
          f"no owner id {no_owner})")
    return out


if __name__ == "__main__":
    build()
