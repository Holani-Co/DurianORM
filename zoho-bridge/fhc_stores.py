# Full Home Customisation (FHC) store directory — the 7 studios used by the
# WhatsApp FHC flow (whatsapp_fhc.py). Consolidated from two client sheets:
#   - "City wise Location template" → the customer-facing store card (manager,
#     contact, Google Map) sent on a "Store address" request.
#   - "Matrix - Enquiry (Durian)" → `owner_id` (Zoho CRM ID, col J) + the
#     `location` key (col E, "Select Your Location Type") used to route the
#     enquiry deal to the right studio.
#
# `pincode` is a representative pincode for each studio, resolved to lat/lon via
# data/pincode_geo.json so an arbitrary customer pincode maps to the NEAREST of
# these 7 by distance (closest-pincode matching). Keep this list in lockstep
# with the client's matrix + location sheet; regenerate the CRM owner routing
# (routing_rules.yaml crm_owner_routing) from the same matrix when it changes.

FHC_STORES = [
    {
        "location": "Bengaluru - JP Nagar",
        "owner_id": "3608871000013515676",
        "city": "Bengaluru",
        "pincode": "560078",
        "manager": "Ms. Nisha Choudhary",
        "contact": "7975174258",
        "map": "https://www.google.com/maps?q=place_id:ChIJkxF8x2hrrjsROEp_cIhyOIM",
        "card_name": "Bengaluru – JP Nagar",
    },
    {
        "location": "Delhi - Kirti Nagar",
        "owner_id": "3608871000000333444",
        "city": "Delhi",
        "pincode": "110015",
        "manager": "Ms. Shubhangi Agarwal",
        "contact": "8657960901",
        "map": "https://www.google.com/maps?q=place_id:ChIJxU_EozYDDTkRYkvGbVT5AfA",
        "card_name": "New Delhi – Kirti Nagar",
    },
    {
        "location": "Noida - Sector 10",
        "owner_id": "3608871000001206109",
        "city": "Noida",
        "pincode": "201301",
        "manager": "Ms. Shruti Shrivastav",
        "contact": "9717246242",
        "map": "https://www.google.com/maps?q=place_id:ChIJBWDoQgzlDDkRB95eoB6FUD4",
        "card_name": "Noida – Sector 10",
    },
    {
        "location": "Hyderabad - Kompally",
        "owner_id": "3608871000433075236",
        "city": "Hyderabad",
        "pincode": "500100",
        "manager": "Mr. Sharath Gaddam",
        "contact": "8976988205 / 8657960624",
        "map": "https://www.google.com/maps?q=place_id:ChIJf-C6W8GFyzsRFUD5KJx5aEA",
        "card_name": "Hyderabad – Kompally",
    },
    {
        "location": "Thane - Subhash Nagar",
        "owner_id": "3608871000104057837",
        "city": "Thane",
        "pincode": "400601",
        "manager": "Mr. Addit Haria",
        "contact": "9967694217",
        "map": "https://www.google.com/maps?q=place_id:ChIJaWjMLj655zsRfiAT9byTLf4",
        "card_name": "Mumbai – Thane (Subhash Nagar)",
    },
    {
        "location": "Mumbai - Goregaon",
        "owner_id": "3608871000000804001",
        "city": "Mumbai",
        "pincode": "400062",
        "manager": "Ms. Janhavi Khapre",
        "contact": "8693046731",
        "map": "https://www.google.com/maps?q=place_id:ChIJ_-exKUy35zsR4VTFPhDf3w8",
        "card_name": "Mumbai – Goregaon",
    },
    {
        "location": "Bikaner - Rani Bazar",
        "owner_id": "3608871000560226015",
        "city": "Bikaner",
        "pincode": "334001",
        "manager": "Ms. Shristi Chandak",
        "contact": "9783056731",
        "map": "https://maps.app.goo.gl/4qd6me2Dx8tMFTg4A",
        "card_name": "Bikaner – NH15 Jaisalmer Road",
    },
]

_BY_LOCATION = {s["location"]: s for s in FHC_STORES}


def by_location(location: str) -> dict | None:
    return _BY_LOCATION.get(location)


def store_card(store: dict) -> str:
    """Customer-facing store-address card (WhatsApp plain text)."""
    return (
        f"Here are the details of our {store['card_name']} studio 🏠\n\n"
        f"👤 Store Manager: {store['manager']}\n"
        f"📞 Contact: {store['contact']}\n"
        f"🗺️ {store['map']}\n\n"
        "You can visit the studio or reach the team directly for any assistance. "
        "Thank you for choosing Durian ✨"
    )
