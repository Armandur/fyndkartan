# ROADMAP - matbutiker

Status och plan. Uppdaterad 2026-05-31.

Slutmål: app som visar aktuella erbjudanden från butiker nära användaren, med
favoritbutiker och - där datan tillåter - jämförelse av samma/liknande produkter
mellan butiker, samt prisutveckling över tid.

---

## Steg 1 - Butiker (KLART)

Unified store-API för 6 kedjor (ICA, Coop, Willys, Hemköp, Lidl, City Gross), ~2722 butiker, Leaflet/OSM-karta. Spec i
`UNIFIED-API.md`. Självförnyande nycklar (ICA token-API, Coop/Lidl scrape-on-401).
Normaliserade veckoöppettider (`opening_hours.week`/`exceptions`) för alla kedjor.

**Att göra (butikslivscykel):**
- [ ] **Rensa/flagga stängda butiker.** En butik vars `link_offers` ger ihållande 404 i sweepen
  (t.ex. ICA Nära Torgboden Falsterbo) är sannolikt stängd/omdöpt, men erbjudande-404 ENSAMT får
  inte avgöra borttagning (en öppen butik kan sakna erbjudanden). Behöver fler signaler: butiken
  saknas i nästa butikssynks lista, koordinater/öppettider borta, e.d. -> markera `closed` (mjuk)
  och först därefter ev. rensa. Undviker att döda butiker felar varje sweep utan att råka kasta
  levande butiker utan erbjudanden.

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
- [x] **Bulk-förhämtning av erbjudanden BYGGT (`sweep_offers` + `POST /v1/offers/sweep`).**
      Proaktiv motsats till lazy-hämtningen: sveper alla offer-stödda butiker och hämtar de som
      inte är färska (`_offers_fresh`, redan valid_to-medveten -> efter kall fyllning refetchas
      bara utgångna). Per kedja bunden parallellism (`OFFERS_SWEEP_CONCURRENCY`) + paus
      (`OFFERS_SWEEP_PACE`) + exponentiell back-off/retry per butik + circuit breaker
      (`OFFERS_SWEEP_CIRCUIT` fel i rad -> pausa kedjan). Egen cadence `OFFERS_SWEEP_CRON`
      (default varje timme, billig då färska hoppas); INGEN kall sweep vid uppstart (skonar
      kedjorna vid omstart). Schemaläggaren generaliserad (`run_scheduler(cron, tz, job, label)`).
      Arkiverar prishistorik via `replace_store_offers`. Konsolens Översikt: "Hämta alla
      erbjudanden"-knapp (+tvinga) + per-kedje-tabell med nuvarande täckning (butiker med cachade
      erbjudanden) och senaste sweep-räknare (`offers_coverage` + `SWEEP_STATE`) inkl. fel-detaljer
      per kedja (`last_errors`). Låser upp kartfilter på produkt + full produktsök (lazy-cachen
      täckte bara öppnade butiker). **EAN/kategori-förvärmning hängd på** (`warm_after_sweep`):
      efter en sweep warmas Axfood-EAN ur de NYSS cachade koderna (`axfood_offer_codes` ->
      `warm_axfood_eans_cached`, komplett kodmängd inkl. regionala koder samplingen missar) +
      Coop/ICA-kategori - stänger luckan att sweep-koder annars väntar på nästa butikssynks warm.
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
    - [x] **ICA native detalj BYGGT (ingredienser/näring/allergener/ursprung/kategori).**
      WAF-myten avfärdad: `handla.ica.se/produkt/{consumerItemId}` är SSR och nås med vanlig
      httpx + browser-headers (`Sec-Fetch-*`); curl-blocket berodde på header-löshet. ICA är nu
      tredje källa i `details.py` (`_fetch_ica` + `_parse_ica_detail`): EAN->consumerItemId via
      globalsearch (butiks-scopat -> resolvern provar flera profiler, `database.ica_resolve_accounts`;
      EAN nollpaddas till 14 siffror), cid cachad i `ica_item_map` (cid='' = försökt utan träff).
      Detaljsidans microdata + sektioner parsas (näring i två varianter: `<table>` + komma-`<p>`).
      Hämtas för ICA:s egna märken (prefix 731869, som Axfood/Coop saknar) + som sista fallback.
      Finare ICA-kategori via breadcrumb-topp (`category_from_detail` source "ica" + `ica_nav`-
      mappning i `DEFAULT_CATEGORY_MAP`). Stänger luckan för 234 ICA-egna-märkes-EAN i offers-
      cachen (av 2185 utan product_info). Verifierat e2e: egna märken + branded utan regression.
    - [x] **ICA-bilder i bild-resolvern BYGGT.** `_parse_ica_detail` plockar `og:image` (resizebar
      cloudinary `/image/upload/`) -> `image` i product_info; `images._resolve_url` föredrar den
      för ICA-egna-märkes-EAN utan offer-bild, före ICA:s EAN-CDN. Kedjad cloudinary-transform
      verifierad (200 image/jpeg, mindre fil än originalet). `image` exponerat i `ProductInfoData`.
    - [x] **ICA-ursprung ur inline-markörer BYGGT.** `_ICA_ORIGIN_RX` täcker nu `*Ursprung X`,
      `*Ursprung: X` (kolon), `*Odlade/Producerad/Tillverkad/Framställd/Fångad/Fiskad/Skördad i X`;
      markörordet skiftlägesokänsligt, landet ett versalt ord. Ursprungsland-sektionen föredras.
    - [x] **ICA-kategori-förvärmning BYGGT.** `sync.warm_ica_categories` (cap 40/synk, concurrency 2):
      ICA-offer-EAN utan mappbar kategori, egna märken (731869) först (via `fetch_ica_only` - hoppar
      bortkastat Coop-anrop), branded via `fetch_for_ean`. Skip-filtret använder RÅ `product_info`-
      membership (`product_info_eans`) så utgångna negativa inte re-warmas och äter capen; lazy
      route:n sköter säsongs-retry via TTL. Körs i `sync_and_warm` + vid uppstart. Inkrementell
      fyllning över många synkar (~2000 EAN / 40), inte ett momentant kategorilyft.
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
  - [ ] **Fulla sortiment** (ej bara offers) - eget hämtnings-/lagringsprojekt. **Detaljerad,
    resumerbar implementationsplan: se "Steg 5 - Fulla sortiment" sist i detta dokument.**
  - [ ] **Butiksmedveten Coop/ICA-prissättning (hyllpris är butiksspecifikt).** Empiriskt bekräftat:
    Coop (`store`-ledger) och ICA (`accountNumber`) returnerar olika pris OCH sortiment per butik
    (se Kända datakälle-fakta). Vi crawlar idag en FAST butik per kedja (Coop 251300, ICA 1003647) -
    katalogradens hyllpris är den butikens, inte nationellt. Halvbyggt: `catalog_products.store`
    taggar raden med ledger:t (NULL = nationellt, Axfood/CG); Coop/ICA backfillade. **Kvar:**
    (1) [x] BYGGT - `store` exponeras i `catalog_browse` (CatalogPrice.store) + en "*"-markör (tooltip)
    på Coop/ICA-hyllpris i katalogkortet så det inte läses som nationellt; (2) ev. crawla flera
    representativa butiker per kedja (per region) för en
    rättvisare cross-chain-jämförelse; (3) store-medveten produktinfo/bild för Coop (perso-fetch
    scopas till 251300 -> produkter som bara finns i andra butiker saknas info/bild). Stort jobb -
    gör (1) först (billig honesty-markör), (2)/(3) vid behov.
  - [ ] **Spåra ALLA butikspriser för Coop + ICA (geografisk prisintelligens).** Stort, eget projekt -
    "var är X billigast nära mig" + regionala prisskillnader. Kärnutmaning = skala: ICA ~1300
    självständigt ägda butiker (potentiellt per-butik-pris -> ~39M pris-punkter), Coop ~800 butiker
    ägda av ~30 regionala föreningar (`ownerName` i native -> TROLIGEN priszoner per förening). Plus:
    alla butiks-ledgers svarar inte i perso-söket (ej e-handelsindexerade).
    - **Steg 0 - research GJORD (2026-06-04), nedslående för zon-genvägen:**
      - **Coop:** 722 butiker / 27 föreningar. **Bara ~43% av ledgers är frågbara** (bred sökning
        "mjölk" gav träffar i 13/30; resten 0 även på vanlig vara -> ej e-handelsindexerade; `storeId`
        funkar ej, `ledger` är rätt param). **Zoner är INKONSEKVENTA:** Coop Nord lika pris inom
        föreningen (231400=231500), men Coop Östra ALLA 10 gemensamma OLIKA inom föreningen -> ingen
        säker "en butik/förening"-förenkling. Per-butik-Coop = crawla de ~310 frågbara (av 722).
      - **ICA:** 1289 butiker, **alla har accountNumber och ALLA svarar** (100% queryable via API-
        gatewayen, 20/20 i urval). Självständigt ägda -> per-butik-pris (bekräftat), ingen förenings-
        struktur. Per-butik-ICA = crawla alla 1289 (fullt frågbara men störst skala).
      - **Metod-läxa:** queryability MÅSTE testas med bred SÖKNING, inte by-id på fasta EAN - butikens
        sortiment skiljer sig, så 0 träffar på 5 EAN betyder "saknar de varorna", inte "ofrågbar".
      - **Slutsats:** ingen billig zon-genväg finns (Coop-zoner inkonsekventa, ICA saknar zoner). Full
        per-butik = ~310 Coop + 1289 ICA staggrat över ~en månad. Hanterbart men inte trivialt.
    - **Detaljerad, resumerbar implementationsplan (datamodell, efterfrågestyrd crawl, nya endpoints,
      kart-app-funktioner, SQLite-vs-Postgres): se "Steg 6 - Per-butik-priser" sist i dokumentet.**
    - **Inramning:** för enkel cross-chain-jämförelse räcker EN representativ butik/kedja (det vi har).
      Full per-butik = geografiskt prisintelligens-projekt, värt det bara om det blir en uttalad produkt.
  - [x] **Unified produktsök (API) BYGGT (`api/catalog.py` + `GET /v1/products/catalog?q=`).**
    Live fan-out mot kedjornas NATIVA sök-API:er -> **hela sortimentet, nationellt/representativt
    hyllpris** (ej butikslokalt, ej offers - en upptäckts-funktion skild från `/v1/products/search`).
    Per kedja en `_search_<chain>` -> normaliserad form, grupperat på EAN cross-chain (`CatalogProduct`
    med per-kedje-`prices`). City Gross (Loop54 search/quick), Coop (perso-search, återanvänder
    `_parse_coop_item`), ICA (globalsearch, flaggskepps-accountNumber), Willys/Hemköp (`/search`,
    EAN via `ean_cache` -> okända katalog-koder blir fristående). Lidl saknas (ingen EAN). Per-kedja
    timeout -> delresultat om en fallerar. Honest schema: inga deal_type/offer_count (hyllpris, ej
    deals). Katalog-kategorivokabulärer (CG superCategory, ICA mainCategoryName) seedade i
    `DEFAULT_CATEGORY_MAP`. **Bara API (v1)** - ingen frontend än (medvetet val).
    Per-query-cache (in-process, 90s TTL, cachar full lista + limit-slicar) skyddar typeahead.
    Axfood-EAN-resolve: koder utan EAN i `ean_cache` resolvas via `/p/{code}` (capat `AXFOOD_RESOLVE_CAP`
    /kedja+sök, ger även kategori, persisteras -> warmar) - lyfte cross-chain-träffarna kraftigt
    (t.ex. "yoghurt" 4-kedjes-matchningar med alla hyllpriser).
    - [x] **Produktkort-layouten översedd (`catalogCard`/`offerCard`/`productCard`).** Riktning
      "städad horisontell": större bild (56px), namn klippt till 2 rader (jämn topphöjd), tydligare
      rea-vs-hyllpris (hyllpris neutralt via `o-price--shelf`, rött reserverat för faktisk rea),
      meta på en rad i rutnätet. Kompakt-vs-detaljerat-läge medvetet bortvalt (horisontell räckte).
    - [x] **Svensk tusentalsavgränsning (visuellt) BYGGT.** Delad `fmtNum` (`toLocaleString("sv-SE")`)
      i app.js + admin.js, applicerad på de stora antalen (bläddra-vyns summary/kategori-chips/titel/
      progress, kart-vyns butiksantal, konsolens stat-kort + Per kedja-tabell). Priser via `kr()` (små,
      decimaler viktigare än tusental) lämnade. Tidigare:
      tusentalsavgränsare i visade tal (12345 -> 12 345) i både konsument-appen och konsolen - rent
      presentationslager (`toLocaleString("sv-SE")` eller en delad `fmtNum`-hjälpare), aldrig på
      lagrade/skickade värden. Redan använt på ett par ställen i konsolen (crawl-feeden); gör det
      konsekvent (stat-kort, antal, prishistorik-räknare m.m.). Akta priser/decimaler (kr) så
      avgränsaren inte krockar med `kr()`-formateringen.
    - [x] **Sortering i bläddra-vyn BYGGT.** `catalog_browse(sort=price|spread|name)` sorterar
      server-side FÖRE paginering (korrekt med infinite scroll/total) + `browseSort`-dropdown.
      `spread` = största hyllpris-skillnaden mellan kedjor (katalogens analog till "störst besparing",
      gratis ur befintlig data). Filter klart sedan tidigare: kategori, kedja, bara erbjudanden.
      - [x] **Offer-baserad besparings-sort + deal-typ-filter BYGGT.** `sort=savings` (störst
        hyllpris-rea över kedjorna) + `deal=multibuy|by_weight|flat` i `catalog_browse` -
        offer-enrichment av hela kategorin via `offers_for_eans` (nu chunkat för SQLite-vargränsen),
        besparing/deal-typ per produkt, begränsar till rea-produkter, server-side före paginering.
        Dropdowns i bläddra-vyn ("Störst besparing", deal-typ-filter).
    - [x] **Infinite scroll (append, inte ladda om).** Bläddra-vyn appendar nästa sida via
      offset-paginering (`catalog_browse` offset/limit) när man skrollar nära botten
      (IntersectionObserver på `#browseMore` i `#browseView`, rootMargin 400px) - behåller
      scrollpositionen i stället för den gamla "Visa fler"-knappen som laddade om allt. Staggad
      fade-in på appendade kort + bild-fade-in (onload) i rutnätet.
    - [x] **"Saknar EAN"-antal i konsolens översikt BYGGT.** `catalog_stats` returnerar `missing_ean`
      per kedja (`available=1 AND ean IS NULL/''`); Per kedja-tabellen har en "Saknar EAN"-kolumn
      (röd om > 0) + totalrad. Visar hur mycket EAN-resolvningen har kvar / cross-chain-täckningen.
    - [x] **Bugg: "Laddar fler…" när allt laddats FIXAD.** `browseLoadingMore` var fortfarande `true`
      när `renderBrowseGrid()`/`renderBrowseProgress()` kördes på första sidan (sattes `false` först i
      `finally`, efter render). Nu sätts den `false` före render -> visar "Alla N produkter visade".
    - [x] **Näringsinnehåll i produktinfo som tabell BYGGT.** Produktmodalen renderar näringen som en
      tabell (näringsämne | mängd) med basis ("per 100 g") i rubriken; energi-rader (kJ + kcal) slås
      ihop i appen (API:t behåller dem separata). Värde/enhet med mellanslag, EAN visas i modalen.
    - [ ] **UI för produktinnehålls-historik (recept-/närings-/ursprungsändringar).** Fångst-lagret är
      byggt: `product_info_observations` (per `(ean, source)`, append-on-change via
      `database.archive_product_info`, matas ur crawl/warm-piggyback + on-demand `fetch_for_ean`).
      Saknas: läs-endpoint (`GET /v1/products/{ean}/info-history`) + produktmodal-vy som visar
      "receptet ändrades 2026-03: socker tillkom" / närings-diff / bytt ursprungsland. **Bygg först när
      historik ackumulerats** (kan inte backfillas) och utvärdera då i verklig data hur mycket som är
      riktiga ändringar vs källbrus (olika ingredienssträngar per hämtning) innan presentation byggs.
      Diffa per källa, inte mot den mergade raden.
    - [ ] **Normalisera tillverkarnamn (förarbete till tillverkar-filtret).** Samma märke stavas
      olika per kedja ("Arla", "Arla Foods", "ARLA AB"...) och `manufacturer`/`brand` kommer rått från
      respektive kedjas API. Innan ett tillverkar-filter blir användbart behövs en kanonisk mappning
      (derive-at-read likt `category_map`/`tag_map`: råname -> kanoniskt namn, redigerbar admin-flik).
      Annars splittras en tillverkares produkter på flera stavningar. Detta är blockern - gör det först.
    - [ ] **Lista/filtrera produkter per tillverkare (API + framtida app).** Man ska kunna lista
      alla produkter från en viss tillverkare/märke (`brand`/`manufacturer`-fältet finns redan i
      katalogen + offers). Främst som API (t.ex. `?manufacturer=` på catalog-browse + en
      tillverkar-katalog/aggregat), kanske inte i nuvarande kart-app men i en kommande konsument-/
      analys-app. **Beror på tillverkarnamn-normaliseringen ovan** - utan den splittras märket på
      olika stavningar. En råname-exakt v1 går att göra tidigare men ger begränsat värde.
    - [ ] **Kost-filter: vegan/vegetariskt (+ härled när otaggat).** Kunna filtrera produkter på
      vegansk/vegetarisk - som ett TVÄRGÅENDE kost-filter (vegetariska varor finns i alla kategorier,
      inte bara kanoniska `vegetariskt`). Steg: (1) använd kedjornas ev. taggar/kategori när de finns;
      (2) **härled annars ur produktinfo** (`details.py` ingredienser/allergener: ingen kött/fisk/
      mejeri/ägg/gelatin -> vegansk; ingen kött/fisk -> vegetarisk) - vokabulär likt
      `extract_allergens`; (3) bara LIVSMEDEL (exkludera hygien/hushåll/djur via kanonisk kategori).
      Osäkra fall markeras "ev." snarare än falskt positivt. API-flagga + filter i bläddra-vyn.
      **Två nivåer: vegan ⊂ vegetariskt** (allt veganskt är vegetariskt) - antingen två kanoniska
      kategorier/taggar eller ett diet-fält med nivåerna `vegan`/`vegetarian`, så att vegetariskt-
      filtret även inkluderar de veganska.
    - [x] **Filtrera bläddra-vyn på "rea hos favoriter" BYGGT.** Toggle "★ Rea hos favoriter"
      (login-only) -> visar bara produkter som har ett ERBJUDANDE hos användarens specifika
      favoritbutiker (per-butik-exakt via `eans_on_offer_at_stores`, chunkat). Favoriterna hämtas
      server-side ur inloggad användares `list_favorites` (ej från klient). Vald tolkning: "på rea
      hos favoriterna" (katalogen är nationell -> per-butik-sortiment finns ej; per-kedje-varianten
      valdes bort).
    - [ ] **Zooma kartan till favoriternas bbox vid favorit-vyer.** När man väljer "mina butikers
      erbjudanden" / "jämför mina favoriter" i kartappen borde kartan zooma till en ruta som rymmer
      användarens favoritbutiker (i st.f. att stå kvar på nuvarande vy). Återanvänd `fitToVisible`-
      mönstret men begränsa till favoritbutikernas koordinater (`map.fitBounds(favPunkter, padding)`).
    - [x] **Aktuellt erbjudande i produktmodalen + erbjudandepris på kartan BYGGT.** En kedjas
      **rea-rad i katalogkortet är klickbar** -> öppnar produktmodalen med en "Aktuellt erbjudande"-
      sektion som visar erbjudandets EGNA namn/pristext/förpackning/deal-typ per kedja
      (`stores_with_offer` utökad med `price_text`/`package`/`deal_type`). Det avslöjar det "ologiska
      rea-fallet": rean hör ofta till ett FLERKÖP ("3 för 18 kr") eller annan förpackning som delar
      EAN med hyllvaran. Kartans butiks-popup visar också erbjudandepriset för den filtrerade produkten
      (`state.productFilter` bär per-butik-offers), och produktfilter-baren visar prisintervall.
      - [x] **Visa flerköp/pristext på kortet (inte bara "rea 18 kr") BYGGT.** Kortets rea visar nu
        beräknat styckpris ("X kr/st") + en liten deal-text ("N för Y kr") vid flerköp, så det inte
        ser ut som ett missvisande styckpris. Samma styckpris + pristext i erbjudande-modalen, kartans
        pris-chip och produktfilter-toasten (`offers_for_eans`/`stores_with_offer` bär `price_text`/
        `multibuy_qty`/`deal_type`).
      - [ ] (sido-spår) bör olika förpackningsstorlekar grupperas på samma EAN i katalogen?
    - [x] **Frontend-läge BYGGT.** Produktpanelen har en lägesväxel: "Erbjudanden" (offers-cachen,
      snabb) vs "Hela sortimentet" (katalog-fan-out). Katalog-läget visar `catalogCard` med
      nationellt HYLLPRIS per kedja (ingen deal-badge/offer_count; beräknat jämförpris märkt ≈),
      längre debounce (450ms) + race-guard (`productsToken`) så ett segt fan-out-svar för en äldre
      söksträng inte skriver över en nyare. Kategori-dropdownen döljs i katalog-läget (ingen
      by-category där). "Visa information" öppnar samma produktmodal (info + prishistorik).
      - [x] **Aktuella erbjudanden överlagrade på katalogen BYGGT.** `catalog._enrich_with_offers`
        slår upp `database.offers_for_eans` (bästa aktuella erbjudandepris per EAN+kedja ur offers-
        cachen, inline + Axfood-kod reverse-resolvat) och överlagrar på hyllpriserna: per kedja
        `offer_price`/`offer_valid_to`/`offer_member` (CatalogPrice), `on_offer`/`offer_min`
        (CatalogProduct). Kortet stryker hyllpriset och visar "rea X kr" + "På erbjudande fr. X kr"-
        badge; kedjor med erbjudande men utan hyllpris-rad får en egen rad. Hyllpris = nationellt,
        erbjudandepris = lägsta butikslokala i kedjan.
      - [ ] **Bunta ihop matchade private-label-varor i katalogen.** Katalogen grupperar på EAN,
        men egna märken delar aldrig EAN (ICA Krossade Tomater vs Coop Änglamark) -> separata kort.
        `product_matches` (manuell paring, idag ~18 grupper) länkar dem; ett andra grupperingspass
        (likt `build_comparisons` `manual_groups`) skulle slå ihop dem till ett kort med hyllpris
        per kedjas egna märke - just det cross-chain-jämförelsefall EAN-gruppering missar. **Haken:**
        kort-UI:t (info-modal, prishistorik, "Visa på karta") nyckar på EN EAN; en hopbuntad post har
        flera (en per kedja), så de actionerna måste göras per kedja/representativ - det är jobbet,
        inte hopslagningen. Bygg när private-label-täckningen vuxit eller egna-märkes-hyllprisjämförelse
        blir en uttalad prioritet. (Matchade private labels buntas redan i erbjudande-jämförelsen.)
  - [x] **Dokumentera alla kedjors produktsök-/katalog-API:er** - endpoint, params,
    EAN/pris/jämförpris-tillgång (för unified-söket). Alla kedjor kartlagda (City Gross, Coop,
    ICA, Axfood nedan + i "Kända datakälle-fakta"; Lidl auth-gatat -> SSR-skrap utan EAN):
    - **City Gross** (Loop54): `GET .../Loop54/search/quick?SearchQuery=` (+ `products/{id}`,
      `category/{id}/products`). EAN (`gtin`) + pris + jämförpris inline.
    - **Coop** (personalization): `POST external.api.coop.se/personalization/search/global`
      `?api-version=v1&store={ledger}&groups=CUSTOMER_PRIVATE&direct=true`, header perso-nyckel
      (skrapas), body `{query, resultsOptions:{skip,take}}` -> `results.items[]` (`count` total).
      Varje item = samma entitet som `entities/by-id` (vi parsar redan i `_parse_coop_item`):
      `ean`, `name`, `manufacturerName`, `salesPriceData.b2cPrice` (hyllpris), `comparativePriceData`
      + `comparativePriceUnit`, `packageSize`, `navCategories`, `listOfIngredients`/`nutrientLinks`,
      `imageUrl`. EAN + jämförpris inline. (Vi använder `b2cPrice`; `b2bPrice` ignoreras.)
    - **ICA** (BEKRÄFTAT nåbart server-side): `POST apimgw-pub.ica.se/sverige/digx/globalsearch/
      v1/search/quicksearch` med public-access-token (Bearer, vi hämtar redan) + `accountNumber`.
      `products.documents[]`: `gtin`, `displayName`, `price` (sträng), `image` (resizebar
      cloudinary), `mainCategoryName`. INGET jämförpris. Via API-gatewayen, inte WAF-blockade
      ehandeln -> ICA:s katalog är sökbar (bara produktDETALJEN är WAF-skyddad).
    - **Axfood** (Willys/Hemköp): fullkatalog-sök `GET {willys|hemkop}.se/search?q=&page=&size=`
      (ingen auth) -> `results[]` + `pagination.totalNumberOfResults`. Item: `code` (Axfood-
      artikelkod, EAN resolvas via `ean_cache`/`/p/{code}` som offers), `name`, `manufacturer`,
      `priceValue`, `comparePrice`+`comparePriceUnit` (jämförpris), `googleAnalyticsCategory`,
      `image` (axfood cloudinary). EAN EJ inline (enda kedjan som kräver resolve i sök).
    - **Lidl**: `/q/api/search`-API:t är auth-gatat (401; host `<stage>.lidl.de` löses bara
      klient-sidan, token ej i sidan/widgeten) - och obscura kan inte köra Lidls Nuxt-SPA
      (för lättviktig JS-motor, saknar `createContextualFragment`/`dataset`). MEN sök-sidan är
      **server-renderad**: `GET www.lidl.se/q/search?q=<term>` (vanlig GET, ingen auth/JS) bär
      hela produkt-JSON:en per kort i `data-grid-data="{...}"` -> `fullTitle`, `itemId`/`erpNumber`,
      `price.basePrice.text` (jämförpris inline), `image`, `category` ("Food" filtrerar non-food-
      brus), `canonicalPath`. **Ingen EAN** - bara `ians` (Lidls interna artikelnr) -> Lidl kan
      inte cross-matchas på EAN. Söket är luddigt (mjölk -> klädesplagg) och SSR ger bara
      första sidan (~5-6 träffar; fulla 48 kräver API:t). Dugligt för listning, ej för compare.
    - **ICA produktdetalj BYGGT** (se "ICA native detalj BYGGT" ovan för integrationen).
      `GET handla.ica.se/produkt/{consumerItemId}` är **server-renderad (Astro)** och nås med
      vanlig httpx OM man skickar browser-headers (`User-Agent` + `Sec-Fetch-Dest/Mode/Site/User`,
      `Upgrade-Insecure-Requests`); AWS-WAF:en blockerar bara header-lösa anrop (curl), inte ett
      riktigt browser-headerset. INGET butiksval krävs - full info ligger i SSR-microdatan:
      `itemprop="sku"/"mpn"` = EAN, `productId` = consumerItemId, `name`, `categories` (full
      breadcrumb), description, samt klartext-block för Ingredienser, Allergener, full
      Näringsdeklaration (kcal/kJ/fett/...) och Ursprungsland. **EAN -> consumerItemId** fås ur
      ICA-söket (quicksearch returnerar `gtin` + `consumerItemId` per item, EAN nollpaddad till
      14). Stänger ICA:s detalj-lucka för ALLA produkter inkl. ICA:s egna märken - obscura behövs
      ej (bypassar WAF:en men httpx med rätt headers gör samma sak billigare). Söket är butiks-
      scopat -> resolvern provar flera butiksprofiler (Maxi/Kvantum/Supermarket/Nära).
    - **Slutsats:** alla kedjor är sökbara med pris. EAN inline för City Gross (`gtin`), Coop
      (`ean`), ICA (`gtin`); Axfood kräver code->EAN-resolve; **Lidl saknar EAN helt** (SSR-skrap,
      bara internt artikelnr). Jämförpris: alla utom ICA. Unified produktsök är genomförbart för
      hela sortimentet - de fem EAN-bärande kedjorna fullt ut, Lidl som EAN-lös listning.
  - [x] **Smart auto-förslag: semantiska embeddings BYGGT.** Märkesvaru-paringsförslagen
    (`brands.rank_candidates`) rankar nu på semantisk namn-likhet via static-embeddings
    (`embeddings.py`, model2vec multilingual, CPU/numpy - ingen torch, lazy-laddad, degraderar
    tyst till lexikal `score` om modellen ej kan laddas). Namnen rensas före embedding (bort med
    cert-/eko-markörer, storlek, %, märke; smak behålls) så delade modifierare inte dominerar
    korta namn. Cosine-grind `_SEM_FLOOR` + förpacknings-bonus. Fångar synonymer/omkastningar som
    token-överlapp missar ("Krossade Tomater" ~ "Tomatkross") och slipper eko-falskmatchningar.
    - [ ] (framtid) **LLM som domare** ovanpå embeddings-kandidaterna - låt en LLM avgöra de
      osäkra paren (embeddings rankar/grovsållar, LLM bekräftar). Ej byggt.
  - [x] **Förhandsmatcha märkesvaror ur katalogen BYGGT.** Märkesvaror kan paras innan de har ett
    erbjudande: huvud-produktsöket tar med private-label-varor ur kedjornas fulla katalog
    (`GET /v1/admin/catalog-private` + `_is_private_catalog`; brand-rot ELLER rot som helt ord i
    namnet för ICA som saknar brand-fält), katalog-only får chippet "inget erbjudande". Paring som
    vanligt i `product_matches` (EAN-nyckel + snapshot) -> tänds automatiskt när ett erbjudande dyker
    upp; `list_matches.active` skiljer Aktiva från "Väntar på erbjudande". EAN-kanonisering
    (GTIN-14->13) gör att ICA matchar; ICA-storlek + härlett jämförpris ur namnet. Källans kedja
    exkluderas ur kandidaterna. (Lidl saknar EAN -> utesluts.)
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
  - [x] **Samlad Inställningar-flik i konsolen (override env) BYGGT.** Fliken samlar `SYNC_CRON`,
    `OFFERS_SWEEP_CRON`, `CATALOG_CRAWL_CRON` + `SYNC_TZ`. `api/settings.py` resolvar DB-override
    (`settings`-tabellen, `cfg_<key>`) > env > kod-default vid läsning. `run_scheduler` tar nu cron/tz
    som callable och läser om varje varv (chunkad sömn `SCHEDULER_CHECK=30s`) -> **ändringar slår
    igenom utan omstart** (verifierat). Validering: cron/`croniter`, tz/`zoneinfo`; `off`/tomt pausar.
    UI: dropdown med förval + fri sträng (synkade, "Anpassad" utanför förval) + live nästa-körning-
    förhandsvisning + override/env-default-badge + "Återställ env". Kvar (medvetet ej byggt):
    tidpunkts-väljare ("dagligen kl X") som egen kontroll - fri sträng täcker det; `_offers_expired`-
    tz läser fortfarande env (hot path, tz ändras sällan).

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

- [ ] **Statistik-/analys-app ovanpå datan (egen konsument).** Med fullsortiment-katalogen
  (nationellt hyllpris per kedja), offers (butikslokalt), prishistoriken (`catalog_price_observations`
  + `offer_observations`), butikernas geo (lat/lng, ort) och cross-chain-EAN finns underlag för en
  read-only analys-app/dashboard. Lämpligen schemalagda aggregat -> summary-tabeller (tungt att
  räkna live), egen frontend (REST redan ren) eller en analys-flik. Frågor att kunna besvara:
  - **Vilka varor/kategorier är olika kedjor dyrare/billigare på** (per EAN + aggregerat per
    kanonisk kategori): prisindex per kedja totalt och per kategori, "kedja X dyrast på mejeri,
    billigast på skafferi".
  - **Standard-varukorg (à la PRO:s matprisundersökning):** en fast, representativ korg av
    produkter (PRO/SCB-liknande metodik) prissatt över alla kedjor och **över tid** som ett
    jämförelse-/indexvärde -> vilken kedja är billigast på korgen (nationellt vs butikslokalt),
    och hur korgens totalpris utvecklas (matpris-index). Korgen bör vara kurerbar (admin väljer
    EAN:er) och hantera att en vara saknas hos en kedja (substitut eller exkludering).
  - **Prisutveckling över tid** ur observationerna: inflation/säsong per kategori/kedja, störst
    prisökningar, "shrinkflation" (jämförpris upp men pris stilla).
  - **Erbjudande-intensitet per kedja:** rea-frekvens, snittbesparing, djup; vem rear mest/djupast.
  - **Private label vs branded prisgap**; **medlemspris-effekt** (klubbpris-rabatt per kedja);
    **ursprung/svenskandel** per kedja/kategori (vi har origin); **jämförpris-anomalier** (samma EAN,
    stor jämförpris-skillnad mellan kedjor).
  - **Sortimentsbredd/täckning** per kedja per kategori (vem har bredast utbud, vilka luckor).
  - **Geografiska skillnader + urbanitets-lager (din idé):** butikslokala offers vs nationellt
    hyllpris -> var avviker priser geografiskt. Kräver en **urbanitets-klassning av butiker**
    (storstad / stad / landsbygd) - data-enrichment: SCB:s tätorts-/landsbygdsindelning eller
    kommuntyp, alternativt härledd ur ortens befolkning / butikstäthet. Då kan man fråga "är
    landsbygdsbutiker dyrare?", prisspridning per region/län, kedjornas geografiska täckning.
  - GDPR: enbart butiks-/produktdata, inga personuppgifter -> okomplicerat. Tunga aggregat bör
    cachas (samma mönster som katalog-grupperingscachen) eller materialiseras vid crawl/sweep.

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
    "Visa information"-knapp + "N butiker". Ej webbläsartestad.
    - [x] **Knappen "Innehåll & näring" -> "Visa information"** (offer-/produkt-/katalog-kort).
      Modalen visar nu mer än bara innehåll/näring (prishistorik, ev. mer framåt), så namnet
      generaliserades. Ren textändring.
  - [x] **Filtrera kartan på en vald produkt BYGGT** ("Visa på karta" på produkt-/katalog-kort).
    Upplåst av bulk-sweepen (full offers-täckning - tidigare blockerat av gles lazy-cache).
    `GET /v1/products/{ean}/stores` (`database.stores_with_offer`, json_each-match inline +
    Axfood-kod reverse-resolvat ur ean_cache, billigaste erbjudandet per butik) -> frontend
    sätter `state.productFilter` (Set av `chain:store_id`), `visibleStores` filtrerar markörerna,
    flytande banner överst på kartan visar varan + antal + rensa-knapp, kartan zoomar till träffen.
    **Ärlig semantik:** "butiker med ERBJUDANDE på varan" (offers-cachen), inte hyllsortiment.
    Latens ~350ms/klick på 382k offers-rader (acceptabelt; ingen indexering än).
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
- [x] **Schemalagt bulk-förhämtningsjobb BYGGT** (se `sweep_offers` under Steg 2) - sveper alla
  offer-stödda butiker rate-limitat, fyller offers-cachen proaktivt i stället för bara lazy.
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
    - [x] **Kategori-flikens (#cats) tabell: bättre kedje-chips + filter/sortering BYGGT.**
      `chain_key` visas nu som färgkodat kedje-chip (axfood -> Willys-färg, coop/coop_nav ->
      Coop osv). Källfilter-dropdown + "Bara omappade"-växel + sorterbara kolumner (kedja/
      råkategori/antal/kanonisk) med pilindikatorer och antalsräknare; filter överlever sort.
  - [x] **Coop-kategoriförvärmning BYGGT** (`warm_coop_categories`). Coops offer-nivå
    (Färsk/Kolonial/Nonfood) är för grov och delvis felklassad (Nonfood innehöll
    grönsaker+kaffe). Förvärmar nu `product_info` per Coop-EAN via personalization-API:t
    (batchat, EAN-array). coop_nav-vokabulären rättad mot verkliga API-namn. Coop-täckning
    0% -> 70%; resterande ~30% är viktvaror (slump-EAN) utan produktdetalj.
  - [x] **Kategori- + deal_type-filter + sort i compare/favorit-vyer BYGGT.** Alla tre
    erbjudande-vyerna (enskild butik, prisjämför nära, favoriters erbjudanden) har nu samma
    kontroller: text/sort/kategori/deal. compare-produkter bär kanonisk category-nyckel.
  - [x] **Frukt/grönt-viktvaror hos Coop -> `frukt_gront` BYGGT.** Slump-EAN saknar
    produktdetalj och föll till `ovrigt`. `categories.category_from_name` mappar tydliga
    färskvaru-termer (helord) -> frukt_gront, men BARA som fallback när kategorin annars
    blir ovrigt (varor med egen kategori orörda). Inkopplad i get_store_offers + list_products.
  - [x] ICA finare kategorier LÖST via produktdetaljens breadcrumb-topp (`category_from_detail`
    source "ica" -> `ica_nav`-mappning); ehandeln var aldrig WAF-skyddad mot rätt headers.

---

## Steg 4 - Prishistorik (BYGGT)

> **Status:** `offer_observations` (append-on-change, per butik), `GET /v1/products/{ean}/history`
> och konsument-modalens inline-SVG prisgraf är BYGGT (se CLAUDE.md "Prishistorik (steg 4)").
> Dessutom: innehållshistorik (`product_info_observations`) + hyllpris-historik
> (`catalog_price_observations`) tillkomna. Texten nedan är den ursprungliga ramen.

Tidsserie (`offer_observations`) per produkt/EAN för prisutveckling. Endast
meningsfull för nivå-2-matchade märkesvaror. ToS/juridik känsligare vid nationell
aggregering - stäm av innan skarp drift.

**Grund som redan finns (medvetet bevarad):**
- **EAN-nycklad modell + persistent `ean_cache`** (code->EAN/kategori/ursprung, rensas aldrig)
  ger den stabila identiteten att spåra pris över tid och kedjor - särskilt för Axfood, vars
  offers saknar inline-EAN och måste resolvas via `ean_cache`. Att vi inte rensar den är alltså
  en förutsättning för historiken, inte slarv.
- **`product_matches`** (manuella paringar, EAN-nyckade, rensas aldrig automatiskt) länkar
  cross-chain-märkesvaror så historiken kan jämföra olika kedjors private labels över tid.
- `product_info` (TTL-uppdaterad) + `product_images` (permanent) ger namn/näring/bild att hänga
  historiken på.

**Vad som saknas / måste byggas:**
- [x] **Arkivering av offers BYGGT (#1).** `replace_store_offers` kallar nu `archive_offers`
  före DELETE+insert: varje observation skrivs append-only till `offer_observations`
  (chain, store_id, offer_id, ean, name, price, comparison_value/unit, savings, member_price,
  valid_to, observed_at), deduppat per (pris, jämförvärde, savings, valid_to) per offer_id ->
  bara faktiska prisändringar lagras. **Per butik** (medvetet: vi vill alltid kunna se
  avvikelser per butik). Ordinarie pris spåras via `savings` + `member_price`.
  `offer_observations_stats()` för konsolen.
- [x] **UI/grafer för prishistorik BYGGT** - `GET /v1/products/{ean}/history`
  (`database.price_history`, grupperad per kedja, Axfood via ean_cache reverse-resolve) + inline-SVG
  stegfunktion i konsument-appens produktmodal (lucka vid utgånget erbjudande, medlemspris som ring).
  Stats i konsolens Översikt. Kvar: ev. djupare vy (per butik, längre tidsspann) när datan vuxit.
- Avvägning kvarstår: per butik (nu, stort) vs aggregerat per kedja/nationellt (juridiskt
  känsligare) - stäm av ToS innan ev. nationell aggregering.

---

## Steg 5 - Fulla sortiment (KÄRNAN BYGGD)

> **Status:** crawl-maskineriet (`api/catalog_crawl.py`), `catalog_products`-tabellen, daglig cadence,
> `/v1/products/catalog/browse` + konsolens Sortiment-flik med live-feed och hyllpris-historik-logg
> är BYGGT och i drift. Plan-texten nedan är den ursprungliga designen (mestadels förverkligad);
> kvarvarande detaljer markeras i "Att göra"-punkterna på respektive ställe ovan.


Persista HELA produktkatalogen per kedja (allt de säljer, inte bara det som är på rea och
inte bara det någon råkat söka på), med nationellt hyllpris, i en beständig tabell. Skild
från: (a) **offers-cachen** = bara nedsatta varor, churnar; (b) **live katalog-söket**
(`catalog.py` + `/v1/products/catalog`) = hela sortimentet MEN efemärt/per-query. Steg 5 =
crawla + lagra allt periodiskt.

**Syfte/upplåser:** komplett produktlista + bläddring per kategori för ALLT; riktigt
hyllprisindex cross-chain (inte bara deals); "vilken KEDJA för varan" (ej per butik, se
nedan); fullständig produktsök (ej bara cachade offers); grund för hyllpris-historik.

### Datamodell (database.py + init_db ALTER-guards)
Ny tabell, en rad per (kedja, produkt) - EAN-gruppering vid LÄSNING (spegla `list_products`):
```
catalog_products
  chain TEXT, product_id TEXT          -- kedjans interna kod; PK (chain, product_id)
  ean TEXT                             -- normaliserad (matching.normalize_ean); NULL för Lidl
  name, brand, image, origin TEXT
  price REAL                           -- nationellt/representativt hyllpris
  comparison_value REAL, comparison_unit TEXT
  package_size TEXT, package_value REAL, package_unit TEXT
  category_raw TEXT                    -- kanonisk härleds vid läsning via category_map (derive-at-read)
  available INTEGER DEFAULT 1          -- 0 om ej sedd i senaste fullständiga crawl (utgången)
  first_seen, last_seen, fetched_at TEXT
  -- INDEX(ean), INDEX(chain, category_raw)
catalog_crawl_state                    -- per kedja: senaste kategori/offset, status, started/finished (resumebar)
```

### Crawl-strategi (NY modul `api/catalog_crawl.py`, återanvänd `catalog.py`-searcharna)
`catalog.py` har redan en `_search_<chain>` per kedja som normaliserar item-dicts. Steg 5 kör
dem i BLÄDDRA-ALLT-läge: enumerera kedjans KATEGORITRÄD, paginera produkter inom varje kategori,
upserta i `catalog_products`. Per kedja (endpoints dokumenterade i "Kända datakälle-fakta" ovan):
- **City Gross** (Loop54): `GET /api/v1/Loop54/category/{id}/products` - paginera hela kategoriträdet.
- **Coop** (perso-search): `personalization/search/global` med `navCategories`-filter + `resultsOptions.skip/take`.
- **Willys/Hemköp** (Axfood): `{domän}/search?q=&page=&size=` per kategori (`googleAnalyticsCategory`-trädet);
  EAN ej inline -> resolve via `ean_cache`/`/p/{code}` som idag (`axfood_offers.fetch_p_meta`, capat).
- **ICA** (globalsearch): `quicksearch` med `offset`/`take`, per `mainCategoryName`, flaggskepps-`accountNumber`
  + public-access-token (`ica_token`). EAN = `gtin` (nollpaddad 14 -> normalisera till 13).
- **Lidl**: UTESLUTS (ingen EAN i sök -> kan ej cross-matchas; SSR-skrap ger bara internt artikelnr).
- Kategoriträd: hämta en gång per kedja (de flesta har ett kategori-API; annars härled ur sökresultatens
  kategorifält). Spara enumererade kategorier i `catalog_crawl_state` för resumerbarhet.

**Genomförande-status (crawler):**
- [x] **City Gross BYGGT** (`_crawl_citygross`): kategoriträd via `GET /api/v1/Navigation` -> `data.tree`
  -> "Matvaror"-barn med `link.categoryPageId` (35 toppkategorier; vissa är kampanjer/säsong som
  överlappar departments -> dedup på `product_id` per körning). Paginera `Loop54/category/{id}/products`
  (`skip`/`take`, har `totalCount`/`totalPages`). Normalisering = `_cg_row` (samma item-shape som offers).
- [x] **ICA BYGGT** (`_crawl_ica`): INGET kategoriträd behövs - `queryString:"*"` (wildcard) + `offset`
  paginerar HELA katalogen (~19 938 produkter), `stats.totalHits` = total. Återanvänder `catalog._norm_ica`,
  `product_id`=gtin. (`""` ger 0; `*` är wildcarden.)
- [x] **Coop BYGGT** (`_crawl_coop`): perso-search är fritext-only, MEN `POST personalization/search/
  entities/by-attribute` med `{"attribute":{"name":"categoryIds","value":"<kod>"},"resultsOptions":{skip,take}}`
  browsar en hel kategori (verifierat via Claude Chrome: Mejeri & Ägg = 876 produkter = by-attribute kod 6262).
  Departement-rötterna (kod = navCategories-rot, tom `superCategories`; 19 st) harvestas ur produkternas
  `navCategories` via ~30 breda sökningar och cachas (`_COOP_ROOTS`). `_norm_coop` återanvänds; product_id=EAN.
- [x] **Axfood (Willys/Hemköp) BYGGT** (`_crawl_axfood`): kategoriträd `GET leftMenu/categorytree` (rot N00 ->
  19 avdelningar, slug i `url`), produktlista `GET /c/<slug>?page=&size=` (`results` + `pagination.numberOfPages`).
  Olika prefix per sajt (Willys `/axfood/rest/v1`, Hemköp bart) + eget träd/koder per sajt. EAN ej inline ->
  slås upp gratis ur `ean_cache` (NULL annars, fylls av `warm_axfood_eans` över tid). category_raw faller till
  avdelningens titel (`/c/` saknar googleAnalyticsCategory). Recon-vägen knäcktes via Claude Chrome.

### Cadence + rate-limiting (återanvänd run_scheduler + sweep-mönstret)
Mycket större än offers-sweepen (tusentals paginerade anrop/kedja). Därför:
- **Rullande/inkrementell:** crawla N kategorier per körning (cap, som `warm_ica_categories`), sprid över
  ett dygn. Egen `CATALOG_CRAWL_CRON` i config + `run_scheduler(cron, tz, crawl_job, "katalog-crawl")`.
- Samma skydd som `sweep_offers`: bunden parallellism (`CATALOG_CRAWL_CONCURRENCY`), paus mellan anrop,
  exponentiell back-off/retry, circuit breaker per kedja. Spegla `_sweep_chain`/`_sweep_one_store`.
- `last_seen` < senaste fullständiga crawl-runda -> sätt `available=0` (utgången vara behålls för historik).
- INGEN crawl vid uppstart (skonar kedjorna); trigga via konsol-knapp + schema, som sweepen.

### Läs-integration
- Läs-funktioner i `database.py` som speglar `list_products` (EAN-gruppering cross-chain, kanonisk kategori
  via `category_map`, brand/origin-split): `catalog_browse(category, chain, q, limit)` + ev. `catalog_product(ean)`.
- `/v1/products/search` + `by-category` kan läsa ur `catalog_products` (eller union med offers) -> söket
  hittar ALLT, inte bara cachade offers. Overlay aktuella erbjudanden via befintlig `offers_for_eans`
  (samma mönster som live-katalogens `_enrich_with_offers`).
- Live `/v1/products/catalog` (fan-out) kan behållas för färskhet men blir overflödigt för bläddring.
- Schema: nya/utökade Pydantic-modeller i `schemas.py` + `OWN_APIS`-poster + `test_schemas.py`-täckning
  (projektets kontrakt-regel). Konsol-status (rader/kedja, senaste crawl, available-andel) i Översikt,
  som offers-sweepen (`offers_coverage`-mönstret).

### Live crawl-visualisering i /admin (UTTRYCKLIGT KRAV)
En dynamisk vy i konsolen som visar produkter strömma in medan crawlen kör - en fin visualisering, inte
bara en slutsiffra. Bygg ovanpå sweep-mönstret men rikare:
- `CRAWL_STATE` (in-memory, som `SWEEP_STATE`): per kedja status + kategorier klara/totalt + produkter
  ingestade (ny/uppdaterad) + aktuell kategori som crawlas + rate (produkter/s) + ev. ETA + last_errors.
- Egen flik eller panel i konsolen som pollar `GET /v1/admin/catalog/crawl/status` (~1-2s medan
  `running`), med: progress-bar per kedja (kategorier), löpande total-räknare som tickar upp, och en
  live-feed av de senast ingestade produkterna (namn + kedja + EAN + bild-thumb) som strömmar förbi.
  "Starta crawl"-knapp (+ev. force/per-kedja) som `POST /v1/admin/catalog/crawl`, speglar sweep-knappen.
- Implementations-not: enklast via polling (som sweepen) - räcker gott. SSE/WebSocket bara om pollingen
  känns trög; håll det till polling i v1. CRAWL_STATE måste uppdateras inkrementellt under crawlen
  (per ingestad batch) så feeden/räknaren rör sig, inte bara vid slutet.

### Hyllpris-historik: läs-vy / graf (DELVIS BYGGT)
Fångsten finns: `catalog_price_observations` (append-only) skrivs i `catalog_upsert` vid pris-/
jämförpris-ändring (+ baslinje vid första pris).
- [x] **Admin-läsvy BYGGT:** `database.catalog_price_changes(chain, q, sort, limit)` (LAG-fönster ->
  föregående pris) + `GET /v1/admin/catalog/price-changes` + konsolens Sortiment-flik: beständig,
  filtrerbar (kedja + sök) och sorterbar (största/minsta ändring, höjning/sänkning) prisändrings-logg
  med upp/ner-visualisering, klickbar rad -> produktmodal. Live-uppdateras under crawl.
- [ ] **Kvar: per-EAN tidsserie + graf i KONSUMENT-appens produktmodal, SAMMANSLAGEN med
  erbjudande-historiken**: `database.catalog_price_history(ean)` (per kedja) + endpoint, och en vy som
  visar både ordinarie hyllpris (linje) och fynd-dipparna (offer_observations). Återanvänd inline-SVG-
  stegfunktionen; hyllpris som andra serie (streckad/grå). Honest: hyllpris = butik/nationellt (se
  Steg 6 om butiksscoping), offer = butikslokalt.

### Avgörande beslut (ta UPP innan bygge)
- **Nationellt, ej per butik.** Katalog-API:erna är nationella -> hyllpris + "KEDJAN för varan",
  inte "BUTIKEN för varan". Per-butiks-sortiment skulle kräva crawl × 2500 butiker × hela katalogen =
  ogenomförbart. Konsekvens: det EXAKTA kartfiltret per butik förblir offers-baserat; fulla sortiment
  ger kedjenivå-täckning + nationellt hyllpris.
- **Storlek:** ~30-50k varor/kedja × 5 ≈ 200k rader (~5x offers). Hanterbart i SQLite med index.
- **ToS/juridik:** att skörda hela kataloger är känsligare än erbjudanden - stäm av före skarp drift.
- **Beroende:** bygg EFTER datalager-översynen (se nedan) - särskilt den normaliserade `offer_eans`-tabellen,
  vars EAN-index-mönster fulla sortiment återanvänder.

---

## Översyn - datalager + struktur (MESTADELS GJORD)

> **Status:** de strukturella punkterna (1-2) är till stora delar genomförda. Kvar = test/städning (3-4).

1. **Filstorlekar/struktur:** [x] `database.py`-monoliten splittad till paketet `api/database/` per domän
   (`_conn`/`offers`/`stores`/`catalog`/`ean`/`products`/`meta`). [x] offers-/sweep-logiken utbruten till
   `api/offers.py`. [x] konsol-logiken i `web/admin.js` (ej inline i admin.html). [ ] Kvar: `api/main.py`
   är fortfarande stort (route-grupper till `api/routes/` ej gjort); `web/app.js` växer.
2. **Query-grunden:** [x] indexerad `offer_eans`-tabell byggd (offer_id->ean, fylld vid
   `replace_store_offers`) -> snabba uppslag i `stores_with_offer`/`offers_for_eans`/`price_history`,
   ersätter `json_each`-scans. (EAN normaliseras nu vid skrivning, se "normalisera offers-EAN".)
3. **Testtäckning:** [ ] fortfarande mest schema-drift-testet; lägg tester runt de tyngsta läs-funktionerna.
4. **Övrigt:** [ ] döda/oanvända helpers, dubblerad logik, derive-at-read-drift, konsekvent felhantering.

Resterande (3-4) kan tas i en fokuserad städ-runda; de strukturella tunga lyften är redan inne.


## Steg 6 - Per-butik-priser (Coop/ICA geografisk prisintelligens) (PLANERAT, ej påbörjat)

Spåra hyllpris PER BUTIK för de kedjor som är butiksprissatta (Coop + ICA), så vi kan svara
"var är varan/matkassen billigast - hos mina favoritbutiker / nära mig". Bygger ovanpå Steg 5
(katalog) men är ett eget, tyngre subsystem. **Bygg inte förrän det finns en uttalad produkt
(t.ex. statistik-/matkasse-appen) som motiverar det - en representativ butik per kedja räcker
för enkel cross-chain-jämförelse.**

### Nuläge (grunden som redan finns)
- `catalog_products.store` taggar vilken butik (ledger/account) priset är scopat till (Coop=251300,
  ICA=1003647, Axfood/CG=NULL=nationellt). Backfillat.
- `catalog_price_observations` (append-on-change) - idag EN butik per kedja.
- `stores`-tabellen har ALLA Coop-ledgers (`native.ledgerAccountNumber`) + ICA-accounts
  (`native.accountNumber`) -> vi kan crawla vilken butik som helst.
- Crawl-maskineri (`catalog_crawl.py`), daglig cadence, per-butik-param finns redan (`store`/`accountNumber`).
- Favoriter (`favorites`, `chain:store_id`) finns - används idag för "rea hos favoriter" i offers-compare.

### Research-fynd (Steg 0, gjord - styr designen)
- **Coop:** ~43% av ledgers frågbara (resten ej e-handelsindexerade); zoner INKONSEKVENTA per förening
  -> ingen "en butik/förening"-genväg. ~310 frågbara av 722.
- **ICA:** 100% queryable (alla 1289 accounts svarar), per-butik-pris, ingen zon-struktur -> alla 1289.
- Slutsats: ingen billig genväg. Full matris worst case ~39M ICA + ~? Coop pris-punkter.

### Vägval: bygg INTE hela matrisen eagerly - efterfrågestyrt
Crawla i prioritetsordning, inte allt:
1. **Favoritbutiker** (användare bryr sig) - full katalog, tätare. När någon favoritar en NY butik ->
   köa den för crawl (demand-driven). Bundet av faktiskt intresse, inte 39M på spekulation.
2. **Representativt urval** per kedja/region för nationell-ish jämförelse + "billigast nära dig"-grundtäckning.
3. **Resten** inkrementellt/sampling, lägst prio.
Detta håller storage + last bundet till vad som faktiskt används.

### Datamodell (separera master från butikspris)
```
catalog_products            -- BLIR butiksoberoende master (namn/brand/ean/kategori/bild/förpackning).
                               'price'/'store' kvar som REPRESENTATIVT pris (bakåtkompat) ELLER flyttas ut.
catalog_store_prices         -- NY. PK (chain, product_id, store)
  ean, price, comparison_value, comparison_unit, available, first_seen, last_seen
  -- INDEX(ean), INDEX(chain, store), INDEX(store)
catalog_price_observations   -- + store-kolumn -> append-on-change per (chain, product_id, store)
store_crawl                  -- NY. per (chain, store): queryable (bool), priority, last_crawled,
                               product_count, status. Driver rotationen + minns ej-frågbara (sluta fråga).
```
Mappning favorit -> crawl-butik: favoritens `store_id` -> `stores.native` -> ledger/account.

### Crawl-strategi (generisk per-butik-rotation, återanvänd Steg 5)
- En kö av (chain, store) ur `store_crawl`, prioritetsordnad (favoriter > representativa > resten).
- Staggrat: N butiker/natt, tungt rate-limitat, egen cadence (`STORE_PRICE_CRON`), circuit-breaker/
  cooldown vid WAF (samma mönster som EAN-warmingen/sweepen).
- Coop varierar `store={ledger}`; markera ej-frågbara i `store_crawl.queryable=0` (sluta fråga).
- ICA varierar `accountNumber` (alla frågbara).
- Inkrementellt: re-crawla en butik var M:e dag; favoriter oftare.
- Återanvänd `catalog_crawl._search_*`/`_*_row` men parametrisera butiken (idag fast COOP_DETAIL_STORE/
  ica_resolve_accounts()[0]).

### Nya API-endpoints
- `GET /v1/products/{ean}/prices` - per-butik-pris för en EAN (cross-chain). Query: `stores=`
  (specifika, t.ex. favoriter), `near=lat,lng&radius=` (närmaste), default representativa.
- `GET /v1/compare/basket?stores=&eans=` - matkasse-jämförelse: summa per butik för en varukorg
  (PRO-undersöknings-caset). Markerar saknade varor per butik.
- Utöka `catalog_browse`/`compare` med butiks-scope (`stores=`) -> hyllpris filtrerat till favoriter.
- Admin: `store_crawl`-status + per-butik-trigger + queryability-karta (likt partial/EAN-warm-korten).

### Kart-appen (konsument) - ny funktionalitet
- **Butiksval/favoriter som jämförelse-scope:** "jämför sortiment bara mot mina favoritbutiker"
  (infran finns - favoriter används redan för offers "rea hos favoriter"; utöka till katalog/hyllpris).
- **Per produkt:** "billigast hos dina favoriter" / "billigast nära dig" + en liten butikslista med pris.
- **Matkasse-vy:** lägg varor i en korg -> jämför totalsumma över favoritbutiker/kedjor.
- **Prisvärmekarta:** var är varan/korgen billigast geografiskt (Leaflet, vi har redan kartan).
- **Hantera ej-frågbara favoriter:** ~57% Coop-butiker saknar e-handelspris -> visa "inget hyllpris för
  den butiken" snyggt (inte tom). Demand-crawl: favoritmarkering köar butiken; visa "hämtar priser...".

### Databasval: SQLite vs PostgreSQL (svar på frågan)
- **Nu / nuvarande scope:** SQLite (WAL) räcker gott - även Steg 5:s ~74k katalograder. Ingen anledning byta.
- **Per-butter-skalan:** ~39M ICA-rader är teknisk möjligt i SQLite (indexerat, append-on-change-historik
  är kompakt), MEN trycket ökar: (a) samtidig tung crawl-skrivning + många API-läsningar (SQLite har
  en-skrivare-lås; WAL klarar 1 skrivare + många läsare men hög skrivvolym + läsning kan ge kontention),
  (b) geo-frågor "billigast nära mig" -> **PostGIS** är överlägset, (c) tunga analytiska frågor (statistik-
  appen) -> Postgres query-planner/partitionering starkare, (d) **api/app/admin-splitten** (uttalat mål):
  separata processer/containrar delar inte gärna en SQLite-FIL -> en DB-server (Postgres) är då naturlig.
- **Rekommendation:** migrera INTE preemptivt. **Triggers** (vilken som helst räcker): per-butter-skalan
  visar kontention/perf-problem ELLER geo/PostGIS behövs ELLER statistik-appen kräver tung analys ELLER
  api/app/admin splittas till separata tjänster. Förbered genom att gå via **SQLAlchemy ORM** (projektets
  egen eskaleringsväg "när det växer") som mellansteg - då blir SQLite->Postgres ett dialekt-byte, inte en
  omskrivning. Migrationer fortsatt utan Alembic (ALTER-guards) tills ORM införs.

### Faser (resumerbart)
1. **Datamodell:** `catalog_store_prices` + `store` i observationer + `store_crawl` (queryability/prio).
2. **Per-butik-crawler:** parametrisera butiken i `catalog_crawl`, rotations-kö, egen cadence, WAF-skydd.
   Starta med favoriter + ett litet representativt urval; markera ej-frågbara Coop-ledgers.
3. **Läs-API:** `/v1/products/{ean}/prices` (stores/near) + admin-status.
4. **Kart-app:** favorit-scope:ad jämförelse + per-produkt "billigast hos favoriter".
5. **Matkasse + geo:** `/v1/compare/basket`, prisvärmekarta.
- DB-migrering (Postgres/ORM) tas in NÄR en trigger slår, inte som egen fas i förväg.

### Caveats att rama in
- Per-butter-pris finns bara för FRÅGBARA butiker (Coop ~43%); favoritar man en ej-frågbar butik finns
  inget hyllpris - kommunicera det.
- Hyllpris != kassapris (samma caveat som idag); medlemspris/erbjudanden ovanpå.
- Stor storage/last -> efterfrågestyrt (favoriter först), inte full matris.
