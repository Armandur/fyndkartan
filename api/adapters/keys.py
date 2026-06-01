import logging
import re

log = logging.getLogger("matbutiker")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# Coop: statiska subscription-nycklar i butikssidans inbäddade serviceAccess-JSON.
# Butiks-API:t och offers-API:t (dke) använder OLIKA nycklar.
COOP_PAGE = "https://www.coop.se/butiker-erbjudanden/"
_COOP_PAT = re.compile(r'"storeApiSubscriptionKey":"([0-9a-fA-F]{32})"')
_COOP_OFFERS_PAT = re.compile(r'"dkeKey":"([0-9a-fA-F]{32})"')

# Lidl: x-apikey ligger inte i HTML utan i storesearch-frontend-bundlen (base.js),
# vars versionerade sökväg står i butikssidan.
LIDL_PAGE = "https://www.lidl.se/s/sv-SE/butiker/"
_LIDL_CHUNK_PAT = re.compile(r'/s/storesearch-frontend/[^"\']+?/base\.js')
_LIDL_KEY_PAT = re.compile(r"""["']x-apikey["']\s*:\s*["']([A-Za-z0-9]{20,})["']""")


async def scrape_coop_key(client):
    r = await client.get(COOP_PAGE, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    m = _COOP_PAT.search(r.text)
    if not m:
        raise RuntimeError("Coop: hittade inte storeApiSubscriptionKey (sidan kan ha ändrats)")
    log.info("Coop: ny subscription-nyckel skrapad")
    return m.group(1)


async def scrape_coop_offers_key(client):
    r = await client.get(COOP_PAGE, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    m = _COOP_OFFERS_PAT.search(r.text)
    if not m:
        raise RuntimeError("Coop: hittade inte dkeKey (offers-nyckel - sidan kan ha ändrats)")
    log.info("Coop: ny offers-nyckel (dke) skrapad")
    return m.group(1)


async def scrape_lidl_key(client):
    r = await client.get(LIDL_PAGE, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    chunk = _LIDL_CHUNK_PAT.search(r.text)
    if not chunk:
        raise RuntimeError("Lidl: hittade inte storesearch base.js-sökväg")
    base_js = "https://www.lidl.se" + chunk.group(0)
    j = await client.get(base_js, headers={"User-Agent": UA}, timeout=30)
    j.raise_for_status()
    km = _LIDL_KEY_PAT.search(j.text)
    if not km:
        raise RuntimeError("Lidl: hittade inte x-apikey i base.js")
    log.info("Lidl: ny x-apikey skrapad från %s", base_js)
    return km.group(1)
