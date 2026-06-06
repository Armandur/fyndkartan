# CLAUDE.md - matbutiker

Kodbasöversikt för utveckling. Projektet är ett unified API för sex svenska
matbutikskedjor (ICA, Coop, Willys, Hemköp, Lidl, City Gross): hittar butiker
(steg 1, klart) och deras erbjudanden (steg 2, pågår), med en Leaflet/OSM-karta.
Aktuell status och plan: `ROADMAP.md`. **Återupptar du arbetet? Läs `HANDOFF.md` först** - var vi är NU
(Steg 6 per-butik-pris), senaste arbetet, öppna beslut (SQLite->Postgres) och nästa steg.

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
- **Ingen Gemini-delegering i detta projekt** (avsteg från den globala regeln). Claude gör
  alla ändringar direkt, även stora filer och koordinerade flerfils-features - delegera inte
  läsning/refaktorering/flerfilsändringar till Gemini CLI här.
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
  main.py            # FastAPI-app, lifespan, middleware, kvarvarande routes (auth/favoriter/konsol-drift/sync/sweep/crawl), custom openapi() (taggar /docs per prefix), serverar web/ statiskt
  deps.py            # delade FastAPI-dependencies: require_consumer (gate konsument-data) + require_admin (re-export av auth.require_admin). Importeras härifrån av main + route-moduler, aldrig kopierade
  routes/            # utbrutna route-grupper (krymper main.py, REVIEW Fynd 2). Routrar utan prefix, fulla paths -> identiska URL:er; main.py include_router:ar. admin_vocab.py = vokabulär-admin (categories/manufacturers/tags/providers, require_admin på router-nivå); stores.py = /v1/stores* (+ _query_stores); compare.py = /v1/compare* + /v1/favorites/offers (+ _compare_rows/_resolve_axfood_eans); products.py = /v1/products* + /v1/admin/products/{ean}/* + /v1/categories + /v1/chains (+ delade resolvers; OBS källordning: literaler före giriga /v1/products/{ean})
  config.py          # env (valfria nycklar), CHAIN_META, Lidl-bounds, ORIGIN_COUNTRIES (babel), OWN_APIS (konsol-katalog, returns deriveras ur schemas)
  schemas.py         # Pydantic-responsmodeller - sanningskälla för API-kontraktet (document-only) + konsolens fält-doc (fields_doc)
  database.py        # SQLite: stores/offers/ean_cache, init_db() (ALTER-guards), row<->dict, list_products() (produktsök/kategori)
  geo.py             # haversine(), grid() (geo_box-rutnät för Lidl)
  matching.py        # cross-chain EAN-matchning: normalize_ean(), _norm_unit() (kanonisk jämförenhet), build_comparisons()
  brands.py          # märkesvaru-paring: private-label-detektion + förslag (semantisk via embeddings, lexikal fallback) + APIRouter (/v1/admin/...)
  embeddings.py      # lättviktiga static-embeddings (model2vec, CPU/numpy) för semantisk produktnamn-likhet; lazy, degraderar till lexikal
  details.py         # EAN-produktinfo (fetch_for_ean): Axfood /p/{code} + Coop personalization-API + ICA SSR-detalj (handla.ica.se, cid via globalsearch)
  catalog.py         # unified katalog-sök (live fan-out -> GET /v1/products/catalog) + persisterad katalog: catalog_browse (q/category/chain/diet/manufacturer-filter), catalog_summary (kategori-räknare), catalog_manufacturers (tillverkar-aggregat -> GET /v1/products/catalog/manufacturers)
  images.py          # unified produktbild per EAN: resolve+resize (Cloudinary-transform)+lokal cache (image_cache/)
  apilog.py          # anropslogg: utgående (make_client-hook) + inkommande (record_incoming, källa "egen"), ring-buffer/statistik
  tags.py            # tagg-normalisering: effective_types() (tag_map-override + seed_types) + effective_provider() (provider_map-override + classify_provider)
  categories.py      # kategori-normalisering: råkategori -> kanonisk (category_map, derive-at-read)
  countries.py       # landnamn <-> ISO-kod (babel): split_origins() (sv-normaliserat ursprung + flerland-koder), flag_emoji()
  diet.py            # kost-klassificering ur ingredienser: classify_diet() -> vegan/vegetarian/none (fristående, delas av details + bläddra-filtret)
  manufacturers.py   # tillverkar-/varumärkesnormalisering (derive-at-read): manufacturer_key() (gruppering) + canonical() (display, MAP-override + auto legal-suffix-städning); admin-redigerbar manufacturer_map
  auth.py            # bcrypt + current_user/public_user (app) + current_admin/public_admin (konsol)
  sync.py            # run_sync() + warm_axfood_eans()/warm_coop_categories()/warm_ica_categories() (kategori-förvärmning; ICA capad+inkrementell). STATE per kedja
  adapters/
    base.py          # make_store(), tags_from_services(), normalize_hours() + week/exceptions (expand_sv_label, enrich_exceptions via holidays), _norm_phone (phonenumbers)
    ica.py coop.py willys.py hemkop.py lidl.py citygross.py   # butiks-adaptrar, fetch_all() -> UnifiedStore[]
    axfood_common.py # fetch_features() (CMS -> tags) + parse_week/parse_exceptions (Axfood-öppettider)
    ica_token.py keys.py     # token/nyckel-hämtning (auto-förnyelse, scrape-on-401)
    ica_offers.py axfood_offers.py coop_offers.py   # erbjudande-adaptrar
web/                 # frontend (statisk, ingen build)
  index.html app.js style.css   # karta + sidopanel + erbjudande-/jämförelse-/produktsök-paneler
  admin.html         # API-konsol (/admin): översikt+synk, API-anrop, datakällor (egna API:er som utfällbara kort + JSON-trädvy), taggar, kategorier, märkesvaror, API-nycklar
tests/test_schemas.py  # drift-test: verkliga svar valideras mot schemas-modellerna
pyproject.toml .env stores.db   # i repo-roten (BASE_DIR)
```

**Beroenden utöver basstacken:** `babel` (CLDR-landnamn för brand/origin-split),
`phonenumbers` (telefon-normalisering), `holidays` (svensk helgdagskalender för
öppettidsavvikelser), `model2vec` (static-embeddings, CPU/numpy - ingen torch - för
semantiska märkesvaru-förslag). Se `pyproject.toml`.

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
- **Bulk-förhämtning av erbjudanden (`sweep_offers` + `POST /v1/offers/sweep`):** proaktiv motsats
  till lazy-hämtningen. Sveper alla offer-stödda butiker (`database.offer_stores`) och hämtar de som
  inte är färska (`_offers_fresh` - redan valid_to-medveten, så efter en kall fyllning refetchas bara
  utgångna). Per kedja: bunden parallellism + paus + exponentiell back-off/retry per butik + circuit
  breaker (fel i rad -> pausa kedjan). Egen cadence `OFFERS_SWEEP_CRON` (default varje timme); INGEN
  kall sweep vid uppstart. Schemaläggaren är generisk: `run_scheduler(cron, tz, job, label)` kör både
  butikssynk och sweep. `SWEEP_STATE` (per-kedje-räknare + `last_errors`) + `database.offers_coverage`
  (nuvarande cachade erbjudanden per kedja) visas i konsolens Översikt. Arkiverar prishistorik via
  samma `replace_store_offers`. Efter en sweep körs `warm_after_sweep`: Axfood-EAN warmas ur de NYSS
  cachade koderna (`database.axfood_offer_codes` -> `sync.warm_axfood_eans_cached`, komplett kodmängd,
  ej sampling) + Coop/ICA-kategori (`ica_offer_eans`/`coop_offer_eans` är redan offers-baserade).
  ICA/Coop bär EAN inline i offers (cachas direkt); bara Axfood kräver code->EAN-resolve.
- **Prishistorik (steg 4, `offer_observations` + `GET /v1/products/{ean}/history`):** offers
  churnar vid synk (`replace_store_offers` = DELETE+insert), så historiken skrivs append-only.
  `archive_offers` (kallas före replace) skriver en observation per offer NÄR (pris/jämförvärde/
  savings/valid_to) ändrats sedan senaste -> kompakt prisförändrings-logg, **per butik** (avvikelser
  per butik). `savings`+`member_price` låter ordinarie pris härledas. `database.price_history(ean)`
  grupperar per kedja och kollapsar lika prisnivå (butiker med samma pris -> en punkt, `stores`
  räknar). Konsument-appens produktmodal ritar en inline-SVG stegfunktion (lucka vid utgånget
  erbjudande - fyndspårning, inte prisindex). Stats i konsolens Översikt (`offer_observations_stats`).
  **Axfood-observationer saknar inline-EAN** (offers bär `eans=[]` vid arkivering): `archive_offers`
  fyller därför EAN ur `ean_cache` (code=offer_id) vid skrivning, OCH `price_history` reverse-resolvar
  Axfood-koder för EAN:en read-time - så Willys/Hemköp-historik kommer med cross-chain även för rader
  arkiverade innan koden warmades.
- **Coop-berikning:** `coop.py` gör ett detalj-anrop per butik (bunden
  parallellism) för `services` -> tags och `concept` -> brand. Tyngre synk men
  ger samma metadata som ICA.
- **Kategori-normalisering (`categories.py` + `category_map`):** kedjornas råkategorier
  mappas till en kanonisk lista (`CANONICAL_CATEGORIES`), derive-at-read (admin-flik
  redigerar mappningen utan omsynk). Offer-nivån är grov; **produktdetaljens kategori
  föredras** i `get_store_offers` när den finns (`product_info.category_raw/source` ->
  `category_from_detail`). Förvärmas globalt per EAN: Axfood via `ean_cache.category`
  (`warm_axfood_eans`, samma `/p/{code}` som EAN-warmingen), Coop via `product_info`
  (`warm_coop_categories`, batchat personalization-API), ICA via `product_info`
  (`warm_ica_categories`, breadcrumb-topp -> `ica_nav`; capad+inkrementell då ICA-detalj saknar
  batch, egna märken först). Coops offer-nivå (Färsk/Kolonial/Nonfood) är opålitlig -> coop_nav
  (navCategories-topp) overridar. Viktvaror (slump-EAN) saknar produktdetalj -> faller till `ovrigt`.
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
  `private_brands`), lista över private-label-produkter ur offers, **paringsförslag rankade på
  semantisk namn-likhet** (`rank_candidates`: rensade namn-embeddings via `embeddings.py` +
  förpacknings-bonus, cosine-grind; lexikal `score` som fallback - fångar synonymer som
  token-överlapp missar, "Krossade Tomater"~"Tomatkross"), produktbild + lazy rik detalj.
  **EAN-centrerat:** en produkt = en EAN;
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
  Coop hämtas även när Axfood har gles näring. **Tredje källa: ICA** (`_fetch_ica`) - SSR-
  produktdetaljen `handla.ica.se/produkt/{consumerItemId}` (nås med browser-headers, ej WAF-
  blockad mot rätt headerset). EAN->consumerItemId via ICA:s globalsearch (butiks-scopat, så
  `database.ica_resolve_accounts` provar flera profiler; EAN nollpaddas till 14), cid cachad i
  `ica_item_map` (''=försökt utan träff). Microdata + sektioner parsas; näring i två varianter
  (`<table>` minifierad/whitespace + komma-`<p>`). ICA hämtas för egna märken (prefix 731869,
  som Axfood/Coop saknar) + som sista fallback. Merge tar längsta textfält + rikaste näring,
  `sources` listar bidragande källor. Allergener (`extract_allergens`) ur ingredienserna via
  vokabulär. Coop är EAN-global (branded i alla kedjor); ICA täcker dessutom ICA:s egna märken.
  **Cache-TTL (`product_info_cached`, lazy re-hämtning efter utgång):** positiv info 30 dygn
  (ingredienser/näring/ursprung kan ändras), negativ (`null`-rad, "inget fanns" -> `found:false`
  direkt utan re-hämtning) 14 dygn (så säsongsvaror kan dyka upp igen). Vid hämtningsfel cachas
  inget (kan vara transient). Kategori påverkas ej (deriveras vid läsning ur `category_map`).
  **Piggyback-fångst (partial-rader):** crawlen (Coop `_parse_coop_item`) och EAN-warmingen (Axfood
  `/p/{code}` via `parse_axfood_detail`) hämtar redan full produktinfo som tidigare kastades -
  den sparas nu som EN-källa `product_info` markerad `partial:true` (`save_product_info(..., partial=True)`,
  skip-if-fresh via `product_info_fresh_set`, batchat). Fyller katalogens ~32k tomma EAN gratis (inga
  extra anrop). On-demand-endpointen behandlar en `partial`-rad som cache-miss -> kör full `fetch_for_ean`
  (Axfood+Coop+ICA-merge) först när någon öppnar produktmodalen. `partial` strippas i `normalize_info`
  (intern, ej i API:t). Bredd ur crawl/warm, djup on-demand. **Schemalagd riktad uppgradering
  (`sync.upgrade_sparse_partials`, egen cadence `PARTIAL_UPGRADE_CRON`, default dagligen 02:00, `off`=av):**
  hämtar bara de GLESA partials (`database.sparse_partial_eans`, näring < 4) på nytt med full merge -
  fyller de verkliga närings-luckorna över tid (cap/körning + bunden parallellism + paus, ICA-WAF-skonsamt).
  Uppgraderade rader tappar `partial` -> faller ur kandidatmängden. Manuell trigger `POST /v1/admin/partials/upgrade`.
- **Produktbild per EAN (`images.py` + `GET /v1/products/{ean}/image`):** hittar bild-URL
  ur cachade offers, annars ICA-detaljens `og:image` ur `product_info` (täcker ICA:s egna
  märken utan offer-bild), annars ICA:s EAN-CDN. **Resizar via Cloudinary-transform** (källorna
  är cloudinary; `c_limit,w_400` -> små filer, kedjas på ICA:s redan namngivna transform), cachar
  bytes lokalt i `image_cache/` (metadata i `product_images`). Gör oss CDN-oberoende. Frontend-
  kort använder den med `onerror`-fallback till original-CDN-URL.
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
  `ADMIN_PASSWORD` (annars genererat + loggat en gång). **Decoupling:** konsol-UI:t anropar bara
  `/v1/admin/*` (och `/v1/console/auth/*`), aldrig konsument-endpoints - inför en framtida api/app/
  admin-split. Där konsolen behöver konsument-data finns admin-speglade routes (`/v1/admin/products/
  {ean}/info|image`) som delar resolver-helper med konsument-endpointen (`_resolve_product_info`/
  `_resolve_product_image`). Speglas/dupliceras vid en riktig split; lägg nya konsol-behov under `/v1/admin/*`.
- **Produktsök/-bläddring (`database.list_products` + `GET /v1/products/search|by-category`):**
  distinkta produkter ur **offers-cachen**, grupperade på EAN (cross-chain, Axfood-EAN via
  `ean_cache`) annars (kedja, namn), med samma berikning som `get_store_offers` (kanonisk
  kategori, brand/origin, package, deal_type) + kedjor + prisintervall. Namnmatchning i
  Python (Unicode-skiftläge; SQLite `LOWER` fäller bara ASCII). Begränsning: bara butiker
  vars offers hämtats (lazy-cache).
- **Unified katalog-sök (`catalog.py` + `GET /v1/products/catalog?q=`):** **live fan-out** mot
  kedjornas NATIVA sök-API:er -> **hela sortimentet, nationellt/representativt hyllpris** (ej
  butikslokalt, ej offers). En upptäckts-funktion, medvetet skild från `list_products`/`/search`
  (offers-cachen = butikslokala deals, appens kärna). Per kedja `_search_<chain>` -> gemensam
  normaliserad form, grupperat på EAN cross-chain (`CatalogProduct` med per-kedje-`prices`).
  Kedjor: City Gross (Loop54 search/quick), Coop (perso-search, `_parse_coop_item`), ICA
  (globalsearch + flaggskepps-accountNumber + token), Willys/Hemköp (`/search`, EAN+kategori+
  ursprung via `/p/{code}`-resolve capat + persisterat i `ean_cache` -> okända katalog-koder blir
  fristående poster; ursprung översätts EN->SV via babel). Lidl saknas (ingen EAN i deras sök). Per-kedja
  timeout -> delresultat. Honest schema: INGA deal_type/offer_count (hyllpris ≠ deal). Katalog-
  specifika kategorivokabulärer (CG superCategory, ICA mainCategoryName) seedade i `category_map`.
  Bara API (ingen frontend än). Auth via `require_consumer` som övriga `/v1`.
- **ICA-crawlens täckning (`catalog_crawl._ica_fetch_store`, storleks-villkorlig):** ICA:s globalsearch
  cappar offset HÅRT vid 20000 (`*` returnerar 0 docs vid offset >= 20000; `totalHits` är dock ärligt
  även när svaret cappas - en 44k-butik rapporterar 44422). Crawlen är därför villkorlig på butiksstorlek
  (`_ICA_OFFSET_CAP`):
  - **Butiker med totalHits <= 20000 (89,6% av ICA-butikerna, 1155/1289): `*`-walken ger HELA sortimentet
    -> ingen kategori-walk, ~100% täckning.** Sidstorlek `ICA_CRAWL_PAGE` (default 1000, verifierat take=2000
    funkar) -> ~10x färre requests än gamla take=100.
  - **Butiker > 20000 (~10%): `*` + kategori-walk för att nå bortom taket.** Kategorinamnen är den
    KOMPLETTA butiks-oberoende `mainCategoryName`-unionen (`database.ica_walk_categories`, skördad vid
    varje `*`-walk; små butikers ocappade walk bidrar med sin fulla kategorimängd -> unionen konvergerar
    mot ICA:s taxonomi) + en hårdkodad bred lista (`_ICA_CATEGORIES`) som säkerhetsnät. **Mätt empiriskt:**
    `mainCategoryName` saknas på ~0% av produkterna (ecom-nivåerna på ~6%), och queryString på kategorinamn
    är textsök med 100% recall (låg precision -> dedup på gtin). Flaskhalsen var kategori-UPPTÄCKT, inte
    matchning: med komplett union når en 44k-butik **~99,7% täckning** (44268/44422, ~179 requests) mot
    ~94% med bara den cappade walkens egna skörd. ecomLevel2 (260 noder) ger 97,5% till ~4x requests -
    ej använt (komplett mainCategory-union räcker). Delad av master- och per-butik-crawlern.
- **Coop-crawlens täckning (`catalog_crawl._coop_fetch_store`):** Coop har INTE ICA:s cap-problem.
  by-attribute (`categoryIds=<departement-rot>`, skip/take) paginerar utan offset-tak (verifierat: skip
  till sista produkten fungerar), och de ~19 departement-rötterna (harvestade ur produkternas
  navCategories, `_coop_harvest_roots`) är en KOMPLETT partition: 694 sök-samplade produkter låg alla i
  departement-crawlen (100%, 0 saknade rot). Enda förbättringen är sidstorleken: `COOP_CRAWL_PAGE`
  (default 300) i st.f. delade 100 - Coops take cappar vid ~400-499 (take<=400 OK, 500 ger tomt 200-svar),
  så 3x färre requests. Full crawl av en butik = ~12,7k distinkta EAN (summa departement-counts ~13,8k,
  ~8% överlapp dedupas på EAN). Delad av master- och per-butik-crawlern.
- **City Gross- + Axfood-crawlens sidstorlek (nationella, en crawl):** **City Gross** (Loop54
  `category/{id}/products`, skip/take) har INGET take-cap (verifierat take=5000 ger hela kategorin i ett
  svar) och inget skip-cap -> `CG_CRAWL_PAGE` (default 1000) = ~10x färre requests. 35 "kategorier" (flera
  är kampanj-/tvärsnitt: Veckans erbjudanden, Kolla priset... som överlappar departementen; dedup på
  produkt-id gör räknaren distinkt). **Axfood (Willys/Hemköp)** `/c/<slug>` (page/size) cappar däremot
  size HÅRT vid 100 (verifierat: size=500/1000/2000 ger alla 100, numberOfPages oförändrat) -> INGEN
  sidstorleks-vinst, lämnas på `CATALOG_CRAWL_PAGE`=100. Ingen page-cap (täckning komplett till
  numberOfPages); 19 topp-avdelningar, summa ~12,5k. Obs: Axfoods `/search/campaigns` tar size=1000, men
  katalog-browse-endpointen `/c/<slug>` gör det inte.
- **Per-butik-crawlens tidsprofil (uppmätt 2026-06-05, efter sidstorleks-höjningen):** dominansen är nu
  **~2/3 nätverk (HTTP-rundtur + JSON-parse av stora sidor), ~1/3 pace** (`store_crawl._PAGE_PACE`=0.35s/sida).
  Sidstorleks-höjningen tog bort *antalet* requests men gjorde varje tyngre (~0,65-0,70s/req även vid take=1000),
  så mer take ger nu avtagande nytta. Uppmätt/butik: ICA stor (44k) ~179 req ~180s; ICA liten (<20k) ~14-20 req
  ~15-20s; Coop (~12-15k) ~56 req ~59s. **De ~134 stora ICA-butikerna (>20k) = ~60% av ICA:s totaltid** trots
  10% av butikerna (hela kategori-walken). Extrapolerat enkeltrådat: ICA (1288) ~11h, Coop (214) ~3,5h; med
  AIMD-parallellism (tak `_MAX_CONC`=12, ICA+Coop parallellt) ~1-1,5h för full bägge-kedjor-crawl. Inkrementellt
  mycket billigare (`stores_to_crawl(max_age_hours)` hoppar nyligen crawlade). Hävstänger ej utnyttjade (se
  ROADMAP "Crawl-prestanda"): lägre pace, droppa breda termer på stora butiker, inom-butik-parallellism.
- **Per-butik-pris konsument-läsväg (Steg 6-payoffen, `GET /v1/products/{ean}/prices`):** SCOPAR
  hyllpriset till det användaren bryr sig om - `lat`/`lng`/`radius` (billigast nära en plats), `favorites=true`
  (inloggad användares favoritbutiker) eller `stores=chain:id,...` (explicit). Skild från
  `/store-prices` (som ger ALLA butiker grupperat per prisnivå - intervall-modalen). `database.store_prices_geo`
  mappar FYSISK butik (`stores`.lat/lng + native) -> ledger/account -> `catalog_store_prices` (en ledger kan
  täcka flera fysiska butiker -> pris per fysisk butik, geo korrekt). Haversine-filter (delad `geo.haversine`),
  billigast först. Geo-scope = bara prissatta butiker; favorit/explicit = alla (pris null = inget data för
  butiken -> visa elegant). Bara ICA/Coop (butiksprissatta). Matkasse-jämförelse (`/compare/basket`) är nästa steg.
- **API-kontrakt (`schemas.py`, en sanningskälla).** Pydantic-modeller för alla konsument-
  endpoints, kopplade **dokumenterande** (`responses={200: {"model": M}}`) - INTE
  `response_model` (som skulle re-serialisera och tappa fält). /docs blir typat, och
  konsolens fält-doc (`config.OWN_APIS` `returns`) deriveras ur samma modeller
  (`schemas.fields_doc`). `tests/test_schemas.py` validerar verkliga svar mot modellerna
  (document-only enforcar inte i runtime). `app.openapi()` taggar /docs per path-prefix.

## Vanliga ändringar

- **Ny butiks-adapter:** lägg `app/adapters/<chain>.py` med `fetch_all()`,
  registrera i `sync.py` + `config.CHAINS`/`CHAIN_META`.
- **Ny erbjudande-adapter:** spegla `ica_offers.py`, koppla in i offers-routen i
  `main.py` (villkoret `if chain != "ica"`). Aktivera knappen i `web/app.js`
  (`s.chain === "ica"`).
- **Ny/ändrad konsument-endpoint:** lägg/uppdatera en modell i `schemas.py`, koppla
  `responses={200: {"model": M}}` på routen, lägg en post i `config.OWN_APIS` (med
  `returns=schemas.fields_doc(M)`), och täck shapen i `tests/test_schemas.py`.
- **Verifiera efter ändring:** `.venv/bin/python -c "from api.main import app; print('OK')"`
  + `.venv/bin/python tests/test_schemas.py`, starta sedan om dev-servern (se Körning ovan)
  och kontrollera `dev.log` för fel.

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
- **Coop produktsök (fullkatalog):** `POST external.api.coop.se/personalization/search/global`
  `?api-version=v1&store={ledger}&groups=CUSTOMER_PRIVATE&direct=true` (perso-nyckeln), body
  `{query, resultsOptions:{skip,take}}` -> `results.items[]` (`count` total). Item = samma
  entitet som `entities/by-id` (`_parse_coop_item`): ean, namn, `manufacturerName`,
  `salesPriceData.b2cPrice`/`b2bPrice`, `comparativePriceData`+`comparativePriceUnit`,
  `packageSize`, `navCategories`, ingredienser/näring, `imageUrl`. EAN + jämförpris inline.
- **ICA produktsök (NÅBART server-side, bekräftat):** `POST apimgw-pub.ica.se/sverige/digx/
  globalsearch/v1/search/quicksearch` med **public-access-token (Bearer, vi hämtar redan)** +
  `accountNumber` (butikens, ur native), body `{queryString, take, offset, accountNumber,
  searchDomain:"All", sessionId}` -> `products.documents[]` (`stats` har total). Item: `gtin`
  (EAN), `displayName`/`title`, `price` (sträng, per butik), `image` (resizebar cloudinary),
  `mainCategoryName`, `countryOfOriginName`. INGET jämförpris i söket. Via API-gatewayen, INTE
  den WAF-blockade ehandeln - så ICA:s katalog ÄR sökbar server-side (till skillnad från
  produktdetaljen som är WAF-skyddad).
- **Coop OCH ICA: pris + sortiment är BUTIKSSPECIFIKT (bekräftat empiriskt).** Båda sök-API:erna
  scopar på butik (`store={ledger}` resp. `accountNumber`) och returnerar olika pris OCH olika
  sortiment per butik - inte nationellt. Mätt: samma EAN 26,03 kr (Coop 251300) vs 33,08 kr (Coop
  Hallsberg 176110); ICA Vetenudlar 11,80 (profil 1003647) vs 14,14 (1003458). **Vi crawlar en FAST
  butik per kedja:** Coop `COOP_DETAIL_STORE` (251300, verkar vara den mest kompletta), ICA
  `ica_resolve_accounts()[0]` (1003647). Katalogradens hyllpris är alltså den butikens, inte
  nationellt - taggat i `catalog_products.store` (NULL = nationellt: Axfood/CG). Påverkar även
  **produktinfo + bilder** för Coop (perso-fetch scopas till 251300, så produkter som bara finns i
  andra butiker saknas; ICA-detaljen provar dock flera profiler via `ica_resolve_accounts`). Andra
  butikers ledgers (t.ex. 196183/176310) kan ge tomt i perso-söket (ej e-handelsindexerade).
  **Willys/Hemköp (Axfood) och City Gross är NATIONELLA (empiriskt bekräftat, ej bara antaget):**
  samma `/search`- resp. Loop54-pris oavsett `storeId`/`siteId`-param (testat utan/2102/2103 för
  Willys, utan/21/46 för CG -> identiska priser). `storeId` är dessutom Axfoods param för erbjudanden,
  så hade söket varit butiksscopat hade den slagit igenom. Därför `store=NULL` för dessa tre.
- **Per-butik-Coop/ICA queryability + zoner (research 2026-06-04, för ev. "spåra alla butikspriser"):**
  **Coop:** bara ~43% av butiks-ledgers är frågbara i perso-söket (bred sökning "mjölk" gav träffar i
  13/30; resten 0 -> ej e-handelsindexerade). `ledger` är rätt param (ej `storeId`). Priszoner är
  INKONSEKVENTA per förening (`ownerName`): Coop Nord lika inom föreningen, Coop Östra olika inom -> ingen
  "en butik/förening"-genväg. **ICA:** alla 1289 butiker har `accountNumber` och ALLA svarar (100%
  queryable via gatewayen); per-butik-pris, ingen förenings-struktur. **Metod:** testa queryability med
  bred SÖKNING, inte by-id på fasta EAN (butikssortiment skiljer -> 0 EAN-träffar != ofrågbar).
- **Willys/Hemköp produktsök (fullkatalog):** `GET {willys|hemkop}.se/search?q=&page=&size=`
  (ingen auth) -> `results[]` + `pagination.totalNumberOfResults`. Item: `code` (Axfood-
  artikelkod - EAN resolvas via `ean_cache`/`/p/{code}` som offers), `name`, `manufacturer`,
  `priceValue`, `comparePrice`+`comparePriceUnit`, `googleAnalyticsCategory`, `image`. EAN ej
  inline (enda kedjan som kräver resolve även i sök).
- **Willys/Hemköp step-1-tjänster:** CMS-komponenten `.../axfoodcommercewebservices/v2/
  {catalog}/cms/components?componentIds={Component}&storeId={id}` -> `storeFeatures`
  ({id: label}) -> tags. Component: `WillysDefaultRightColumnStoreInfoComponent` /
  `HemkopDefaultRightColumnStoreInfoComponent`. Hämtas per butik i `axfood_common.fetch_features`.
- **Lidl:** `x-apikey`, geo_box-svep; erbjudanden är regionala (`offerRegion`).
