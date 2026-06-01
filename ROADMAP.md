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
    - [ ] **Normalisera + berika produktinfon (större produktinfo-API-jobb).** Nu
      first-hit-vinner per EAN (Axfood = näring, Coop = ingredienser/ursprung). Bör:
      - slå ihop fält över källor (näring från Axfood + ingredienser från Coop för
        samma EAN) eller välja på rikedom, inte bara "först vinner";
      - **strukturera näring** till enhetliga fält;
      - **plocka ut allergener** ur ingredienslistan (svenska listor versaliserar dem,
        t.ex. "SMÖR (pastöriserad GRÄDDE...), ... MJÖLK") -> strukturerad allergenlista
        att visa/filtrera på (>= 2 versala bokstäver, hantera E-nummer/förkortningar).
    - [ ] **ICA native detalj** är bot-skyddat (AWS WAF, bekräftat via curl + obscura) -
      täcks tills vidare av Coop-fallback för branded varor; ICA:s egna märken går ej.
    - [ ] (övervägt) Bredare semantisk uppdelning av API:t (butiker/erbjudanden/produkter/
      compare i egna routrar) - EJ gjort: bara `products` bröts ut (ny konsument krävde
      det); resten är redan modulärt internt, reorg = churn utan vinst på single-container.
    - [ ] **Unified EAN -> produktbild-tjänst (+ cache + varianter).** Nu hotlinkas bilder
      direkt från kedjornas CDN (assets.icanet.se, cloudinary, axfood) i list-/erbjudande-/
      paringsvyer. Bör bli en egen EAN-nyckad bild-endpoint (likt `/v1/products`):
      - cacha/proxa lokalt för robusthet, fart och CDN-oberoende;
      - välj bästa bild per EAN på **kvalitet/storlek** över källorna;
      - om vi får bilder i flera storlekar och de skiljer sig: cacha dem som **valbara
        varianter** (thumbnail/full) som klienten kan begära efter behov.
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
    - [ ] **API-anrop-fliken: visa även våra egna inkommande anrop** (nu loggas bara
      utgående httpx-anrop mot kedjorna via apilog). Lägg till middleware som loggar
      inkommande requests mot vårt eget API, och **filtrera "senaste anrop" på källa**
      (kedja/egen + status).
    - [x] **API-testverktyg i konsolen (#sources-fliken) BYGGT.** Kör egna endpoints
      (förinställda exempel + fri sökväg) och kedjornas upstream-API:er via admin-proxy
      (`/v1/admin/proxy`, whitelistade hostar, server-side nyckel/token).
    - [ ] **Externa API-nycklar (förbered för publik konsumtion).** API:t/konsolen ska
      kunna utfärda/återkalla API-nycklar så externa konsumenter kan anropa vårt API
      autentiserat (t.ex. `/v1/products`, `/v1/stores`). Nyckel-tabell + utfärdande i
      konsolen + nyckelvalidering (header) + ev. rate limiting/scope per nyckel. Hänger
      ihop med att `products` redan bröts ut som egen publik domän.
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
