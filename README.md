# Matbutiker - Unified Store & Offers API

Ett gemensamt API för fem svenska matbutikskedjor (ICA, Coop, Willys, Hemköp,
Lidl): hittar butiker, deras erbjudandesidor och - för ICA - de faktiska
veckoerbjudandena, plus en webbkarta på OpenStreetMap.

- **Steg 1 (butiker):** klart - 5 kedjor, ~2682 butiker. Spec: [`UNIFIED-API.md`](UNIFIED-API.md).
- **Steg 2 (erbjudanden):** pågår - ICA byggt, Axfood-källa klar. Status + plan: [`ROADMAP.md`](ROADMAP.md).
- **Kodbasöversikt för utveckling:** [`CLAUDE.md`](CLAUDE.md).

## Stack

FastAPI + SQLite-cache + httpx. Frontend: Bootstrap 5 + Leaflet (OSM) med
marker-clustering. Inga byggsteg, allt laddas via CDN.

## Köra

```bash
uv run uvicorn api.main:app --host 0.0.0.0 --port 8700
```

Inga nycklar behöver konfigureras - ICA-token, Coop- och Lidl-nycklarna
skrapas automatiskt från kedjornas egna sidor (se "Nycklar" nedan). `.env` är
valfri och behövs bara om du vill tvinga en egen nyckel.

Öppna `http://<host>:8700/`. Vid första start är cachen tom och en synk
startar automatiskt i bakgrunden; kartan fylls på medan kedjorna blir klara
(badge uppe till höger visar status). Synka om manuellt med knappen eller
`POST /v1/sync`.

## API

| Endpoint | Beskrivning |
|----------|-------------|
| `GET /v1/stores` | Alla butiker. Filter: `chain`, `city`, `q`, `brand`, `features`, `has_offers` |
| `GET /v1/stores/near?lat=&lng=&radius_km=` | Geosökning (Haversine), sorterad på avstånd |
| `GET /v1/stores/{chain}/{store_id}` | Enskild butik |
| `GET /v1/stores/{chain}/{store_id}/offers` | Butikens erbjudanden (lazy, 6h cache; ICA/Willys/Hemköp/Coop) |
| `GET /v1/compare/near?lat=&lng=&radius_km=&min_chains=2` | Produkter (per EAN) på erbjudande hos >= N närliggande kedjor, jämfört på enhetspris (ICA, Coop, Willys, Hemköp) |
| `GET /v1/compare/stores?stores=chain:id,...` | Samma jämförelse men bland angivna butiker (favoriter) |
| `GET /v1/chains` | Integrationsstatus + antal per kedja |
| `GET /admin` | Admin-dashboard: översikt, API-anrop (logg/statistik), datakällor, tagg-normalisering |
| `POST /v1/sync` | Starta omsynk (butiker) |
| `GET /v1/sync/status` | Synkstatus per kedja |

## Datakällor (verifierade 2026-05-31)

| Kedja | Metod | Auth | Erbjudande-länk |
|-------|-------|------|-----------------|
| ICA | `storeslist`-API | Bearer | ✅ direkt (`highlightUrls.offers.url`) |
| Coop | `proxy.api.coop.se` lista | subscription key | ✅ härledd (`dr.coop.se/butik/{ledger}`) |
| Willys | `/axfood/rest/store` | ingen | ✅ `flyerURL` |
| Hemköp | `/axfood/rest/store` | ingen | ✅ `/erbjudanden/{id}` |
| Lidl | Schwarz geo_box-svep | x-apikey | ❌ regionalt (steg 2) |

Totalt ~2682 butiker i cachen (ICA 1288, Coop 722, Lidl 212, Hemköp 206, Willys 254).

## Kända luckor (v1)

- **Lidl** har ingen erbjudande-länk per butik (erbjudanden är regionala,
  `offerRegion`/`zone` sparas i `native`) och erbjudande-adaptern är inte byggd än.
- **EAN för Willys/Hemköp** hämtas inte vid visning (ligger i produktdetaljen);
  resolvas separat när matchningslagret byggs. ICA + Coop har EAN inline.

## Nycklar (självförnyande)

Ingen nyckel konfigureras manuellt - alla skrapas från kedjornas publika sidor:

| Kedja | Nyckel | Källa | Förnyelse |
|-------|--------|-------|-----------|
| ICA | `publicAccessToken` | `ica.se/e11/public-access-token` (JSON-API) | kortlivad ~1h, hämtas proaktivt + vid 401 |
| Coop (butiker) | `storeApiSubscriptionKey` | `coop.se/butiker-erbjudanden/` | statisk, skrapas vid 401 |
| Coop (erbjudanden) | `dkeKey` | `coop.se/butiker-erbjudanden/` | statisk, skrapas vid 401 |
| Lidl | `x-apikey` | `storesearch-frontend/base.js` | statisk, skrapas vid 401 |

Kod: `app/adapters/ica_token.py` och `app/adapters/keys.py`. (Obs: ICA-tokenet
ska hämtas från token-API:t, inte skrapas ur `/butiker/`-HTML:en - den är
CDN-cachad och kan ge ett redan utgånget token.)

## Erbjudanden (steg 2)

`GET /v1/stores/{chain}/{store_id}/offers` hämtar en butiks erbjudanden lazy
(första anropet hämtar live + skriver till `offers`-tabellen; därefter ur cache
i 6h, `?refresh=true` tvingar omhämtning). I v1 stöds **ICA**; övriga kedjor
returnerar tom lista + en `note`.

| Kedja | Erbjudande-källa | EAN | Status |
|-------|------------------|-----|--------|
| ICA | server-renderad `/erbjudanden/{butik}` (`weeklyOffers`) | ✅ inline | byggd |
| Willys | e-handel `/search/campaigns?storeId=` + `/axfood/rest/p/{code}` | ✅ via detalj | byggd |
| Hemköp | identisk Axfood-endpoint | ✅ via detalj | byggd |
| Coop | `external.api.coop.se/dke/offers/{ledger}` (offers-nyckel) | ✅ inline | byggd |
| Lidl | regionalt (`offerRegion`) | ? | kvar |

I v1 stöds **ICA, Willys, Hemköp, Coop**. Coop-erbjudandena bär `member_price`
(medlemspris) precis som Axfoods Klubbpris.

Se [`ROADMAP.md`](ROADMAP.md) för full status, datakällor per kedja och
matchningsplanen (EAN-nyckel + kategori/enhetspris-fallback).

## Arkitektur

Sync-jobbet kör alla butiks-adaptrar (`app/adapters/`), normaliserar till
`UnifiedStore` och cachar i SQLite. API:et läser alltid ur cachen, aldrig live
mot kedjorna. Lidl sveps via ett geo_box-rutnät; övriga fyra hämtar hela
beståndet i ett eller få anrop. Erbjudanden ligger i en separat `offers`-tabell
med egen lazy-cache (ej del av butikssynken). Butikssynken körs dessutom på
schema inifrån appen enligt `SYNC_CRON` (cron-uttryck, default dagligen 04:00).
