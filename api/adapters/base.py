from datetime import datetime, timezone

import phonenumbers


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_phone(raw):
    """Svenskt telefonnummer -> nationellt standardformat ("08-26 50 80"). Kedjorna
    skriver olika (mellanslag/bindestreck, varierande gruppering); libphonenumber ger
    rätt riktnummerlängd. Ogiltigt/tomt lämnas oförändrat."""
    s = (raw or "").strip()
    if not s:
        return s or None
    try:
        p = phonenumbers.parse(s, "SE")
        if phonenumbers.is_valid_number(p):
            return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.NATIONAL)
    except phonenumbers.NumberParseException:
        pass
    return s


def _hhmm(part):
    p = part.strip().replace(".", ":")
    if not p:
        return None
    if ":" in p:
        bits = p.split(":")  # tål både HH:MM och HH:MM:SS (Coop) - sekunder ignoreras
        h, m = bits[0], bits[1]
    else:
        h, m = p, "00"
    if not (h.isdigit() and m.isdigit()):
        return None
    hi, mi = int(h), int(m)
    if not (0 <= hi <= 24 and 0 <= mi < 60):
        return None
    return f"{hi:02d}:{mi:02d}"


# Svenska dag-etiketter (fulla + förkortningar) -> veckodagsindex, 0=måndag .. 6=söndag.
_SV_DAYS = {
    "måndag": 0, "mån": 0, "tisdag": 1, "tis": 1, "onsdag": 2, "ons": 2,
    "torsdag": 3, "tors": 3, "tor": 3, "fredag": 4, "fre": 4,
    "lördag": 5, "lör": 5, "söndag": 6, "sön": 6,
}


def expand_sv_label(text):
    """Svensk dag-etikett -> lista veckodagsindex. 'Måndag-fredag' -> [0,1,2,3,4],
    'Lördag' -> [5], 'Mån' -> [0]. Tom lista om oparsbar (avsaknad = okänt)."""
    t = (text or "").strip().lower()
    if not t:
        return []
    if t in ("alla dagar", "dagligen", "varje dag"):
        return list(range(7))
    if "-" in t:
        a, _, b = t.partition("-")
        ai, bi = _SV_DAYS.get(a.strip()), _SV_DAYS.get(b.strip())
        if ai is None or bi is None or bi < ai:
            return []
        return list(range(ai, bi + 1))
    i = _SV_DAYS.get(t)
    return [i] if i is not None else []


def day_entry(day, opens, closes, closed=False):
    """Ett veckodagsobjekt för opening_hours.week (day: 0=mån..6=sön)."""
    closed = bool(closed)
    return {
        "day": day,
        "closed": closed,
        "opens": None if closed else _hhmm(opens or ""),
        "closes": None if closed else _hhmm(closes or ""),
    }


def exception_entry(date, label, opens, closes, closed=False):
    """Ett daterat avvikelse-objekt för opening_hours.exceptions (helgdagar m.m.).
    `date` är 'YYYY-MM-DD' eller None (ICA anger bara helgnamn, inte datum)."""
    closed = bool(closed)
    return {
        "date": date,
        "label": label,
        "closed": closed,
        "opens": None if closed else _hhmm(opens or ""),
        "closes": None if closed else _hhmm(closes or ""),
    }


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
    week=None,
    exceptions=None,
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
        "contact": {"phone": _norm_phone(phone), "email": email},
        "opening_hours": {
            "today": normalize_hours(oh_today),
            "open_now": open_now,
            "week": week or None,
            "exceptions": exceptions or None,
            "raw": raw,
        },
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


def seed_types(label):
    """Regelbaserad seed: lista av kanoniska typer (kan vara flera, t.ex. en
    'Posten Brev & paket' är både postal och parcel). Override sker via tag_map."""
    t = (label or "").lower()
    out = []
    if "apotek" in t or "läkemedel" in t:
        out.append("pharmacy")
    if "atg" in t:
        out.append("atg")
    if "post" in t or "frimärk" in t:
        out.append("postal")
    if any(x in t for x in ("dhl", "schenker", "privpak", "instabox", "bring", "budbee", "paket")):
        out.append("parcel")
    if "spel" in t:
        out.append("gambling")
    if "bröd" in t or "bageri" in t or "bakat" in t:
        out.append("bakery")
    if "scan" in t or "självscan" in t:
        out.append("self_scan")
    if "kontant" in t or "uttag" in t:
        out.append("cash")
    if "hämta" in t or "e-handel" in t:
        out.append("click_collect")
    if "ladd" in t:
        out.append("e_charging")
    if "togo" in t or "to go" in t:  # ICA To Go m.fl. - grab-and-go/obemannad
        out.append("togo")
    # dedupe, behåll ordning
    seen, res = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res or ["other"]


def classify_provider(label):
    """Speditör/aktör för paket-/post-taggar (annars None). Behålls vid sidan av
    den kanoniska typen så man vet *vilken* speditör ett paketombud gäller."""
    t = (label or "").lower()
    if "dhl" in t:
        return "DHL"
    if "schenker" in t or "privpak" in t:
        return "Schenker"
    if "dsv" in t:
        return "DSV"
    if "instabox" in t:
        return "Instabox"
    if "budbee" in t:
        return "Budbee"
    if "bring" in t:
        return "Bring"
    if "postnord" in t or "posten" in t or "postombud" in t:
        return "PostNord"
    return None
