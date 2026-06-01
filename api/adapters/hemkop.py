from .axfood_common import fetch_features
from .base import classify_service, make_store

# Hemköp använder samma Axfood-endpoint som Willys (SAP Commerce). Ingen auth,
# ett anrop ger hela beståndet - ingen headless-rendering behövs.
URL = "https://www.hemkop.se/axfood/rest/store"
COMPONENT = "HemkopDefaultRightColumnStoreInfoComponent"


async def fetch_all(client):
    headers = {"Accept": "application/json", "User-Agent": "matbutiker-sync/1.0"}
    r = await client.get(URL, params={"online": "false"}, headers=headers, timeout=30)
    r.raise_for_status()
    rows = [s for s in r.json() if (s.get("name") or "").strip()]
    features = await fetch_features(
        client, "hemkop.se", "hemkop", COMPONENT, [s.get("storeId") for s in rows]
    )
    return [_map(s, s["name"].strip(), features.get(s.get("storeId"), [])) for s in rows]


def _map(s, name, features):
    a = s.get("address") or {}
    gp = s.get("geoPoint") or {}
    store_id = s.get("storeId")
    tags = [{"type": classify_service(f), "label": f} for f in features]
    if s.get("clickAndCollect"):
        tags.append({"type": "click_collect", "label": "Click & Collect"})
    if s.get("franchiseStore"):
        tags.append({"type": "franchise", "label": "Franchisebutik"})
    return make_store(
        "hemkop",
        store_id,
        name,
        brand="hemkop",
        street=a.get("line1"),
        postal_code=a.get("postalCode"),
        city=a.get("town"),
        lat=gp.get("latitude") or a.get("latitude"),
        lng=gp.get("longitude") or a.get("longitude"),
        phone=a.get("phone") or s.get("customerServicePhone"),
        email=a.get("email") or s.get("customerServiceEmail"),
        oh_today=s.get("openingStoreMessageValue"),
        raw={
            "openingHours": s.get("openingHours"),
            "specialOpeningHours": s.get("specialOpeningHours"),
        },
        open_now=s.get("open"),
        link_store=f"https://www.hemkop.se/butik/{store_id}",
        link_offers=f"https://www.hemkop.se/erbjudanden/{store_id}",
        tags=tags,
        native={"flyerPdf": s.get("flyerURL")},
    )
