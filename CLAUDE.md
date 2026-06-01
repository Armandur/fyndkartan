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
- **Körning (dev):** I DETTA projekt äger Claude start/stopp/reset av dev-servern
  (avsteg från den globala regeln om att aldrig starta servern). Kör den i bakgrunden
  från repo-roten och logga till `dev.log`:
  `​.venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8700 > dev.log 2>&1`
  (Bash `run_in_background: true`). Reset = döda processen (`kill <pid>`, hitta via
  `ps aux | grep api.main`) och starta om på samma sätt. Servern nås på `ubuntu-ai:8700`.
  Ingen `.env` krävs (nycklar auto-hämtas). Kör utan `--reload`, så starta om efter
  kodändringar för att de ska slå igenom (admin-kontot skapas t.ex. först vid uppstart).
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
  matching.py        # cross-chain EAN-matchning: normalize_ean(), build_comparisons() (manual_groups)
  brands.py          # märkesvaru-paring: private-label-detektion + förslag + APIRouter (/v1/admin/...)
  details.py         # EAN-produktinfo (fetch_for_ean): Axfood /p/{code} + Coop personalization-API; ICA via Coop-fallback
  images.py          # unified produktbild per EAN: resolve+resize (Cloudinary-transform)+lokal cache (image_cache/)
  apilog.py          # anropslogg: utgående (make_client-hook) + inkommande (record_incoming, källa "egen"), ring-buffer/statistik
  tags.py            # tagg-normalisering: effective_types() (tag_map-override + seed_types)
  categories.py      # kategori-normalisering: råkategori -> kanonisk (category_map, derive-at-read)
  auth.py            # bcrypt + current_user/public_user (app) + current_admin/public_admin (konsol)
  sync.py            # run_sync(): kör butiks-adaptrar parallellt -> SQLite. STATE per kedja
  adapters/
    base.py          # make_store(), tags_from_services(), classify_service(), normalize_hours()
    ica.py coop.py willys.py hemkop.py lidl.py   # butiks-adaptrar, fetch_all() -> UnifiedStore[]
    axfood_common.py # fetch_features() - Willys/Hemköp storeFeatures (CMS) -> tags
    ica_token.py keys.py     # token/nyckel-hämtning (auto-förnyelse, scrape-on-401)
    ica_offers.py axfood_offers.py coop_offers.py   # erbjudande-adaptrar
web/                 # frontend (statisk, ingen build)
  index.html app.js style.css   # karta + sidopanel + erbjudande-/jämförelse-paneler
  admin.html         # API-konsol (/admin, egen admin-login): översikt+synk, API-anrop, datakällor+test, taggar, märkesvaror, API-nycklar
pyproject.toml .env stores.db   # i repo-roten (BASE_DIR)
```

## Datamodell

**`stores`** (en rad per butik, PK `(chain, store_id)`): gemensamma kolumner
(chain, store_id, name, brand, street, postal_code, city, lat, lng, phone,
email, oh_today, open_now, link_store, link_offers, link_online) + JSON-kolumner
`tags`, `raw` (öppettidernas råformat), `hours` (normaliserad vecka:
`{week, exceptions}`), `native` (kedjans sekundär-id:n). Se `row_to_store()`.

**`offers`** (PK `(chain, store_id, offer_id)`): name, brand, package, price,
price_text, comparison_price/value/unit, category_raw/id, mechanic_type,
valid_to, `eans` (JSON), image, fetched_at. `get_store_offers` berikar varje rad
med härledda fält: `category` (kanonisk), `deal_type`+`multibuy_qty` (ur `price_text`,
inte opålitliga `mechanic_type`).

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
  hämtar live första gången och cachar i `offers`. Inte del av butikssynken (synken
  rör aldrig erbjudanden). Färskhet (`_offers_fresh`): cache till 6h TTL, MEN hämtas
  om tidigare om en cachad offer gått ut (`valid_to` < idag) - med ett 30-min golv
  (`OFFERS_MIN_REFRESH`) så källans kvarliggande utgångna offers inte ger refetch-loop.
  Compare/favoriter laddar via samma `_ensure_offers` (TTL-respekterande, ej tvingat).
  Byggt för ICA, Willys, Hemköp, Coop (ej Lidl). Coop/Axfood bär `member_price`.
- **Coop-berikning:** `coop.py` gör ett detalj-anrop per butik (bunden
  parallellism) för `services` -> tags och `concept` -> brand. Tyngre synk men
  ger samma metadata som ICA.
- **Kategori-normalisering (`categories.py` + `category_map`):** kedjornas råkategorier
  mappas till en kanonisk lista (`CANONICAL_CATEGORIES`), derive-at-read (admin-flik
  redigerar mappningen utan omsynk). Offer-nivån är grov; **produktdetaljens kategori
  föredras** i `get_store_offers` när den finns (`product_info.category_raw/source` ->
  `category_from_detail`). Förvärmas globalt per EAN: Axfood via `ean_cache.category`
  (`warm_axfood_eans`, samma `/p/{code}` som EAN-warmingen), Coop via `product_info`
  (`warm_coop_categories`, batchat personalization-API). Coops offer-nivå
  (Färsk/Kolonial/Nonfood) är opålitlig -> coop_nav (navCategories-topp) overridar.
  Viktvaror (slump-EAN) saknar produktdetalj -> faller till `ovrigt`.
- **Matchning (`matching.py` + `/v1/compare/near`):** grupperar närliggande
  butikers erbjudanden per EAN (>= 2 olika kedjor). Strikt `normalize_ean`
  (rejekta 2-prefix/ogiltig längd). Jämför på **enhetspris** (jämförpris) när alla
  har det, annars råpris med `compare_by`-flagga (råpris ≠ jämförbart pga
  förpackningsstorlek/multibuy/medlemspris). I drift för alla fyra kedjor. ICA+Coop
  har EAN inline; Willys/Hemköp resolvas bundet via `ean_cache` (code->EAN,
  persistent) + `axfood_offers.fetch_eans` (cap `EAN_RESOLVE_CAP`/anrop, warmar över tid).
  Grupper med identisk deal slås ihop (`_merge_same_deal`, `variant_count`).
- **Märkesvaru-paring (`brands.py` + "Märkesvaror"-fliken):** egna märkesvaror (ICA,
  Garant, Änglamark...) har kedjeinterna EAN och matchar aldrig automatiskt. Admin
  parar ihop dem manuellt: redigerbar private-label-vokabulär per kedja (brand-rötter,
  `private_brands`), lista över private-label-produkter ur offers, namn+förpacknings-
  baserade förslag, produktbild + lazy rik detalj. **EAN-centrerat:** en produkt = en EAN;
  samma EAN i flera kedjor (Willys+Hemköp delar Axfood-EAN) kollapsas till en post och
  matchar redan automatiskt, så paring sker bara över olika private labels. Mappningen
  (`product_matches`) skickas EAN-nycklad som `manual_groups` till `build_comparisons`.
  Endast offers-data (v1): inte fulla sortiment.
- **EAN-produktinfo som egen domän (`details.py` + `GET /v1/products/{ean}`):** rik
  produktinfo (ingredienser/näring/ursprung/allergener) nyckad på EAN, **publik** (utanför
  admin-routern) så både konsument-appen (erbjudande-info-modal) och konsolen delar den.
  EAN-nyckad cache `product_info`. **Normaliserad + sammanslagen över källor** (`_merge`):
  Axfood `/p/{code}` (EAN->code via `ean_cache`) + Coops personalization-API (POST EAN-array;
  näring i `nutrientLinks`; nyckel skrapas via `keys.scrape_coop_perso_key`, scrape-on-401).
  Coop hämtas även när Axfood har gles näring; merge tar längsta textfält + rikaste näring,
  `sources` listar bidragande källor. Allergener (`extract_allergens`) ur VERSALA ord i
  ingredienserna. Coop är EAN-global -> täcker branded varor i alla kedjor inkl. ICA (vars
  ehandel är AWS-WAF-bot-skyddad). ICA:s egna märken (ICA-intern EAN) saknas dock.
- **Produktbild per EAN (`images.py` + `GET /v1/products/{ean}/image`):** hittar bild-URL
  ur cachade offers (annars ICA:s EAN-CDN), **resizar via Cloudinary-transform** (källorna
  är cloudinary; `c_limit,w_400` -> små filer i stället för full-res), cachar bytes lokalt
  i `image_cache/` (metadata i `product_images`). Gör oss CDN-oberoende. Frontend-kort
  använder den med `onerror`-fallback till original-CDN-URL.
- **Normalisering:** öppettider -> `HH:MM` (`normalize_hours`), taggar som
  positiva påståenden (avsaknad = okänt), `0,0`-koordinater = saknad position.
- **Veckoöppettider (`opening_hours.week`/`exceptions`):** varje adapter parsar sitt
  råformat till en gemensam veckoform (`{day 0-6, closed, opens, closes}`) + daterade
  avvikelser. Delade hjälpare i `base.py` (`expand_sv_label` för ICA+Coops etikettgrupper,
  `day_entry`/`exception_entry` som kör tider genom `_hhmm`, som tål `HH:MM:SS`). Axfood
  per veckodag-sträng (`axfood_common.parse_week`), Lidl härleder veckodag ur datum.
  Coops vecka ligger i butiksdetaljen vi redan hämtar. Avsaknad av en veckodag = okänt.
- **Två skilda auth-domäner.** App-konton (`users`, slutanvändare) och konsol-
  admins (`admin_users`, drift) är helt separata: olika tabeller och olika
  session-nycklar (`uid` resp. `admin_uid` i samma signerade cookie). En app-
  användare har aldrig admin-behörighet; ett konsolkonto kan inte logga in i appen.
  Session-secret löses vid import (env `SESSION_SECRET` annars DB-persisterad i
  `settings`) -> sessioner överlever omstart. Logout poppar bara sin egen nyckel.
- **App-auth:** e-post/lösenord (bcrypt). `/v1/auth/*` (register/login/logout/me/
  password), `/v1/favorites` kräver inloggning. Favoriter är endast-inloggad även i
  frontend (CSS `body:not(.logged-in)` döljer stjärnor/filter, ingen localStorage).
  `current_user` accepterar både session-cookie OCH opak `Authorization: Bearer`-token
  (`POST /v1/auth/token`, lagras hashad i `user_tokens`) för icke-webb-klienter.
- **Hela API:t är gatat (ingen anonym åtkomst).** `require_consumer`-dependency på
  alla `/v1`-dataendpoints (`products|stores|compare|chains`) kräver inloggad app-
  användare (session/bearer) ELLER giltig `X-API-Key`. Undantag som måste vara öppna:
  `/v1/auth/*` (login/register/token), `/v1/console/auth/login`, `/healthz`, sidorna
  `/` + `/admin` + `/static`. **Kart-appen är en inloggnings-vägg:** `web/app.js` visar
  authModal som icke-stängbar vägg tills man loggat in, och laddar data först därefter.
- **Externa API-nycklar:** konsolen utfärdar/återkallar (`api_keys`, hashade, visas en
  gång). `X-API-Key`-middleware validerar och `require_consumer` accepterar nyckeln som
  åtkomst. CORS via `CORS_ORIGINS` (env-allowlist, default av).
- **API-konsol (`web/admin.html` på `/admin`):** drift/dataadministration, skild
  från kartappen. Egen inloggningsruta på sidan (`/v1/console/auth/*`). `require_admin`
  (-> `auth.current_admin`, 403 annars) gatar alla `/v1/admin/*`, `/v1/tags*` och
  `/v1/sync*`. Synk-knapp + status bor i konsolens Översikt-flik (inte i appen).
  Konsolkontot seedas vid uppstart (`ensure_admin` -> `admin_users`) från `ADMIN_EMAIL`
  (generisk default `admin@example.com` i koden, sätts per instans via env/`.env`) +
  `ADMIN_PASSWORD` (annars genererat + loggat en gång).

## Vanliga ändringar

- **Ny butiks-adapter:** lägg `app/adapters/<chain>.py` med `fetch_all()`,
  registrera i `sync.py` + `config.CHAINS`/`CHAIN_META`.
- **Ny erbjudande-adapter:** spegla `ica_offers.py`, koppla in i offers-routen i
  `main.py` (villkoret `if chain != "ica"`). Aktivera knappen i `web/app.js`
  (`s.chain === "ica"`).
- **Verifiera efter ändring:** `.venv/bin/python -c "from api.main import app; print('OK')"`,
  starta sedan om dev-servern (se Körning ovan) och kontrollera `dev.log` för fel.

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
