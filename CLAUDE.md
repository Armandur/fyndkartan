# CLAUDE.md - matbutiker

Kodbasöversikt för utveckling. Projektet är ett unified API för fem svenska
matbutikskedjor: hittar butiker (steg 1, klart) och deras erbjudanden (steg 2,
pågår), med en Leaflet/OSM-karta. Aktuell status och plan: `ROADMAP.md`.

## Stack

- **Backend:** Python 3.12 + FastAPI (uvicorn), `httpx` (async) för utgående anrop.
- **Databas:** SQLite via raw `sqlite3`. Schema + migrering i `init_db()`
  (`ALTER TABLE`-guards, ingen Alembic).
- **Frontend:** vanilla JS, **Bootstrap 5** + **Leaflet** (OSM) med markercluster
  - allt via CDN, ingen bundler. Ren statisk app i `web/`, serveras av API:t.
- **Körning (dev):** `uv run uvicorn api.main:app --host 0.0.0.0 --port 8700` (kör
  från repo-roten). Servern nås på `ubuntu-ai:8700`. Ingen `.env` krävs (nycklar auto-hämtas).
- **Deploy:** monolitisk single-container är normalfallet (lokal Unraid) -
  `docker-compose.yml`. Externt hostad med Caddy/TLS = undantag,
  `docker-compose.hetzner.yml`. CI bygger till `ghcr.io/armandur/fyndkartan`. Se `DOCKER.md`.

## Repo-struktur: api/ + web/

`api/` (FastAPI-paketet) och `web/` (frontend) är medvetet separerade men i samma
repo - "appen bygger på API:t". API:t serverar `web/` statiskt (single deploy);
frontend anropar `/v1/...` same-origin. Splitten till två repon är billig senare
(REST-ytan är redan ren) om en andra konsument dyker upp.

```
api/                 # Python-paketet (importeras som `api`)
  main.py            # FastAPI-app, lifespan, alla routes, serverar web/ statiskt
  config.py          # env (valfria nycklar), CHAIN_META, Lidl-svepets bounds. BASE_DIR = repo-roten
  database.py        # SQLite: stores/offers/ean_cache, init_db() (ALTER-guards), row<->dict
  geo.py             # haversine(), grid() (geo_box-rutnät för Lidl)
  matching.py        # cross-chain EAN-matchning: normalize_ean(), build_comparisons()
  apilog.py          # instrumentering av utgående anrop (make_client + ring-buffer/statistik)
  tags.py            # tagg-normalisering: effective_type() (tag_map-override + classify_service)
  sync.py            # run_sync(): kör butiks-adaptrar parallellt -> SQLite. STATE per kedja
  adapters/
    base.py          # make_store(), tags_from_services(), classify_service(), normalize_hours()
    ica.py coop.py willys.py hemkop.py lidl.py   # butiks-adaptrar, fetch_all() -> UnifiedStore[]
    axfood_common.py # fetch_features() - Willys/Hemköp storeFeatures (CMS) -> tags
    ica_token.py keys.py     # token/nyckel-hämtning (auto-förnyelse, scrape-on-401)
    ica_offers.py axfood_offers.py coop_offers.py   # erbjudande-adaptrar
web/                 # frontend (statisk, ingen build)
  index.html app.js style.css   # karta + sidopanel + erbjudande-/jämförelse-paneler
  admin.html         # admin-dashboard (/admin): översikt, API-anrop, datakällor, taggar
pyproject.toml .env stores.db   # i repo-roten (BASE_DIR)
```

## Datamodell

**`stores`** (en rad per butik, PK `(chain, store_id)`): gemensamma kolumner
(chain, store_id, name, brand, street, postal_code, city, lat, lng, phone,
email, oh_today, open_now, link_store, link_offers, link_online) + JSON-kolumner
`tags`, `raw` (öppettider), `native` (kedjans sekundär-id:n). Se `row_to_store()`.

**`offers`** (PK `(chain, store_id, offer_id)`): name, brand, package, price,
price_text, comparison_price/value/unit, category_raw/id, mechanic_type,
valid_to, `eans` (JSON), image, fetched_at.

UnifiedStore-fältschemat och brand/tags-vokabulären beskrivs i `UNIFIED-API.md`.

## Designbeslut

- **Sync -> cache -> servera.** Appen pratar aldrig live med kedjorna. Butiks-
  synken (`run_sync`) körs vid uppstart om cachen är tom, annars via `POST /v1/sync`.
- **Två insamlingsmönster:** fyra kedjor hämtar hela beståndet (filtreras lokalt
  med Haversine); Lidl ger bara butiker inom en `geo_box` -> svep ett rutnät.
- **Självförnyande nycklar.** ICA-token hämtas från token-API:t (kortlivat ~1h,
  cache till utgång); Coop/Lidl använder ev. env-nyckel och skrapar ny vid 401.
  Willys/Hemköp kräver ingen nyckel.
- **Erbjudanden är lazy + separat cache.** `GET /v1/stores/{chain}/{id}/offers`
  hämtar live första gången (eller efter 6h TTL) och cachar i `offers`. Inte del
  av butikssynken. Byggt för ICA, Willys, Hemköp, Coop (ej Lidl). Coop/Axfood
  bär `member_price` (medlems-/Klubbpris).
- **Coop-berikning:** `coop.py` gör ett detalj-anrop per butik (bunden
  parallellism) för `services` -> tags och `concept` -> brand. Tyngre synk men
  ger samma metadata som ICA.
- **Matchning (`matching.py` + `/v1/compare/near`):** grupperar närliggande
  butikers erbjudanden per EAN (>= 2 olika kedjor). Strikt `normalize_ean`
  (rejekta 2-prefix/ogiltig längd). Jämför på **enhetspris** (jämförpris) när alla
  har det, annars råpris med `compare_by`-flagga (råpris ≠ jämförbart pga
  förpackningsstorlek/multibuy/medlemspris). I drift för alla fyra kedjor. ICA+Coop
  har EAN inline; Willys/Hemköp resolvas bundet via `ean_cache` (code->EAN,
  persistent) + `axfood_offers.fetch_eans` (cap `EAN_RESOLVE_CAP`/anrop, warmar över tid).
  Grupper med identisk deal slås ihop (`_merge_same_deal`, `variant_count`).
- **Normalisering:** öppettider -> `HH:MM` (`normalize_hours`), taggar som
  positiva påståenden (avsaknad = okänt), `0,0`-koordinater = saknad position.

## Vanliga ändringar

- **Ny butiks-adapter:** lägg `app/adapters/<chain>.py` med `fetch_all()`,
  registrera i `sync.py` + `config.CHAINS`/`CHAIN_META`.
- **Ny erbjudande-adapter:** spegla `ica_offers.py`, koppla in i offers-routen i
  `main.py` (villkoret `if chain != "ica"`). Aktivera knappen i `web/app.js`
  (`s.chain === "ica"`).
- **Verifiera efter ändring:** `uv run python -c "from api.main import app; print('OK')"`,
  starta servern och kontrollera `dev.log`.

## Kända datakälle-fakta (dyrköpt research)

- **ICA-token:** hämta från `www.ica.se/e11/public-access-token` (JSON, alltid
  färskt). Det inbäddade tokenet i `/butiker/`-HTML är CDN-cachat och kan vara utgånget.
- **ICA-erbjudanden:** server-renderade i `/erbjudanden/{slug}-{accountNumber}/`
  under `window.__INITIAL_DATA__` -> `weeklyOffers`. Bär EAN (`eans`-array).
- **Willys/Hemköp-erbjudanden:** e-handel `/search/campaigns?storeId=&size=1000`
  (sidparam heter `page`, inte `currentPage`) + `/axfood/rest/p/{code}` för EAN.
  Samma endpoints för båda. OBS: e-handelns kampanjpris ≠ garanterat butikspris.
- **Coop-erbjudanden:** `external.api.coop.se/dke/offers/{ledger}?api-version=v2`
  med offers-nyckeln (`dkeKey`, ≠ butiks-nyckeln). EAN i `externalId` inline,
  medlemspris i `priceInformation.isMemberPrice`. `channelCodes:DR` = reklambladet.
- **Coop step-1-metadata:** butiksdetalj `/external/store/stores/{ledger}`
  (butiks-nyckeln) ger `services` (-> tags) + `concept` (-> brand).
- **Willys/Hemköp step-1-tjänster:** CMS-komponenten `.../axfoodcommercewebservices/v2/
  {catalog}/cms/components?componentIds={Component}&storeId={id}` -> `storeFeatures`
  ({id: label}) -> tags. Component: `WillysDefaultRightColumnStoreInfoComponent` /
  `HemkopDefaultRightColumnStoreInfoComponent`. Hämtas per butik i `axfood_common.fetch_features`.
- **Lidl:** `x-apikey`, geo_box-svep; erbjudanden är regionala (`offerRegion`).
