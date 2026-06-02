# ROADMAP - matbutiker

Status och plan. Uppdaterad 2026-05-31.

Slutmål: app som visar aktuella erbjudanden från butiker nära användaren, med
favoritbutiker och - där datan tillåter - jämförelse av samma/liknande produkter
mellan butiker, samt prisutveckling över tid.

---

## Steg 1 - Butiker (KLART)

Unified store-API för 5 kedjor, ~2682 butiker, Leaflet/OSM-karta. Spec i
`UNIFIED-API.md`. Självförnyande nycklar (ICA token-API, Coop/Lidl scrape-on-401).
Normaliserade veckoöppettider (`opening_hours.week`/`exceptions`) för alla kedjor.

**Att göra (öppettider):**
- [x] **Helgdagsavvikelser normaliserade.** `enrich_exceptions` i make_store fyller saknat
  namn (Lidl: datum -> helgnamn) och saknat datum (ICA: helgnamn -> datum) via en svensk
  helgdagskalender (**holidays**-biblioteket + härledda aftnar: Midsommarafton/Julafton/
  Nyårsafton/Alla helgons afton m.fl., söndagar bortfiltrerade). ICA:s fritext-avvikelser
  (`Inventering 2026-06-01`, `Pizzafredag ...`) får datum ur ett inbäddat `YYYY-MM-DD` i
  labeln (som då rensas). Resultat: ICA 0/2442 utan datum, Lidl 0/212 utan namn. Datum
  visas i UI som `6/6`.

| Kedja | Butiker | Metod |
|-------|--------:|-------|
| ICA | 1288 | `storeslist`-API (Bearer) |
| Coop | 722 | `proxy.api.coop.se` lista |
| Willys | 254 | `/axfood/rest/store` |
| Hemköp | 206 | `/axfood/rest/store` (samma som Willys) |
| Lidl | 212 | Schwarz geo_box-svep (`x-apikey`) |

**Att göra (nya kedjor):**
- [x] **City Gross som 6:e kedja BYGGT** (Bergendahls) - butiker + erbjudanden + compare.
  - **Erbjudanden: BYGGT** (`adapters/citygross_offers.py`). `GET /api/v1/Loop54/category/
    2930/products?currentWeekDiscountOnly=true` (nationella veckoerbjudanden, ingen butiks-
    cookie - `store_id` ignoreras). **EAN inline** (`gtin`) + jämförpris -> rakt in i compare
    (citygross i `SUPPORTED_OFFER_CHAINS` + `COMPARE_CHAINS`). Ordinarie pris i `currentPrice`,
    erbjudandet i `promotions[].priceDetails` (kampanj-/medlemspris); savings = ordinarie-
    erbjudande. superCategory -> kanonisk (citygross-mappning i DEFAULT_CATEGORY_MAP). 263
    offers verifierade. **Bonus upptäckt:** Loop54 har även fullkatalog-sök (`search/quick`)
    + produktdetalj (`products/{id}`, strukturerad näring/allergener) - se unified-sök-todo.
  - **Butiker: BYGGT.** `GET https://www.citygross.se/api/v1/PageData/stores`
    (JSON, ~39 butiker, ingen auth). Per butik: `storeName`, `address` (streetAddress/
    zipCode/city), `storeLocation.coordinates` ("lat,lng"-sträng), `openingHours`
    (mon-sun + holidays, opens/closes ISO -> vår week-normalisering), `services`
    (booleans: fish/deli/bakery/catering/atg/scanning/svenskaSpel/atm/wifi/postnord/
    schenker -> tags), `contactInformation` (phone/email), `url`, `siteId`.
  - **storeNumber:** `GET /api/v1/sites/{siteId}/storeNumber` -> `{storeNumber, provider:"Axfood"}`.
    Erbjudandena går alltså via **Axfoods** infrastruktur (som Willys/Hemköp) -> EAN +
    jämförpris sannolikt tillgängliga på samma sätt.
  - **Erbjudanden: endpoint hittad, ett kvarvarande frågetecken.** `GET /api/v1/offers?
    size=999&currentweekoffer=true` (`nextweekoffer=true` för nästa vecka, `&category=`-
    filter) med `Cookie: store={storeNumber}` -> `{offers, aggregations, total}`. Cookien
    läses (utan -> 400 "Store Number is required"), men `total=0` för alla testade butiker/
    värden via curl. Butiks-väljaren sätter troligen en session/encodad cookie -> fånga det
    riktiga anropet via **obscura** (headless) i en uppföljning, sen verifiera EAN+jämförpris.
  - Ny adapter `adapters/citygross.py` (+ `citygross_offers.py`), registrera i `sync.py` +
    `config.CHAINS`/`CHAIN_META`/`DATA_SOURCES` + `COMPARE_CHAINS`.

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
      - [x] **Näring + allergener normaliserade (read-time, `details.normalize_info`).**
        Näring: kanonisk etikett-form (`energi`->`Energi` osv) + standardordning
        (`_NUT_ORDER`) + enhetsförkortningar (Kilojoule->kJ, Gram->g, Mikrogram->µg).
        Allergener: vokabulär-match (`_ALLERGENS`, EU:s 14 grupper) i stället för alla
        VERSALA ord - inget KRAV/BCAA/trunkerings-skräp längre. Appliceras i
        `/v1/products/{ean}` på både cachad och färsk data (täcker de 507 cachade direkt),
        idempotent. Övervarnar hellre än missar (växtdrycker med "mjölk" i namnet). Kvar:
        ev. finputs av vokabulären (plant-milk-falskpositiv).
    - [ ] **ICA native detalj** är bot-skyddat (AWS WAF, bekräftat via curl + obscura) -
      täcks tills vidare av Coop-fallback för branded varor; ICA:s egna märken går ej.
    - [ ] **Utvärdera vad ICA:s upptäckta katalog-sök kan förbättra i befintligt** (söket är
      nåbart server-side, till skillnad från detaljen). Kandidater: (a) **ICA finare kategorier**
      via sökets `mainCategoryName`/`categoryName` (löser ev. caveaten om bara 9 `articleGroupId`);
      (b) **ICA produktinfo/bild för egna märken** som Coop-fallbacken missar (söket har gtin->
      namn/kategori/bild, dock ej ingredienser/näring); (c) ICA-bilder ur söket (resizebar
      cloudinary) i bild-resolvern. Avgör vilka som är värda en faktisk integration.
    - [ ] (övervägt) Bredare semantisk uppdelning av API:t (butiker/erbjudanden/produkter/
      compare i egna routrar) - EJ gjort: bara `products` bröts ut (ny konsument krävde
      det); resten är redan modulärt internt, reorg = churn utan vinst på single-container.
    - [x] **Unified EAN -> produktbild-tjänst BYGGT (v1).** `GET /v1/products/{ean}/image`
      (`images.py` + `product_images`-tabell + bytes i `image_cache/`): hittar bild-URL ur
      cachade offers (annars ICA:s EAN-CDN), **resizar via Cloudinary-transform** (c_limit
      400px - Coop gav 11 MB full-res, nu ~16 KB), cachar lokalt -> CDN-oberoende + snabbt.
      Erbjudande-/jämförelsekort använder den (fallback till CDN-URL vid fel).
      - [x] **Storleksvarianter BYGGT.** `?size=thumb|default|full` (150/400/800px via
        Cloudinary-transform), cachat separat per (ean, size). Erbjudande-/jämförelsekort
        använder `thumb`. Ogiltig size -> default.
        - [x] **Kvalitetsval av bästa bildkälla per EAN BYGGT.** `_resolve_url` väljer nu en
          resizebar cloudinary-bild (Coop före Axfood) framför ICA:s offer-bild (200px, ej
          resizebar); för ICA-produkter används EAN-CDN:n (400px) i stället. Tidigare togs
          första träffen (LIMIT 1). Bildcachen rensad så det slår igenom.
  - [ ] **Fulla sortiment** (ej bara offers) - se separat övervägande; ger komplett
    produktlista + hyllprisjämförelse men är ett eget hämtnings-/lagringsprojekt.
  - [ ] **Unified produktsök mot kedjornas NATIVA katalog-API:er.** Nuvarande
    `/v1/products/search` söker bara offers-cachen. Kedjorna har riktiga katalog-sök-API:er
    (City Gross `Loop54/search/quick?SearchQuery=`, Axfood-sök, ev. ICA/Coop) som täcker
    hela sortimentet - bygg ett unified sök ovanpå dem (live eller cachat). Närbesläktat
    med Fulla sortiment men lättare (sök on-demand, ingen full spegling).
  - [ ] **Dokumentera alla kedjors produktsök-/katalog-API:er** - endpoint, params,
    EAN/pris/jämförpris-tillgång (för unified-söket). Kartlagt hittills:
    - **City Gross** (Loop54): `GET .../Loop54/search/quick?SearchQuery=` (+ `products/{id}`,
      `category/{id}/products`). EAN (`gtin`) + pris + jämförpris inline.
    - **Coop** (personalization): `POST external.api.coop.se/personalization/search/global`
      `?api-version=v1&store={ledger}&groups=CUSTOMER_PRIVATE&direct=true`, header perso-nyckel
      (skrapas), body `{query, resultsOptions:{skip,take}}` -> `results.items[]` (`count` total).
      Varje item = samma entitet som `entities/by-id` (vi parsar redan i `_parse_coop_item`):
      `ean`, `name`, `manufacturerName`, `salesPriceData.b2cPrice` (hyllpris), `comparativePriceData`
      + `comparativePriceUnit`, `packageSize`, `navCategories`, `listOfIngredients`/`nutrientLinks`,
      `imageUrl`. EAN + jämförpris inline.
      - [ ] **Utforska Coops `b2bPrice`.** `salesPriceData`/`comparativePriceData`/`piecePriceData`
        bär både `b2cPrice` (51,58 - konsumentpris) och `b2bPrice` (48,66 - lägre, ~5-6% under).
        Vad är b2b-priset? Företags-/storkundspris, exkl. moms, eller medlemspris? Avgör om vi
        ska exponera/använda det (t.ex. som "pris exkl. moms" eller ignorera).
    - **ICA** (BEKRÄFTAT nåbart server-side): `POST apimgw-pub.ica.se/sverige/digx/globalsearch/
      v1/search/quicksearch` med public-access-token (Bearer, vi hämtar redan) + `accountNumber`.
      `products.documents[]`: `gtin`, `displayName`, `price` (sträng), `image` (resizebar
      cloudinary), `mainCategoryName`. INGET jämförpris. Via API-gatewayen, inte WAF-blockade
      ehandeln -> ICA:s katalog är sökbar (bara produktDETALJEN är WAF-skyddad).
    - **Axfood** (Willys/Hemköp): `/search/campaigns` (kampanjer) + `/axfood/rest/p/{code}`
      (detalj). Kvar: rent katalog-sök (inte bara kampanjer)?
    - **Slutsats:** alla utom Axfood-fullkatalog har sökbara katalog-API:er med EAN+pris
      (Coop+City Gross har även jämförpris; ICA saknar jämförpris men har per-butik-pris).
      Unified produktsök är därmed klart genomförbart.
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
    - [x] **Persistent anropslogg BYGGT.** apilog skriver nu till SQLite i stället för
      in-memory: `api_calls` (ring-buffer för feeden, beskärs till de 2000 senaste) +
      `api_call_stats` (kumulativ per host, överlever omstart). Egen autocommit-connection
      med `busy_timeout` (get_conn fick också busy_timeout) så apilog + synk-skrivningar
      inte krockar. `recent()`/`stats()` läser från DB. Samma svarsform -> frontend orört.
    - [x] **API-anrop-fliken: egna inkommande anrop + filter BYGGT.** Middleware loggar
      inkommande /v1-requests (`apilog.record_incoming`, källa "egen", inkl. status/ms;
      hoppar över anropslogg-pollern). Fliken filtrerar "senaste anrop" på källa
      (egen/kedja) + status (OK/fel); filtret överlever 5s-uppdateringen.
    - [x] **API-testverktyg i konsolen (#sources-fliken) BYGGT.** Kör egna endpoints
      (förinställda exempel + fri sökväg) och kedjornas upstream-API:er via admin-proxy
      (`/v1/admin/proxy`, whitelistade hostar, server-side nyckel/token).
      - [x] **Per-endpoint-utfällning i "Egna API:er" BYGGT.** Varje endpoint är ett
        `<details>`-kort: fäll ut för parametrar + returnerade fält (med beskrivningar) och
        en Testa-knapp per endpoint. Fält-metadatan ligger i utökad `OWN_APIS` (delade
        fält-listor `_RET_PRODUCT/_RET_STORE/...` som en sanningskälla).
    - [x] **Externa API-nycklar BYGGT.** Konsolflik "API-nycklar": utfärda (visas en
      gång, lagras hashad) + återkalla. `X-API-Key`-middleware validerar om nyckel skickas
      (ogiltig/återkallad -> 401) men gatar inte de öppna läs-endpoints. `api_keys`-tabell.
      - [ ] Kvar: rate limiting + scopes per nyckel (när en faktisk konsument finns).

### Normalisering (datakvalitet)

Genomgång av onormaliserade fält i unified-API:t (datadriven audit, sampling per
kedja). Rangordnat efter värde:

- [x] **#1 Jämförenheten i compare (`_norm_unit`) FIXAD.** `comparison_value` var redan
  ren float men `_norm_unit` gjorde bara lower()/trim(), så ICA/Coop `liter` vs Axfood `l`
  behandlades som olika enheter -> `build_comparisons` föll tillbaka på råpris även när
  alla var per liter. Nu kanoniseras enheten till basenhet (`liter/lit -> l`, `meter -> m`,
  första token före whitespace/komma/slash/plus, så `liter + pant`/`kg utan spad`/
  `kg 26,67/liter` -> `l`/`kg`), och platshållaren `Inget` -> None. Verifierat: en grupp
  som blandar liter/l jämför nu på enhetspris.
- [x] **#2 Normaliserad deal-typ (`deal_type`) FIXAD - härledd ur `price_text`.** Upptäckt
  under bygget: `mechanic_type` är opålitlig (ICA "Standard" blandar platt pris OCH multibuy;
  Axfood "MixMatchPricePromotion" är bara platt pris trots namnet; "SubtotalOrderPromotion"
  är viktpris, inte threshold). Den faktiska signalen ligger i `price_text`. `_deal_type()`
  i get_store_offers (derive-at-read) sätter `deal_type` (`multibuy`/`by_weight`/`flat`) +
  `multibuy_qty`: `"N för M"`/`"Köp N betala"` -> multibuy, `"X kr/kg|l"` -> by_weight, annars
  flat. Rå `mechanic_type` behålls. Coop bär ingen multibuy-text -> alltid flat (ärligt).
- [x] **#3 `package` FIXAD.** `get_store_offers` berikar med `package_size` (ren storleks-
  sträng: Axfood-brandprefix bort, "ca:" -> approx-flagga, ordenheter Gram/Milliliter/ST ->
  g/ml/st) + `package_value`/`package_unit` för enkla "N enhet" + `package_approx`. Ranges/
  multipack ("350-500 g", "12 x 33 cl") får ingen value (ärligt None). Täckning value+unit:
  ICA 60% / Coop 86% / Axfood 92-96%.
- [x] **#4 `offers.brand` FIXAD.** `_split_brand_origin` delar i `brand` + `origin` (lista av
  länder). ICA: "BRAND. [Ursprung] LAND" + bart "Colombia/Peru/..." (rena ursprung); Coop:
  ledande land-tokens = ursprung, resten varumärke. Landnamnen hämtas från CLDR via **babel**
  (svenska, alla ISO-länder) + alias holland/england - inte hårdkodat. Verifierat att babel
  täcker alla origin-tokens i datan och skiljer Coops länder från varumärken.
- [x] **#5 `phone` FIXAD.** `_norm_phone` i make_store (write-path) formaterar till svenskt
  nationellt standardformat via **phonenumbers** (libphonenumber) - rätt riktnummerlängd
  (`030-4678600` -> `0304-67 86 00`). Ogiltigt/tomt lämnas. Slår igenom vid synk.
- [x] **#6 Frontend för `deal_type` BYGGT** - badge på erbjudande-kortet (Flerköp med
  multibuy-antal / Per vikt) + filter-dropdown (Alla typer/Flerköp/Per vikt/Fast pris) i
  erbjudande-panelen. Korten använder nu även normaliserad `package_size` + `origin` i
  meta-raden. Gäller även favoritvyn och jämför-vyn (`deal_type` tillagt i `_OFFER_KEYS`
  så compare-utdata bär det; badge per rad). Ej webbläsartestad.
- Redan rent: `valid_to` (ISO), butikernas `brand` (snake_case-vokabulär), `comparison_value`.

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
- [x] **OpenAPI-kurering (grupperat /docs) BYGGT.** Custom `app.openapi()` taggar varje
  endpoint per path-prefix (Butiker/Produkter/Jämförelse/Metadata/Favoriter/Auth & konto/
  Admin) utan att tagga varje route manuellt, + app-description. `/docs` är nu grupperat.
  - [x] **Pydantic-responsmodeller BYGGT** (`api/schemas.py`). Alla konsument-endpoints har
    nu en modell, kopplad DOKUMENTERANDE (`responses={200: {"model": M}}`) - inte enforcing,
    så svaren re-serialiseras inte (inga fält tappas). Modellerna är enda sanningskällan:
    konsolens fält-doc (`OWN_APIS` returns) deriveras ur dem (`schemas.fields_doc`) och
    /docs visar dem. Drift-test (`tests/test_schemas.py`) validerar verkliga Product/Store/
    Offer-svar mot modellerna; övriga shapes HTTP-validerade. 28 schemas i kontraktet.
- [x] **Produktsök-endpoint BYGGT** (`GET /v1/products/search?q=&limit=&chain=`).
  `database.search_products` söker namn ur offers-cachen (Unicode-skiftlägesokänsligt),
  grupperar distinkta produkter på EAN (cross-chain) annars (kedja, namn), och returnerar
  normaliserade fält (brand/origin, package, deal_type, kanonisk category via samma
  berikning som get_store_offers) + kedjor + prisintervall + antal. Relevans-sort
  (prefix-träff, flest kedjor/erbjudanden). Begränsning: bara butiker vars offers hämtats
  (lazy-cache) - full täckning kräver sortiment-jobbet.
  - [x] **Frontend-produktsök BYGGT.** Sökruta i sidopanelen + egen produktpanel som gör
    både namnsök och kategori-bläddring (dropdown med kanoniska kategorier). Produktkort:
    bild, märke/förpackning/ursprung, kategori-chip, kedje-chips, prisintervall, deal-badge,
    "Innehåll & näring"-knapp + "N butiker". Ej webbläsartestad.
  - [ ] **Filtrera kartan på en vald produkt** ("visa butiker som har varan"). BEROR PÅ
    bredare offer-täckning / fulla sortiment: med lazy-cachen blir filtret antingen
    missvisande (precist = döljer de flesta butiker som faktiskt har varan, vi har bara en
    bråkdel cachad) eller meningslöst (per kedja = nästan hela kartan). Bygg när täckningen
    finns. "N butiker" på produktkortet har samma brasklapp (= cachade butiker, inte totalt).
- [x] **Kategori-endpoint BYGGT** (`GET /v1/products/by-category?category=&chain=&limit=`).
  Bläddrar distinkta produkter i en kanonisk kategori ur erbjudande-cachen, samma form som
  produktsöket (delar `database.list_products`). Okänd kategori -> 400.
- [ ] (övervägt) Formell repo-/tjänstesplit api/ vs web/ - CLAUDE.md noterar att den
  är billig när en andra konsument dyker upp; men enablers ovan (CORS/auth/kontrakt)
  är det som faktiskt krävs, inte själva splitten.
    - [x] **/admin#tags: ladda inte om/sortera om vid klick BYGGT.** Typ-toggle och
      "↺ auto" uppdaterar raden in-place (ingen re-fetch/re-sort); raden stannar kvar.
      `del_tag` returnerar auto-typerna så även återställning sker in-place.
    - [x] **Sökfält för råetiketter i #tags BYGGT.** Filtrerar listan på råetikett/kedja
      (klient-sida, behåller ordning).
    - [x] **Borttagning av inbyggda tagg-typer BYGGT.** BUILTIN-guarden borttagen i
      DELETE-routen. Följden hanteras: `effective_types` filtrerar mot vokabulären så en
      seedad typ utan vokabulär-post faller till `other`. Tombstone-tabell
      (`tag_types_removed`) hindrar att init_db återskapar borttagna inbyggda vid omstart;
      återskapande (POST) un-tombstonar. Manuella mappningar (tag_map) skyddas fortfarande.
    - [x] **Administrera speditörslistan (`provider`) + knyt till taggar BYGGT.** Speglar
      tagg-typ/tag_map-mönstret: editerbar `providers`-vokabulär (seedas) + `provider_map`-
      override per råetikett. `tags.effective_provider` = override annars `classify_provider`,
      filtrerad mot vokabulären (borttagen speditör -> None). Routes `/v1/providers`
      (GET/POST/DELETE) + `/v1/tags/provider` (POST/DELETE), in-use-guard. Admin Taggar-flik:
      speditör-vokabulärsektion + provider-dropdown per frakt-/post-rad (auto/override).
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
- **Kategorinormalisering:**
  - [x] **Fas 1 BYGGT** - kanonisk lista (17) + seed-mappning + derive-at-read
    (`categories.py`, `category_map`) + kategori-filter i erbjudande-vyn. ~87% täckning.
  - [x] **Fas 2 (produktdetalj-berikning) BYGGT** - kategori fångas per EAN ur
    produktdetaljen (Axfood `googleAnalyticsCategory`, Coop `navCategories`-topp),
    lagras i `product_info`, och föredras framför offer-nivån i `get_store_offers`
    (fixar t.ex. Willys ovrigt -> brod_bageri). Lazy: gäller där produktinfo hämtats.
  - [x] **Bulk-förvärmning av kategori BYGGT.** `ean_cache` fick `category`-kolumn;
    `warm_axfood_eans` fångar nu Axfoods `googleAnalyticsCategory` i samma `/p/{code}`-anrop
    som EAN-warmingen. get_store_offers fyller Willys saknade kategori därifrån. Willys
    gick 0% -> 70% täckning (warmas mot ~100% över fler körningar; koderna är nationella).
  - [x] **Admin-flik för kategori-mappning BYGGT** (speglar tagg-fliken: redigerar
    `category_map` in-place, omappade sorteras först/markeras). Avslöjade att
    produktdetaljens `googleAnalyticsCategory` använder andra segment-namn än kampanjen
    (t.ex. `kott-chark-och-fagel` vs `kott-fagel-och-chark`) - varianterna seedade.
    - [ ] **Kategori-flikens (#cats) tabell: bättre kedje-chips + filter/sortering.**
      `chain_key`-kolumnen visar råa nycklar (`axfood`/`coop_nav`/`citygross`...) utan
      kedje-chip/färg - ge dem rätt chips (axfood -> Willys/Hemköp, coop_nav -> Coop osv).
      Gör dessutom alla kolumner (kedja, råkategori, antal, kanonisk) filtrerbara/sorterbara
      (idag bara fritext-sök). Spegla ev. taggfliken.
  - [x] **Coop-kategoriförvärmning BYGGT** (`warm_coop_categories`). Coops offer-nivå
    (Färsk/Kolonial/Nonfood) är för grov och delvis felklassad (Nonfood innehöll
    grönsaker+kaffe). Förvärmar nu `product_info` per Coop-EAN via personalization-API:t
    (batchat, EAN-array). coop_nav-vokabulären rättad mot verkliga API-namn. Coop-täckning
    0% -> 70%; resterande ~30% är viktvaror (slump-EAN) utan produktdetalj.
  - [x] **Kategori- + deal_type-filter + sort i compare/favorit-vyer BYGGT.** Alla tre
    erbjudande-vyerna (enskild butik, prisjämför nära, favoriters erbjudanden) har nu samma
    kontroller: text/sort/kategori/deal. compare-produkter bär kanonisk category-nyckel.
  - [ ] Frukt/grönt-viktvaror hos Coop får `ovrigt` (ingen produktdetalj på slump-EAN);
    ev. namn-heuristik eller annan Coop-signal senare.
  - [ ] ICA finare kategorier (offer har bara `articleGroupId` 1-9; ehandel WAF-skyddad).

---

## Steg 4 - Prishistorik (SENARE)

Tidsserie (`offer_observations`) per produkt/EAN för prisutveckling. Endast
meningsfull för nivå-2-matchade märkesvaror. ToS/juridik känsligare vid nationell
aggregering - stäm av innan skarp drift.
