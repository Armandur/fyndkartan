from datetime import datetime, timezone


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hhmm(part):
    p = part.strip().replace(".", ":")
    if not p:
        return None
    h, _, m = p.partition(":") if ":" in p else (p, "", "00")
    if not (h.isdigit() and m.isdigit()):
        return None
    hi, mi = int(h), int(m)
    if not (0 <= hi <= 24 and 0 <= mi < 60):
        return None
    return f"{hi:02d}:{mi:02d}"


def normalize_hours(s):
    """Normalisera en öppettidssträng till HH:MM-HH:MM.

    '9-20' -> '09:00-20:00', '6-23:30' -> '06:00-23:30'. Text som 'Stängt'
    och oparsbara värden lämnas oförändrade.
    """
    if not s or not isinstance(s, str):
        return s
    t = s.strip()
    if "-" not in t:
        return t
    left, _, right = t.partition("-")
    a, b = _hhmm(left), _hhmm(right)
    return f"{a}-{b}" if a and b else t


def make_store(
    chain,
    store_id,
    name,
    *,
    brand=None,
    street=None,
    postal_code=None,
    city=None,
    lat=None,
    lng=None,
    phone=None,
    email=None,
    oh_today=None,
    raw=None,
    open_now=None,
    link_store=None,
    link_offers=None,
    link_online=None,
    tags=None,
    native=None,
    method="api",
):
    # 0,0 = "null island" -> behandla som saknad koordinat (källan har inget värde)
    has_loc = lat is not None and lng is not None and not (lat == 0 and lng == 0)
    return {
        "chain": chain,
        "store_id": str(store_id),
        "name": name,
        "brand": brand,
        "address": {"street": street, "postal_code": postal_code, "city": city},
        "location": {"lat": lat, "lng": lng} if has_loc else None,
        "contact": {"phone": phone, "email": email},
        "opening_hours": {"today": normalize_hours(oh_today), "raw": raw, "open_now": open_now},
        "links": {
            "store_page": link_store,
            "offers": link_offers,
            "online_shopping": link_online,
        },
        "tags": tags or [],
        "native": native,
        "source": {"method": method, "fetched_at": now_iso()},
    }


def tags_from_services(services):
    """Mappa fritext-tjänststrängar (ICA/Coop) till normaliserade taggtyper.

    Endast positiva påståenden modelleras - avsaknad betyder okänt, inte 'nej'.
    """
    out = []
    for s in services or []:
        out.append({"type": classify_service(s), "label": s})
    return out


def classify_service(s):
    t = (s or "").lower()
    if "apotek" in t or "läkemedel" in t:
        return "pharmacy"
    if "atg" in t:
        return "atg"
    if "post" in t or "frimärk" in t:
        return "postal"
    if any(x in t for x in ("dhl", "schenker", "instabox", "bring", "privpak", "budbee", "paket")):
        return "parcel"
    if "spel" in t:
        return "gambling"
    if "bröd" in t or "bageri" in t or "bakat" in t:
        return "bakery"
    if "scan" in t or "självscan" in t:
        return "self_scan"
    if "kontantuttag" in t or "uttag" in t:
        return "cash"
    if "hämta" in t or "e-handel" in t:
        return "click_collect"
    if "ladd" in t:
        return "e_charging"
    return "other"
