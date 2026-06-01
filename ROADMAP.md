# ROADMAP - matbutiker

Status och plan. Uppdaterad 2026-05-31.

Slutmål: app som visar aktuella erbjudanden från butiker nära användaren, med
favoritbutiker och - där datan tillåter - jämförelse av samma/liknande produkter
mellan butiker, samt prisutveckling över tid.

---

## Steg 1 - Butiker (KLART)

Unified store-API för 5 kedjor, ~2682 butiker, Leaflet/OSM-karta. Spec i
`UNIFIED-API.md`. Självförnyande nycklar (ICA token-API, Coop/Lidl scrape-on-401).

| Kedja | Butiker | Metod |
|-------|--------:|-------|
| ICA | 1288 | `storeslist`-API (Bearer) |
| Coop | 722 | `proxy.api.coop.se` lista |
| Willys | 254 | `/axfood/rest/store` |
| Hemköp | 206 | `/axfood/rest/store` (samma som Willys) |
| Lidl | 212 | Schwarz geo_box-svep (`x-apikey`) |

---

## Steg 2 - Erbjudanden (PÅGÅR)

`GET /v1/stores/{chain}/{store_id}/offers` - lazy, 6h cache, `offers`-tabell.

### Erbjudande-källor per kedja

| Kedja | Källa | Strukturerad | EAN | In-store-trogen | Status |
|-------|-------|:---:|:---:|:---:|--------|
| **ICA** | server-renderad `/erbjudanden/{butik}` (`weeklyOffers`) | ✅ | ✅ inline | ✅ (butiksspecifik) | **byggd** |
| **Willys** | e-handel `/search/campaigns?storeId=&size=1000` + `/axfood/rest/p/{code}` | ✅ | ⏳ via detalj (ej hämtad än) | ⚠ e-handelspris | **byggd** (utan EAN) |
| **Hemköp** | identiska Axfood-endpoints | ✅ | ⏳ via detalj (ej hämtad än) | ⚠ e-handelspris | **byggd** (utan EAN) |
| **Coop** | `external.api.coop.se/dke/offers/{ledger}` (offers-nyckel) | ✅ | ✅ `externalId` inline | ✅ (DR = reklambladet) | **byggd** |
| **Lidl** | regionalt (`offerRegion`/`zone`), Schwarz | ? | ? | ? | ej undersökt |

Detaljerade endpoints finns i minnesfilerna `ica-offers-data-source` och
`axfood-offers-data-source`.

### Viktiga insikter

- **Erbjudande-källor varierar enormt i svårighet** (till skillnad från butiks-
  API:erna). ICA var lätt (server-renderad + EAN). Axfood löstes via e-handelns
  `campaigns`-API. Willys/Hemköps **reklamblad** (iPaper/PDF) är svårlästa: iPaper
  laddar produkter ("enrichments") i per-sida-chunks bakom 403-CDN, kräver
  browser-automation - därför valdes e-handels-API:t istället.
- **E-handelspris ≠ garanterat butikspris.** Axfood-campaigns är butiks-scopade
  och inkluderar Klubbpris-flagga, men bör spot-valideras mot reklambladet.
- **EAN finns där matchning är möjlig.** ICA + Axfood bär riktiga tillverkar-GTIN
  för märkesvaror. Egna märken (ICA-prefix, Garant, Änglamark) och rörlig vikt
  (2-prefix/butiksintern) matchar inte cross-chain - vilket är korrekt.

### Att göra (steg 2)

- [x] **Axfood-erbjudande-adapter** (`axfood_offers.py`) för Willys + Hemköp:
      `campaigns` (size=1000), mappar pris/jämförpris/kampanjpris/`member_price`
      (Klubbpris)/mekanik/validitet. Inkopplad i offers-routen + frontend.
- [x] **Coop-erbjudande-adapter** (`coop_offers.py`): `dke/offers/{ledger}`
      (offers-nyckel, skrapas via `keys.scrape_coop_offers_key`). EAN inline.
- [x] (step 1) Fyll Coops `tags` + `brand` från butiksdetaljens `services`/`concept`
      (722 detalj-anrop, bunden parallellism i `coop.py`).
- [x] **code -> EAN-cache** för Axfood (byggd i steg 3, se `ean_cache` nedan).
- [ ] Rekognoscera Lidl erbjudande-källa + EAN (regionalt via `offerRegion`).
      Lidl verkar bara ha PDF-reklamblad -> kräver fångst av nätverksanrop / OCR.
- [x] **ICA To Go** - hanterad: `togo`-typen finns i vokabulären och `seed_types`
      taggar labels med "to go"/"togo" automatiskt.

---

## Steg 3 - Jämförelse, favoriter, närhet (PÅGÅR)

- **Nivå 2 - exakt produkt (EAN): BYGGD** (`app/matching.py` + `GET /v1/compare/near`).
  Grupperar närliggande butikers erbjudanden per EAN, kräver >= 2 olika kedjor.
  Strikt EAN-normalisering (rejekta 2-prefix/ogiltig längd -> inga falska matchningar).
  **Jämför på enhetspris (jämförpris kr/kg|l|st)** när alla har det, annars råpris
  med `compare_by`-flagga - eftersom råpris ej är jämförbart (förpackningsstorlek,
  multibuy "2 för 129", medlemspris). `member_price` visas per post.
  - I drift för **ICA + Coop** (EAN inline). Grupper med identisk erbjudande-
    uppsättning slås ihop (`_merge_same_deal`) så att en kampanj som täcker flera
    varianter (t.ex. "Zeta pasta 3 för 39" i flera former) blir ETT kort med
    `variant_count`/`variants`, inte ett per EAN.
  - [x] **Axfood EAN-resolution (fas 2): BYGGD.** `ean_cache`-tabell (code->EAN,
    persistent) + `axfood_offers.fetch_eans` (bunden parallellism). Compare resolvar
    bundet (`EAN_RESOLVE_CAP=150` nya/anrop), cachen warmar över tid. Alla fyra
    kedjor (ICA, Coop, Willys, Hemköp) är nu med i `COMPARE_CHAINS`.
    - [x] **EAN-förvärmning BYGGD** (`warm_axfood_eans`): bakgrundsjobb som efter
      varje synk + vid uppstart samlar Axfood-koder från ett urval butiker
      (kampanjerna är nationella) och fyller den globala code->EAN-cachen. Compare
      i ett färskt område gick från ~6s till ~1,6s (bara lazy offers-hämtning kvar).
  - [x] Frontend "jämför i närheten"-vy ovanpå `/v1/compare/near` ("Prisjämför här"
        i sidopanelen -> produktkort med pris per kedja, billigast markerad).
- **Nivå 1 - kategori + enhetspris** ("billigaste mjölk per liter"): separat feature,
  kräver ingen matchning. Medvetet INTE med i matchningslagret (undviker falsk-
  gruppering). Byggs vid behov.
- [x] **Para ihop kedjornas egna märkesvaror (v1 BYGGT).** Egna märkesvaror saknar
  gemensam EAN och matchar aldrig via nivå 2. Konsolflik "Märkesvaror" (`api/brands.py`):
  redigerbar private-label-vokabulär per kedja (`private_brands`), lista över private-
  label-produkter ur offers, namn+förpacknings-baserade matchningsförslag, godkänn
  eller sök manuellt. Stabil EAN-nycklad mappning (`product_matches`) som
  `matching.build_comparisons` slår ihop på (manual=True-kort). Produktbild visas.
  - [x] **EAN-produktinfo som egen domän BYGGT.** `details.py` (`fetch_for_ean`) +
    EAN-nyckad cache `product_info` + **publik** `GET /v1/products/{ean}?prefer_chain=`
    (utanför admin-routern -> konsument-appen + konsolen delar den). Källor: Axfood
    `/axfood/rest/p/{code}` (ingredienser/näring/ursprung, EAN via ean_cache) + Coops
    personalization-API (POST EAN-array, skrapad nyckel, scrape-on-401). Coop funkar
    EAN-globalt -> berikar branded varor även i ICA (vars ehandel är bot-skyddad).
    `source` sparas i cachen. Datakällor-fliken listar källorna.
    - [x] **Visas i paringsvyn** (basprodukt auto, kandidater/paringar via "info").
    - [x] **Erbjudande-info-modal i konsument-appen BYGGT** ("Innehåll & näring" på
      erbjudandekort med EAN -> modal). OBS: bara där EAN finns klient-sida (ICA/Coop
      inline; Axfood-EAN resolvas lazy, saknas ofta på kortet).
    - [x] **Normalisera + berika produktinfon BYGGT.** `details.fetch_for_ean` hämtar
      Axfood + Coop och **mergar fält-för-fält** (`_merge`): textfält = längsta icke-tom,
      näring = rikaste listan, labels = union, `sources` listar bidragande källor. Coop
      hämtas även när Axfood har gles näring. **Coop-näring fixad** (låg i `nutrientLinks`,
      inte `nutrientInformation` - 8-12 poster). **Allergener** extraheras ur VERSALA ord
      i ingredienserna (`extract_allergens`). Visas i paringsvyn + erbjudande-modalen.
      - [ ] Strukturera/mappa näring till kanoniska fält + ordna (nu rå label/value/unit).
      - [ ] Allergener via vokabulär-match (nu alla VERSALA ord; ingen kedja har rent
        strukturerat allergen-fält - dietTypeInformation/labels är diet/ursprung).
    - [ ] **ICA native detalj** är bot-skyddat (AWS WAF, bekräftat via curl + obscura) -
      täcks tills vidare av Coop-fallback för branded varor; ICA:s egna märken går ej.
    - [ ] (övervägt) Bredare semantisk uppdelning av API:t (butiker/erbjudanden/produkter/
      compare i egna routrar) - EJ gjort: bara `products` bröts ut (ny konsument krävde
      det); resten är redan modulärt internt, reorg = churn utan vinst på single-container.
    - [x] **Unified EAN -> produktbild-tjänst BYGGT (v1).** `GET /v1/products/{ean}/image`
      (`images.py` + `product_images`-tabell + bytes i `image_cache/`): hittar bild-URL ur
      cachade offers (annars ICA:s EAN-CDN), **resizar via Cloudinary-transform** (c_limit
      400px - Coop gav 11 MB full-res, nu ~16 KB), cachar lokalt -> CDN-oberoende + snabbt.
      Erbjudande-/jämförelsekort använder den (fallback till CDN-URL vid fel).
      - [ ] **Storleks-/kvalitetsvarianter:** `?size=thumb|full` cachat per (ean, storlek)
        + välj bästa källa på kvalitet. Nu en fast storlek (400px).
  - [ ] **Fulla sortiment** (ej bara offers) - se separat övervägande; ger komplett
    produktlista + hyllprisjämförelse men är ett eget hämtnings-/lagringsprojekt.
  - [ ] **Smart auto-förslag** kan förbättras (nu namn-token + förpackningsstorlek;
    ev. LLM/embeddings som domare).
- [x] **Tagg-normalisering BYGGD.** Kanonisk vokabulär (`config.CANONICAL_TAG_TYPES`)
  + editerbar `tag_map` (label -> typ). Typen härleds vid läsning (`tags.effective_type`):
  override från tag_map annars `classify_service`-seed, så admin-ändringar slår igenom
  direkt utan omsynk.
- [x] **Admin-dashboard BYGGD** (`/admin`, `web/admin.html`) - blev större än bara
  taggar: översikt (kedjor/cacher/schemaläggare), **API-anrop** (logg + statistik per
  källa via httpx-hook i `apilog.py`), datakällor per kedja, och tagg-underhållet
  (mappa omappade råetiketter mot vokabulären).
  - [x] **API-konsol med separat admin-auth BYGGT.** Admin/drift är skild från
    kartappen: `web/admin.html` på `/admin` heter "API-konsol" och har egen
    inloggningsruta (`/v1/console/auth/*`). Konsol-admins ligger i egen tabell
    `admin_users` med egen session-nyckel (`admin_uid`) - en app-användare är
    aldrig admin, ett konsolkonto kan inte logga in i appen. `require_admin`
    (-> `current_admin`) gatar alla `/v1/admin/*`, `/v1/tags*` och `/v1/sync*`.
    Synk-knapp + status flyttade till konsolens Översikt-flik. Konsolkontot seedas
    vid uppstart (`ensure_admin`) från `ADMIN_EMAIL` (generisk default i koden,
    sätts per instans via env/`.env`) + `ADMIN_PASSWORD` (annars genererat + loggat).
    - [ ] Persistent anropslogg (nu in-memory).
    - [x] **API-anrop-fliken: egna inkommande anrop + filter BYGGT.** Middleware loggar
      inkommande /v1-requests (`apilog.record_incoming`, källa "egen", inkl. status/ms;
      hoppar över anropslogg-pollern). Fliken filtrerar "senaste anrop" på källa
      (egen/kedja) + status (OK/fel); filtret överlever 5s-uppdateringen.
    - [x] **API-testverktyg i konsolen (#sources-fliken) BYGGT.** Kör egna endpoints
      (förinställda exempel + fri sökväg) och kedjornas upstream-API:er via admin-proxy
      (`/v1/admin/proxy`, whitelistade hostar, server-side nyckel/token).
    - [x] **Externa API-nycklar BYGGT.** Konsolflik "API-nycklar": utfärda (visas en
      gång, lagras hashad) + återkalla. `X-API-Key`-middleware validerar om nyckel skickas
      (ogiltig/återkallad -> 401) men gatar inte de öppna läs-endpoints. `api_keys`-tabell.
      - [ ] Kvar: rate limiting + scopes per nyckel (när en faktisk konsument finns).

### Plattform / aktivera andra frontend-appar

Per-domän-REST:en är redan ren (stores/offers/products/compare/chains). Det som
saknas för en *andra/extern* frontend är tvärgående enablers, inte fler utbrutna
domäner:

- [x] **Hela API:t gatat (ingen anonym åtkomst) BYGGT.** `require_consumer` på alla
  `/v1`-dataendpoints kräver inloggad app-användare (session/bearer) eller giltig
  API-nyckel. Kart-appen är nu en **inloggnings-vägg** (ingen anonym surf). Öppna:
  `/v1/auth/*`, `/v1/console/auth/login`, `/healthz`, sidorna `/`+`/admin`+`/static`.
- [x] **CORS för externa origins BYGGT.** Env-allowlist `CORS_ORIGINS` (default tom =
  oförändrat same-origin). Explicita origins + credentials, aldrig `*`. CORSMiddleware
  läggs bara om allowlist är satt.
- [x] **Token-baserad slutanvändar-auth BYGGT.** `POST /v1/auth/token` (e-post+lösen ->
  opak bearer-token, lagras hashad) + `GET/DELETE /v1/auth/tokens`. `current_user`
  accepterar både session-cookie och `Authorization: Bearer` -> icke-webb-klienter kan
  anropa `/v1/favorites` m.m.
- [ ] **Kurera OpenAPI-kontraktet.** `/docs` finns auto men oputsat - tagga endpoints
  per domän, markera publikt vs admin, lägg beskrivningar/responsmodeller, så
  integratörer har ett stabilt kontrakt att bygga mot.
- [ ] **Produktsök-endpoint (på namn/text).** Produkter nås nu bara via EAN
  (`/v1/products/{ean}`) eller offers/compare. En andra app vill kunna söka produkter
  på namn -> exponera sök (över cachade offers/produkter nu, fullständigt med
  sortiment-jobbet).
- [ ] **Kategori-endpoint** för bläddring/filtrering - beror på kategorinormaliseringen.
- [ ] (övervägt) Formell repo-/tjänstesplit api/ vs web/ - CLAUDE.md noterar att den
  är billig när en andra konsument dyker upp; men enablers ovan (CORS/auth/kontrakt)
  är det som faktiskt krävs, inte själva splitten.
    - [x] **/admin#tags: ladda inte om/sortera om vid klick BYGGT.** Typ-toggle och
      "↺ auto" uppdaterar raden in-place (ingen re-fetch/re-sort); raden stannar kvar.
      `del_tag` returnerar auto-typerna så även återställning sker in-place.
    - [x] **Sökfält för råetiketter i #tags BYGGT.** Filtrerar listan på råetikett/kedja
      (klient-sida, behåller ordning).
    - [ ] **Tillåt borttagning även av inbyggda tagg-typer.** Nu skyddas
      `BUILTIN_TAG_TYPES` från radering (annars kan `seed_types` producera en typ
      som inte finns i vokabulären). Vill att användaren ska kunna ta bort dem ändå
      - hantera följden (seedad typ utan vokabulär-post visas som omappad/other).
- **Favoritbutiker: BYGGD (endast inloggad).** Stjärn-toggle i butikslistan kräver
  inloggning (öppnar login-modal annars); "Bara favoriter"-filter + "Jämför mina
  favoriter" -> `GET /v1/compare/stores?stores=chain:id,...` döljs helt utloggad
  (CSS `body:not(.logged-in)`). Ingen localStorage-fallback.
  - [x] **Konton + server-favoriter BYGGT.** E-post + lösenord (bcrypt), session-cookie
    (SessionMiddleware, secret persisterad i DB -> överlever omstart). `users`/`favorites`-
    tabeller, `auth.py`, `/v1/auth/*` (inkl. `/v1/auth/password` för lösenordsbyte) +
    `/v1/favorites`. Favoriter är serverbundna -> synk mellan enheter.
    - [ ] Ev. magic-link/lösenordsåterställning (kräver SMTP) - ej byggt i v1.
    - [ ] **Prenumerera på produkter + erbjudande-notiser.** Användare ska kunna
      "prenumerera" på produkter (per EAN) och få notis när de finns på erbjudande,
      och/eller notis om vilka butiker inom X km som har erbjudanden. Prenumeration
      markeras på aktuella erbjudanden (nu) och framåt via produktsök/EAN-API:t. Kräver
      notis-kanal (e-post/push), bevakningstabell per användare och ett jobb som matchar
      nya erbjudanden mot prenumerationer.
    - [x] **Sortering i "Mina favoriters erbjudanden" BYGGT.** Sort-dropdown (störst
      besparing/lägst pris/A-Ö) + textfilter, som erbjudande-vyn.
    - [x] **Visa ALLA favoriters erbjudanden BYGGT.** `GET /v1/favorites/offers` +
      "Mina favoriters erbjudanden"-vyn: hela listan + `compared`-sektion (produkter
      hos >= 2 favoriter, samma EAN, billigast först). (Sortering kvar, se ovan.)
- **Närliggande erbjudanden:** geosök (finns) + erbjudande-lagret. `compare/near`
  laddar offers lazy för de ~12 närmaste butikerna; för ett tätt flöde kan ett
  schemalagt bulk-/radie-förhämtningsjobb behövas.

### Caveats att rama in

- **Erbjudande-data = "fyndspårning", inte prisindex.** Vi ser bara kampanjpriser,
  aldrig ordinarie hyllpris; en produkt försvinner ur tidsserien när den inte är
  nedsatt.
- **Multibuy/medlemsmekanik** ("3 för 2", Klubbpris, "max 1 köp") måste
  normaliseras till jämförbart enhetspris - största felkällan.
- **Kategorinormalisering:** varje kedja har egen taxonomi (ICA `articleGroup`,
  Axfood `N0x`-koder) -> kanoniskt träd, LLM-stödd mappning. Behövs även för
  bläddring i steg 3.
  - [ ] **Filtrera erbjudanden på kategori** (i erbjudande-/favorit-/jämförelse-vyer)
    när kategorinormaliseringen är på plats - kräver kanoniska kategorier, inte
    kedjornas råa `category_raw`.

---

## Steg 4 - Prishistorik (SENARE)

Tidsserie (`offer_observations`) per produkt/EAN för prisutveckling. Endast
meningsfull för nivå-2-matchade märkesvaror. ToS/juridik känsligare vid nationell
aggregering - stäm av innan skarp drift.
