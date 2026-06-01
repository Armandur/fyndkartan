import asyncio
import logging

log = logging.getLogger("matbutiker")

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
