import asyncio
import logging
import re

from .base import day_entry, exception_entry, expand_sv_label

log = logging.getLogger("matbutiker")

_RANGE = re.compile(r"(\d{1,2}[:.]\d{2})\s*-\s*(\d{1,2}[:.]\d{2})")
_SPECIAL = re.compile(r"(\d{4}-\d{2}-\d{2})\s*\(([^)]*)\)\s*(.*)")


def parse_week(opening_hours):
    """Axfood `openingHours` (['Mån 08:00-21:00', ...]) -> normaliserad vecka.
    Rad utan tidsintervall (t.ex. 'Sön Stängt') tolkas som stängd dag."""
    out = []
    for line in opening_hours or []:
        parts = (line or "").split(None, 1)
        if not parts:
            continue
        days = expand_sv_label(parts[0])
        if not days:
            continue
        m = _RANGE.search(parts[1]) if len(parts) > 1 else None
        for d in days:
            if m:
                out.append(day_entry(d, m.group(1), m.group(2), False))
            else:
                out.append(day_entry(d, None, None, True))
    return sorted(out, key=lambda e: e["day"]) or None


def parse_exceptions(special):
    """Axfood `specialOpeningHours` (['2026-06-06 (Nationaldagen) 08:00-21:00', ...])
    -> daterade avvikelser. Rad utan intervall tolkas som stängd."""
    out = []
    for line in special or []:
        m = _SPECIAL.match((line or "").strip())
        if not m:
            continue
        date, label, rest = m.group(1), m.group(2).strip(), m.group(3)
        r = _RANGE.search(rest)
        if r:
            out.append(exception_entry(date, label, r.group(1), r.group(2), False))
        else:
            out.append(exception_entry(date, label, None, None, True))
    return out or None

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

_CONCURRENCY = 8


async def fetch_features(client, domain, catalog, component, store_ids):
    """Hämta butikstjänster (storeFeatures) per butik via SAP Commerce CMS-komponenten.

    domain t.ex. 'willys.se', catalog 'willys', component
    'WillysDefaultRightColumnStoreInfoComponent'. Returnerar {store_id: [labels]}."""
    base = f"https://www.{domain}/axfoodcommercewebservices/v2/{catalog}/cms/components"
    headers = {"Accept": "application/json", "User-Agent": UA}
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def one(sid):
        async with sem:
            try:
                r = await client.get(
                    base,
                    params={"componentIds": component, "storeId": sid, "pageSize": 1},
                    headers=headers,
                    timeout=20,
                )
                if r.status_code == 200:
                    comp = (r.json().get("component") or [{}])[0]
                    return sid, list((comp.get("storeFeatures") or {}).values())
            except Exception as e:  # noqa: BLE001 - logga, hoppa butiken
                log.warning("Axfood features %s misslyckades: %s", sid, e)
        return sid, []

    return dict(await asyncio.gather(*(one(sid) for sid in store_ids)))
