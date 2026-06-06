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
    - [x] **Normalisera tillverkarnamn BYGGT (förarbete till tillverkar-filtret).** Fristående
      `api/manufacturers.py` (derive-at-read likt `categories`/`tags`): `manufacturer_key()` grupperar
      varianter (gemener, konservativ legal-suffix/region-strippning - INTE "Foods"/"Group" som ofta är
      del av namnet) och `canonical()` ger display-namn (MAP-override per nyckel, annars städad default).
      `manufacturer_map`-tabell (admin-redigerbar) + `set_map` vid uppstart/ändring. Konsolens
      **Tillverkare-flik** (`/v1/admin/manufacturers` + `.../map`): råvarianter grupperade på nyckel
      med antal, sätt kanoniskt namn för semantiska merges ("Arla Foods"+"Arla"->"Arla"). Kalibrerat:
      2808 brands -> 2473 nycklar (case/legal/punkt-varianter grupperas auto; 310 grupper med >1 variant).
      `catalog_browse` exponerar `manufacturer` (kanonisk) per produkt.
    - [x] **Lista/filtrera produkter per tillverkare BYGGT (API)** (2026-06-05). `catalog_browse(manufacturer=...)`
      filtrerar på normaliserad `manufacturer_key` (tål både aggregatets `key` och fritt namn -> samma nyckel).
      Nytt aggregat `catalog_manufacturers(chain, q, limit)` + `GET /v1/products/catalog/manufacturers`
      ({key, name, count}, flest produkter först; `q` filtrerar namn). Schema `CatalogManufacturersResponse`
      + OWN_APIS + test_schemas (browse-count == aggregat-count). Bara API (ingen frontend - per ROADMAP).
      Känd egenhet: `®`/sub-märken (Arla® vs Arla) blir separata nycklar (manufacturer_key strippar ej
      trademark-symboler); admin-flikens map kan merga. Eventuell normaliserings-tuning är separat.
    - [ ] **(framtid) Tillverkar-filter/-katalog i kart-appen.** API:t finns (ovan); en UI (tillverkar-chip
      eller dropdown i bläddra-vyn ovanpå `/catalog/manufacturers` + `?manufacturer=`) återstår. Främst
      relevant för analys-appen, kanske inte i nuvarande kart-app.
    - **Kost-filter: vegan/vegetariskt (+ härled när otaggat).**
      - [x] **Vegan/vegetariskt som kombinerbara chips BYGGT** (2026-06-05). Kost-dropdownen ersatt av två
        chips (🌱 Vegansk / 🥬 Vegetarisk) inblandade i bläddra-vyns kategori-rad (avvikande grön-tonad färg,
        `.browse-diet`). TVÄRGÅENDE: kombineras fritt med en vanlig kategori (Mejeri + Vegansk = veganska
        mejeri-alt; verifierat 2986 -> 322 vegan / 1402 vegetarisk). Designval (bekräftat med användaren):
        båda valbara samtidigt, vegetarisk dominerar (⊃ vegansk) via `browseDietParam()`; `browseState.diet`
        ersatt av flaggorna `vegan`/`vegetarian`. Backend oförändrad (stödde redan `category`+`diet`).
        Frontend ej browser-testad (node --check OK). Kvar (sido): diet i delbar hash (idag transient).
      - [x] **v1 BYGGT - härledning + badge.** `details.classify_diet(ingredients)` -> `diet`-fält
        (`vegan`/`vegetarian`/`none`/null) deriverat read-time i `normalize_info` (vokabulär likt
        `extract_allergens`: kött/fisk -> none; mejeri/ägg/honung/gelatin -> vegetarisk; annars vegan;
        `\b`-ordstart så "kokosmjölk" ej träffar "mjölk", PLANT_OK-allowlist för "äggplanta" mfl).
        Exponerat i `ProductInfoData.diet`; produktmodalen (konsument + konsol) visar "🌱 Vegansk"/
        "🥬 Vegetarisk (härledd)"-badge. Kalibrerat på ~11k ingredienslistor (52% vegan/33% veg/15% none).
      - [x] **TVÄRGÅENDE filter i bläddra-vyn BYGGT.** `details/diet.classify_diet` flyttad till
        fristående `api/diet.py`. `database.get_product_diets()` (derive-at-read ur cachade ingredienser,
        ~11k EAN) + `diet`-param i `catalog_browse` (filtrerar HELA mängden före paginering; vegan ⊂
        vegetarian; produkter utan ingredienslista faller bort) + `diet` på `/v1/products/catalog/browse`
        + dropdown "🥬 Vegetariskt / 🌱 Veganskt" i bläddra-vyn. Verifierat (choklad: 200 alla -> 31 vegan).
      - [x] **(a) Kategori-räknarna speglar diet-filtret BYGGT** (2026-06-05, REVIEW E). `catalog_summary`
        tar nu `diet` och filtrerar grupperna identiskt med `catalog_browse` (samma `get_product_diets()`-map);
        `/v1/products/catalog/summary` + frontend (`loadBrowseSummary` skickar diet, cachenyckel + listener
        uppdaterar räknarna). Verifierat: summary-kategori = browse-total per filter (frukt_gront 1024 ->
        vegan 45 / vegetarian 48).
      - [x] **(b) Livsmedels-guard BYGGT** (2026-06-05). Icke-livsmedel med ingredienslista (kosmetika/tvål,
        rengöring, djurmat) filtreras bort ur kost-filtret via kanonisk kategori (`_NONFOOD_DIET =
        {halsa_skonhet, hem_hushall, djur}`) i `catalog_browse` + `catalog_summary`. `barn`/`ovrigt` lämnas
        (kan vara mat). Verifierat: hem_hushall/halsa_skonhet/djur -> 0 under vegan, mat orörd; summary = browse.
      - [ ] **Kvar/finputs:** (c) täckning växer med product_info-warmingen (idag ~11k av 35k katalog-EAN
        har ingredienser).
    - [x] **Filtrera bläddra-vyn på "rea hos favoriter" BYGGT.** Toggle "★ Rea hos favoriter"
      (login-only) -> visar bara produkter som har ett ERBJUDANDE hos användarens specifika
      favoritbutiker (per-butik-exakt via `eans_on_offer_at_stores`, chunkat). Favoriterna hämtas
      server-side ur inloggad användares `list_favorites` (ej från klient). Vald tolkning: "på rea
      hos favoriterna" (katalogen är nationell -> per-butik-sortiment finns ej; per-kedje-varianten
      valdes bort).
    - [x] **Zooma kartan till favoriternas bbox vid favorit-vyer BYGGT** (2026-06-05). `fitToFavorites(keys)`
      i `web/app.js` (`map.fitBounds` till favoritbutikernas koordinater, symmetrisk padding/maxZoom 13).
      `showFavoriteOffers` zoomar till alla favoriter, `showCompareFavorites` till de jämförda (COMPARE_CHAINS-
      delmängden). No-op om inga favoriter har koordinater. Frontend ej browser-testad (syntax OK).
    - [x] **Bugg: avfavorisera via stjärnan i kartpopupen FIXAD** (2026-06-05). Popup-handlern läste
      `isFav(s)` SYNKRONT efter ett oawaitat `toggleFav(s)` (som sätter `state.favorites` först efter
      `await fetch`) -> visuellt fel + upprepade klick slog tillbaka (POST återskapade favoriten). Nu
      `await toggleFav` + popup-innehållet skrivs om via `setPopupContent(popupHtml(s))` (rätt stjärn-
      state även vid återöppning, då on-state bakas in vid render-tid) med re-wirad DOM. Popup-wiringen
      utbruten till `wirePopup()`. Frontend ej browser-testad (node --check OK).
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
  - [x] **Översikt-fliken (#overview) genomgången (2026-06-05).** Lade till Steg 6-kort "Per-butik-priser
    (ICA/Coop)" (catalog_store_prices-volym, antal crawlade butiker, valda, senaste crawl + crawlar-status)
    via ny `database.store_prices_stats()` -> `overview.store_prices`. Övriga kort/tabeller stämde fortf.
  - [x] **"Uppgradera alla glesa" cappas inte längre till 300 (2026-06-05).** Knappen skickar `cap=0` ->
    `upgrade_sparse_partials(cap=0)` tolkar 0 som obegränsat (`limit=None`), schemalagd körning (cap=None)
    behåller `PARTIAL_UPGRADE_CAP`-defaulten. Frontend `triggerPartialUpgrade` skickar nu cap-param även
    vid 0 (`cap != null`, tidigare falsy-droppad).
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
- AVGJORT (2026-06-06): **per butik** för butiksprissatta kedjor (ICA/Coop) - vi spårar pris i ALLA
  frågbara butiker (se Steg 6). Den tidigare "avvägning per butik vs nationellt aggregat" är därmed
  resolverad till per butik; ToS/last hanteras som drift-fråga (rate-limit/cadence), inte genom att
  avstå granulariteten.

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
- [x] **ICA BYGGT** (`_crawl_ica` / `_ica_fetch_store`): `queryString:"*"` (wildcard) + `offset`,
  `stats.totalHits` = total. Återanvänder `catalog._norm_ica`, `product_id`=gtin. (`""` ger 0; `*` är wildcarden.)
- [x] **ICA-crawl effektiviserad + storleks-villkorlig (2026-06-05, empiriskt grundad).** Bakgrund: ICA:s
  globalsearch cappar offset HÅRT vid 20000 (`*` ger 0 docs vid offset >= 20000), så `*` allena når bara
  ~20k av t.ex. 44k på en storbutik. `totalHits` är dock ärligt även när svaret cappas. **Mätningar (live
  mot butik 1003807, 44 422 produkter):** (a) `mainCategoryName` saknas på ~0% av produkterna, ecom-nivåerna
  på ~6%; (b) queryString på kategorinamn är textsök med **100% recall**, låg precision (over-return ->
  dedup på gtin); (c) ingen kategori > ~4k -> inget kategori-fråga cappas; (d) `take`=2000 ger 2000 docs.
  **Slutsats: flaskhalsen var kategori-UPPTÄCKT, inte matchning.** Åtgärder:
    1. **`ICA_CRAWL_PAGE` (egen, default 1000)** i st.f. delade `CATALOG_CRAWL_PAGE`=100 -> ~10x färre
       requests/butik. Verifierat take=2000 funkar; egen knapp då övriga kedjor kan ha andra server-caps.
    2. **Storleks-villkorlig walk:** `totalHits <= 20000` (89,6% av butikerna, 1155/1289) -> `*` gav HELA
       sortimentet, **ingen kategori-walk** (100% täckning, ~14-20 requests). `> 20000` (~10%) -> kategori-walk.
    3. **Komplett kategori-union** (`database.ica_walk_categories`, ny tabell): varje `*`-walk skördar
       `mainCategoryName` -> persisterad butiks-OBEROENDE union. Små butikers ocappade walk bidrar med hela
       sin kategorimängd -> unionen konvergerar mot ICA:s taxonomi. Stora butiker använder DEN (inte sin egna
       cappade 44-skörd) + `_ICA_CATEGORIES` som säkerhetsnät. **Mätt resultat: ~99,7% täckning** (44268/44422,
       ~179 requests) mot ~94% med gammal metod. ecomLevel2 (260 noder) ger 97,5% till ~4x requests - ej använt.
  Bugg-fix på vägen: `store_total` skrevs över med 0 vid offset>=20000 (ICA svarar `totalHits:0` där) ->
  läs bara från första sidan. Dokumenterat i CLAUDE.md (Designbeslut: "ICA-crawlens täckning").
- [x] **Coop BYGGT** (`_crawl_coop`): perso-search är fritext-only, MEN `POST personalization/search/
  entities/by-attribute` med `{"attribute":{"name":"categoryIds","value":"<kod>"},"resultsOptions":{skip,take}}`
  browsar en hel kategori (verifierat via Claude Chrome: Mejeri & Ägg = 876 produkter = by-attribute kod 6262).
  Departement-rötterna (kod = navCategories-rot, tom `superCategories`; 19 st) harvestas ur produkternas
  `navCategories` via ~30 breda sökningar och cachas (`_COOP_ROOTS`). `_norm_coop` återanvänds; product_id=EAN.
- [x] **Coop-crawl utvärderad + sidstorlek höjd (2026-06-05).** Till skillnad från ICA har Coop INGET
  offset-cap (skip paginerar till sista produkten) och de 19 departement-rötterna är en KOMPLETT partition
  (mätt: 694 sök-samplade produkter låg alla i departement-crawlen, 0 saknade rot). Ingen kategori-union/
  villkorlig logik behövs. Enda vinsten: `COOP_CRAWL_PAGE` (default 300) i st.f. 100 - take cappar vid
  ~400-499 (500 ger tomt 200-svar), så ~3x färre requests. Full butik = ~12,7k distinkta EAN.
- [x] **Axfood (Willys/Hemköp) BYGGT** (`_crawl_axfood`): kategoriträd `GET leftMenu/categorytree` (rot N00 ->
  19 avdelningar, slug i `url`), produktlista `GET /c/<slug>?page=&size=` (`results` + `pagination.numberOfPages`).
  Olika prefix per sajt (Willys `/axfood/rest/v1`, Hemköp bart) + eget träd/koder per sajt. EAN ej inline ->
  slås upp gratis ur `ean_cache` (NULL annars, fylls av `warm_axfood_eans` över tid). category_raw faller till
  avdelningens titel (`/c/` saknar googleAnalyticsCategory). Recon-vägen knäcktes via Claude Chrome.
- [x] **City Gross- + Axfood-sidstorlek utvärderad (2026-06-05).** **City Gross:** Loop54 har inget take-
  cap (verifierat take=5000 = hela kategorin) eller skip-cap -> `CG_CRAWL_PAGE` (default 1000) = ~10x färre
  requests. **Axfood (Willys/Hemköp):** `/c/<slug>` cappar size HÅRT vid 100 (size=500/1000/2000 ger alla
  100) -> ingen sidstorleks-vinst, lämnas på 100. Ingen page-cap (täckning komplett). Dokumenterat i CLAUDE.md.

### Cadence + rate-limiting (återanvänd run_scheduler + sweep-mönstret)
Mycket större än offers-sweepen (tusentals paginerade anrop/kedja). Därför:
- **Rullande/inkrementell:** crawla N kategorier per körning (cap, som `warm_ica_categories`), sprid över
  ett dygn. Egen `CATALOG_CRAWL_CRON` i config + `run_scheduler(cron, tz, crawl_job, "katalog-crawl")`.
- Samma skydd som `sweep_offers`: bunden parallellism (`CATALOG_CRAWL_CONCURRENCY`), paus mellan anrop,
  exponentiell back-off/retry, circuit breaker per kedja. Spegla `_sweep_chain`/`_sweep_one_store`.
- `last_seen` < senaste fullständiga crawl-runda -> sätt `available=0` (utgången vara behålls för historik).
- INGEN crawl vid uppstart (skonar kedjorna); trigga via konsol-knapp + schema, som sweepen.

### Konsol-UI: får ALDRIG blockera renderingen (lazy-laddning)
Inget i API-konsolen (`/admin`) ska göra att hela UI:t hänger sig (webbläsaren fast i "loading").
- [x] **Sortiment-fliken KLAR (2026-06-06):** `loadCatalog` byggde skelettet EFTER tre sekventiella
  status-anrop (catalog-status ~1,8s) -> nu `ensureCatalogSkeleton()` först + `Promise.all` på status-
  anropen. Butiksurvalet (~2000 rader) laddas lazy bakom "Hantera butiksurval"-toggle (`ssLoaded`-flagga);
  sammanfattningen visas ur measure-stats direkt. (Mätt: store-prices/stores var faktiskt snabb 0,03s -
  boven var render-ordningen, inte listan.)
- [x] **Universell spinner i `show()` KLAR:** en tom flik får en spinner-platshållare direkt -> ingen
  blank/"fastnad" skärm under laddning, alla flikar.
- **Granskning av alla flik-laddare (mätt 2026-06-06 mot PG).** Med spinnern ger ingen flik längre en
  blank/"fastnad" skärm. Kvar var LATENS på endpoints som awaitas före render:
  - [x] **Versionerad stats-memo KLAR (2026-06-06).** Alla sex dyra argumentlösa konsol-aggregat cachas
    nu på FUNKTIONSNIVÅ (`@stats_memo` i `_conn.py`): `ean_stats` (~1,3s), `catalog_stats` (~0,65s),
    `partial_info_counts` (~1,3s), `offer_observations_stats` (~1,1s), `product_info_observations_stats`
    (~83ms), `offers_coverage` (~85ms) -> alla varm 0ms. (`store_prices_stats` redan materialiserad ~5ms.)
    EN version-räknare (`invalidate_stats()`/`stats_version()`) bumpas vid varje data-skrivning som
    påverkar dem: katalog-crawl-slut (`catalog_crawl`), butikspris-crawl-slut (`store_crawl`),
    `warm_after_sweep`, `sync_and_warm`, `upgrade_sparse_partials`. TTL-backstop 600s fångar konsument-
    driven drift (product_images/info växer av bläddring) + missade hooks + omstart. Lat omräkning
    (invalidate bumpar bara versionen). `/v1/admin/catalog/crawl/status` delade `catalog_stats`+
    `partial_info_counts` med overview -> EN omräkning återanvänds av båda. **Overview-bundlen**
    (`_overview_stats`) nycklas nu på `stats_version` (+ TTL höjd 30s->300s): crawl-slut invaliderar
    version-räknaren -> nästa overview räknar om. Mätt: overview kall 4,2s -> varm 0ms. Version->overview-
    länken verifierad via direkt `invalidate_stats()`-anrop; full crawl-cykel ej körd (hooken är kod-placerad
    vid record_crawl_run/warm-slut). **Per-process** (in-process scheduler, single-worker) - bryts vid
    `uvicorn --workers>1`.
  - [x] **Märkesvaror-fliken skal-först KLAR (2026-06-06).** `loadMarques` byggde hela DOM:en EFTER
    `private-products`-fetchen (~3,5s) -> fliken kändes hängd. Nu `ensureMarquesSkeleton()` (renderar
    layout + hjälpruta + tom kedje-select SYNKRONT, spinner i `#mqList`, `dataset.ready` = bygg en gång)
    + async-fyllning av listan efteråt; `loadMqGroups` parallellt. Kedjefiltret behålls över omladdning.
    (Backend-skannen `_products` ~3,5s kvar - kan cachas på `stats_version` senare, gynnar även
    match-suggestions; ej gjort, listan är inte längre blockerande.)
  - OK (snabba, <0,15s, inget behov): sources, categories, manufacturers, settings. Skal-först redan:
    calls, tags, keys, catalog.

### Crawl-prestanda - hävstänger att undersöka framöver (TODO, mätt 2026-06-05)
Tidsprofil uppmätt efter sidstorleks-höjningen (per butik, produktions-pace 0.35s/sida): **~2/3 nätverk
(HTTP-rundtur + JSON-parse), ~1/3 pace**. ICA stor (44k) ~179 req ~180s; ICA liten (<20k) ~14-20 req ~15-20s;
Coop (~12-15k) ~56 req ~59s. **De ~134 stora ICA-butikerna (>20k) = ~60% av ICA:s totaltid** (10% av butikerna).
Full bägge-kedjor-crawl med AIMD-parallellism ~1-1,5h (ICA ~11h / Coop ~3,5h enkeltrådat). Dokumenterat i
CLAUDE.md ("Per-butik-crawlens tidsprofil"). Hävstänger ej utvärderade i drift:
  - [ ] **Sänk `_PAGE_PACE`** (0.35 -> 0.15-0.20s) -> ~15-20% kortare wall-tid. AIMD/circuit breaker fångar
    WAF redan; mät faktisk block-frekvens vid lägre pace innan permanent sänkning.
  - [ ] **Droppa breda termer (`_ICA_CATEGORIES`) på stora butiker** - med komplett kategori-union ger de nu
    marginell extra täckning (~99,7% redan). ~20 requests/storbutik × 134 ≈ 2 700 färre requests. Mät täcknings-
    tappet (om något) först.
  - [ ] **Parallellisera kategori-hämtning INOM en storbutik** (idag sekventiellt, 179 req i rad = långpolen
    på ~180s). Störst potential för storbutikerna, men ökar WAF-risken mest -> bunden parallellism + per-butik-AIMD.
  - [ ] **Höj `_MAX_CONC`** (12 -> högre) - men det är en medveten säkerhetsgräns; AIMD hittar redan faktiska
    gränsen under. Mät om kedjorna tål mer innan taket höjs.
  - [ ] Mer `take` ger nu AVTAGANDE nytta (payloaden växer, ~0,65s/req även vid take=1000) - inte en hävstång.
  - [ ] **Overview-kallstart** (`_overview_stats`): de tunga aggregaten är nu versions-memoiserade
    (varm 0ms, crawl-slut invaliderar) men FÖRSTA laddningen efter omstart är fortf. trög (~4,7s,
    dominerat av `ean_stats` ~1,3s + `partial_info_counts` ~1,3s + observation-stats + storage-scan).
    Kvar om det stör: warma memon i lifespan (som `warm_catalog_cache`) eller materialisera EAN-unionen
    (counter vid insert) i st.f. UNION-distinct read-time.

### Crawl-historik/observabilitet - persistera per-körning (TODO, 2026-06-05)
  - [x] **Crawl-körningshistorik BYGGT (2026-06-06).** Tabell `crawl_runs(kind, chain, started, finished,
    status, rows, changed, errors, stores_ok, stores_total, last_error)` (`api/database/crawl_runs.py`),
    skrivs vid varje körnings slut: per-butik (`store_crawl._run_chain` finally, kind='store_prices') och
    master (`catalog_crawl.crawl_all` finally, kind='catalog'). `GET /v1/admin/crawl-history` + historik-vy
    i Sortiment-fliken (tid/kedja/typ/rader/prisändringar/fel/längd/status). DURABLE "ändringar sedan senaste":
    `last_crawl_runs()` exponeras i crawl/status -> Steg-6-korten visar senaste körningen även efter omstart
    (in-memory-staten nollställs då). Motiverades av 22:12-incidenten som inte lämnade spår. (Möjlig
    utbyggnad: löpande skrivning för pågående körning + `crawl_run_errors` för fler fel/körning.)

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
- [x] **BYGGT: per-EAN hyllpris-tidsserie + graf i KONSUMENT-modalen, sammanslagen med erbjudande-
  historiken.** `database.catalog_price_history(ean)` (per kedja, kollapsad på lika pris) -> `shelf`
  i `GET /v1/products/{ean}/history`-svaret. Modalens inline-SVG ritar nu hyllpriset som en STRECKAD
  kontinuerlig stegfunktion (ordinarie) bakom erbjudande-seriernas heldragna fynd-dippar. Honest:
  hyllpris för Coop/ICA är butiksscopat (se Steg 6), offer butikslokalt. (Serien kort tills crawl-
  historiken vuxit.)

### Avgörande beslut (ta UPP innan bygge)
- **Nationellt för Steg 5 (de nationellt prissatta kedjorna), per butik för ICA/Coop i Steg 6.**
  ~~"Per-butiks-sortiment är ogenomförbart"~~ - det antagandet är ÖVERSPELAT (beslut 2026-06-06):
  Willys/Hemköp/CG ÄR nationella (en crawl, nationellt hyllpris), men ICA/Coop är butiksprissatta och
  spåras nu per butik i ALLA frågbara butiker (Steg 6). "Ogenomförbart" gällde att crawla × tusentals
  butiker × hela katalogen - det är exakt vad vi medvetet bygger nu; den verkliga begränsningen är
  crawl-tid/WAF (drift), inte teknisk omöjlighet. Konsekvens: kartfilter/jämförelser kan vara per butik
  för ICA/Coop, kedjenivå/nationellt för de andra. (Se Steg 6 "Konsekvenser av beslutet".)
- **Storlek:** ~30-50k varor/kedja × 5 ≈ 200k rader (~5x offers). Hanterbart i SQLite med index.
- **ToS/juridik:** att skörda hela kataloger är känsligare än erbjudanden - stäm av före skarp drift.
- **Beroende:** bygg EFTER datalager-översynen (se nedan) - särskilt den normaliserade `offer_eans`-tabellen,
  vars EAN-index-mönster fulla sortiment återanvänder.

---

## Översyn - datalager + struktur (GJORD)

> **Status:** de strukturella punkterna (1-2) är genomförda. Testtäckningen (3) i stort sett klar; kvar = sweep-test + småstädning.

1. **Filstorlekar/struktur:** [x] `database.py`-monoliten splittad till paketet `api/database/` per domän
   (`_conn`/`offers`/`stores`/`catalog`/`ean`/`products`/`meta`). [x] offers-/sweep-logiken utbruten till
   `api/offers.py`. [x] konsol-logiken i `web/admin.js` (ej inline i admin.html). [x] **`api/main.py`-route-
   split GJORD** (2026-06-05, pass 1-3): konsument-routerna utbrutna till `api/routes/` (admin_vocab/stores/
   compare/products) + `require_consumer` till `api/deps.py`; main.py 1446 -> 739 rader. [ ] Kvar: `web/app.js` växer.
2. **Query-grunden:** [x] indexerad `offer_eans`-tabell byggd (offer_id->ean, fylld vid
   `replace_store_offers`) -> snabba uppslag i `stores_with_offer`/`offers_for_eans`/`price_history`,
   ersätter `json_each`-scans. (EAN normaliseras nu vid skrivning, se "normalisera offers-EAN".)
3. **Testtäckning:** [x] `tests/test_schemas.py` (shape) + `tests/test_logic.py` (normalize_ean/archive/
   stores_with_offer/price_history) + `tests/test_reads.py` (catalog_browse paginering/filter/sort,
   price_changes, price_history, diet, tillverkar-normalisering, split_origins) + `tests/test_compare.py`
   (`build_comparisons`: min_chains/min_stores-grindar, dedup, unit_price-vs-price, manual_groups) +
   `tests/test_auth.py` (gating: 13 endpoints -> 401/403, öppna -> 200, ogiltig X-API-Key). [ ] Kvar:
   `_ensure_offers`/sweep-cykeln, catalog_crawl per-kedje-parsers.
4. **Övrigt:** [x] dubblerad `product_info`-batch-logik avduplicerad (REVIEW fynd C: `_product_info_fields`)
   + diet-map cachad (fynd D). [ ] Kvar: ev. döda/oanvända helpers, derive-at-read-drift, konsekvent felhantering.

Fullständig genomlysning + rangordnade fynd i **REVIEW.md** (uppdaterad 2026-06-05). Översynen är i praktiken
klar: alla P1-fynd + de fristående P2 (C/D/E) åtgärdade; kvar = sweep-/crawl-parser-tester och Steg 6-bundna
fynd (F: per-butik-`store`-kolumn, G: Coop-butiksscoping).


## Steg 6 - Per-butik-priser (Coop/ICA geografisk prisintelligens) (INSAMLING BYGGD, LÄSVÄG KVAR)
<!-- Status 2026-06-06: datamodell (catalog_store_prices/store_crawl), mät-sweep (queryability), per-butik-
rotations-crawl (AIMD, härdad), intervall i bläddra-vyn + butik-modal, per-butik prishistorik, och
crawl-körningshistorik = BYGGT. KVAR (den faktiska konsumentnyttan): geo-/favorit-/matkasse-läsvägen
(`/{ean}/prices?near=`, `/compare/basket`, butiks-scope i browse/compare) + kadens/färskhet i drift. -->


Spåra hyllpris PER BUTIK för de kedjor som är butiksprissatta (Coop + ICA), så vi kan svara
"var är varan/matkassen billigast - hos mina favoritbutiker / nära mig". Bygger ovanpå Steg 5
(katalog) men är ett eget, tyngre subsystem. **Beslut 2026-06-05: vi siktar på ALLA frågbara butiker
(ej bara efterfrågestyrt), med per-butik-historik från dag 1 och admin-styrt urval.** (Den tidigare
brasklappen "vänta på en uttalad konsument" är medvetet åsidosatt.) Fas 1 (datamodell + mät-sweep) är
låg risk och ger skala-siffran som DB-beslutet vilar på.

### Konsekvenser av beslutet "alla frågbara butiker" (2026-06-06)
Beslutet att spåra pris i ALLA frågbara ICA/Coop-butiker (inte representativt urval) flyttar tyngdpunkten
från "ska vi?" till drift + konsumentnytta. Konkreta konsekvenser:
- **Skala blir ett hårt krav, inte en öppen fråga.** Full matris ≈ ICA 1288 × ~13k + Coop ~214-310 × ~12k
  ≈ **~20M rader**, plus per-butik prishistorik (append-on-change) ovanpå. Vi är på ~3,6M idag (341 ICA +
  48 Coop crawlade). -> **Datalager-beslutet (SQLite vs Postgres) är inte längre hypotetiskt.** Varnings-
  tecken syns redan: `recompute_store_aggregates` ~24s, `COUNT(*)` på catalog_store_prices ~6s (därför
  materialiserat). Läs-frågorna (`store_prices_for_ean`, "billigast nära mig") måste hålla vid 20-50M rader.
- **Färskhet blir det centrala drift-problemet.** En full körning ~2h (mätt) -> kan inte köras konstant.
  Värdet av "alla butiker" beror på att priserna är rimligt färska -> kadens (re-crawl var M:e dag,
  favoriter oftare) måste matchas mot hur ofta ICA/Coop faktiskt ändrar hyllpris. Den frekvensen VET vi
  inte än - crawl-historiken + `changed`-räknarna (nyss byggt) avslöjar den över tid. Prioriterad rotation
  (favoriter > representativa > resten) blir nödvändig, inte valfri.
- **Prisspridnings-mätningen byter roll: tuning, inte go/no-go.** Sedan beslutet är fattat avgör den inte
  LÄNGRE om vi gör det - men den styr kadens/prioritering (vilka varor/butiker ändras mest -> crawla dem
  oftare) och om lika-pris-kollaps sparar nämnvärt lagring. Fortf. värd att köra, för att rikta resurser.
- **Konsument-läsvägen blir THE deliverable.** Insamlingen är beslutad -> nyttan ("billigast nära mig /
  hos favoriter / hela matkassen") levereras av läs-API:erna nedan (`/{ean}/prices?near=`, `/compare/basket`).
  Vi har byggt datapipen + intervall + modal; geo-/favorit-/matkasse-frågorna är där den faktiska
  användarnyttan sitter och bör prioriteras före mer crawl-finputs.
- **ToS/last skalar upp.** Per-butik × ~1500 är den mest aggressiva skörden vi gör (22:12-PoolTimeout-
  incidenten visar att vi tänjer på deras infra). Härdningen (breaker/token/dispatcher) är nu bärande.
  En medveten rate-/cadence-hållning krävs; lagring är inte flaskhalsen, deras tålamod är.
- **Ej-frågbara butiker = hårt tak.** Coop ~43% frågbara -> för ~57% av Coop-butikerna går pris INTE att
  hämta (ej e-handelsindexerade). "Alla butiker" = "alla FRÅGBARA". Konsument-UX:t måste visa "inget
  prisdata för den här butiken" elegant, inte tomt/fel.

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

### Mål: ALLA frågbara butiker (beslut 2026-06-05), admin-styrt urval
Slutmålet är full täckning av alla FRÅGBARA butiker (ICA 100% + Coop ~43%; resten kan inte täckas - hårt
tak, ej e-handelsindexerade). Prioriterad rotation är fortfarande crawl-ORDNINGEN (inte ett tak):
1. **Favoritbutiker** (+ demand-crawl: favoritmarkering köar butiken) - tätare refresh.
2. **Representativt urval** per kedja/region - grundtäckning för "billigast nära dig".
3. **Resten av de frågbara** inkrementellt tills allt täcks.
**Admin väljer omfång:** crawla alla frågbara ELLER ett manuellt urval (se admin-tabellen nedan). Den
verkliga begränsningen är crawl-tid/WAF, inte lagring -> per-butik-färskhet på dagar/veckor är inneboende.
**Pris-historik per butik från dag 1** (beslut 2026-06-05): append-on-change-observationer skrivs per butik,
inte bara senaste pris (kan inte backfillas). Huvud-raddrivaren -> mät-sweepen (Fas 1) ger den riktiga skalan
som DB-beslutet (SQLite/Postgres) vilar på.

### Datamodell (separera master från butikspris)
```
catalog_products            -- BLIR butiksoberoende master (namn/brand/ean/kategori/bild/förpackning).
                               'price'/'store' kvar som REPRESENTATIVT pris (bakåtkompat) ELLER flyttas ut.
catalog_store_prices         -- NY. PK (chain, product_id, store)
  ean, price, comparison_value, comparison_unit, available, first_seen, last_seen
  -- INDEX(ean), INDEX(chain, store), INDEX(store)
catalog_price_observations   -- + store-kolumn -> append-on-change per (chain, product_id, store) FRÅN DAG 1
store_crawl                  -- NY. per (chain, store): queryable (bool), enabled (admin-vald, bool),
                               priority, last_crawled, product_count, status. Driver rotationen + urvalet +
                               minns ej-frågbara (sluta fråga). Mät-sweepen (Fas 1) fyller queryable+product_count.
```
Mappning favorit -> crawl-butik: favoritens `store_id` -> `stores.native` -> ledger/account.

### Admin: butiksväljare (omfångskontroll)
Filterbar tabell-lista i `/admin` (Sortiment-fliken) över butiker ur `store_crawl` joinat med `stores`:
kolumner kedja, namn, ort, frågbar, vald (`enabled`), senast crawlad, produktantal, status. **Markera flera /
välj alla**-kryssrutor + filter (kedja/ort/frågbar/namn-sök) -> sätt `enabled` på urvalet. Lägesväxel
"crawla ALLA frågbara" vs "bara valda". Driver vilka butiker rotations-crawlern tar. Speglar mönstret från
partial-/EAN-warm-korten (status + manuell trigger). Ej-frågbara visas men kan inte väljas.

### Crawl-strategi (generisk per-butik-rotation, återanvänd Steg 5)
- En kö av (chain, store) ur `store_crawl` WHERE `enabled=1` (admin-vald omfång), prioritetsordnad
  (favoriter > representativa > resten).
- Staggrat: N butiker/natt, tungt rate-limitat, egen cadence (`STORE_PRICE_CRON`), circuit-breaker/
  cooldown vid WAF (samma mönster som EAN-warmingen/sweepen).
- Coop varierar `store={ledger}`; markera ej-frågbara i `store_crawl.queryable=0` (sluta fråga).
- ICA varierar `accountNumber` (alla frågbara).
- Inkrementellt: re-crawla en butik var M:e dag; favoriter oftare.
- Återanvänd `catalog_crawl._search_*`/`_*_row` men parametrisera butiken (idag fast COOP_DETAIL_STORE/
  ica_resolve_accounts()[0]).

### Nya API-endpoints
- [x] **`GET /v1/products/{ean}/prices` BYGGT (2026-06-06).** Per-FYSISK-butik hyllpris scopat: `lat`/`lng`/
  `radius` (billigast nära plats), `favorites=true` (inloggad), `stores=chain:id,...` (explicit). Billigast
  först. `database.store_prices_geo` mappar fysisk butik (`stores`.lat/lng + native) -> ledger/account ->
  catalog_store_prices (haversine-filter). Verifierat live: 3 ICA-butiker inom 30km, 33,95 vs 34,96 kr.
  `ProductPricesScopedResponse` + OWN_APIS. (Frontend-wiring i kart-appen = nästa steg.)
- [ ] `GET /v1/compare/basket?stores=&eans=` - matkasse-jämförelse: summa per butik för en varukorg
  (PRO-undersöknings-caset). Markerar saknade varor per butik.
- [ ] Utöka `catalog_browse`/`compare` med butiks-scope (`stores=`) -> hyllpris filtrerat till favoriter.
- Admin: `store_crawl`-status + per-butik-trigger + queryability-karta (likt partial/EAN-warm-korten).

### Kart-appen (konsument) - ny funktionalitet
- [ ] **UI-OMTAG: gör per-butik-pris-vyn GEO-FIRST, inte produkt-first (TODO 2026-06-06).** Nuvarande
  lösning (per-butik-pris-modalen med tabbarna Alla/Nära kartans mitt/Mina favoriter) FUNKAR men är
  bakvänd - "nära kartans mitt" gömd i en produktmodal i bläddra-vyn (där kartan dessutom är dold).
  Bättre riktning (användarens förslag): börja från KARTAN. Sätt en pil/markör på kartan + rita en
  RADIE-cirkel (justerbar), och en knapp "Bläddra zonens sortiment" -> bläddra alla varor med pris inom
  den geografiska zonen. Flippar flödet: plats/zon -> produkter+pris, i st.f. produkt -> pris-nära.
  Kräver ev. ny läs-endpoint (katalog scopad till en geo-zon, dvs union av butikerna inom radien) eller
  att `catalog_browse` tar `near=lat,lng&radius=`. Den nuvarande modal-vyn kan finnas kvar som komplement.
  **Semantik för "zonens sortiment" (BEKRÄFTAT 2026-06-06):** varor som finns i MINST EN butik i zonen
  (union - brett och mest användbart, ej "finns i alla zon-butiker" som blir för smalt). Per vara:
  BILLIGAST i zonen + prisintervall + antal zon-butiker som har varan; sorterbart på billigast / störst
  prisspridning. **Implementations-not:** zonens aggregat (billigast/intervall) kan INTE använda de
  globalt materialiserade `catalog_products.price_min/max` - de gäller alla butiker - utan måste
  LIVE-aggregeras ur `catalog_store_prices` filtrerat till zonens ledgers/accounts (härleds via samma
  fysisk-butik -> native-mappning som `store_prices_geo`). Den live-aggregeringen är den tunga,
  skala-känsliga frågan -> mät den (spike) FÖRE kart-cirkeln; den siffran avgör också SQLite-vs-Postgres
  (se "Databasval" nedan: serverings-tabellen är bunden ~20M, bara observations-historiken växer fritt).
- **Butiksval/favoriter som jämförelse-scope:** "jämför sortiment bara mot mina favoritbutiker"
  (infran finns - favoriter används redan för offers "rea hos favoriter"; utöka till katalog/hyllpris).
- **Per produkt:** "billigast hos dina favoriter" / "billigast nära dig" + en liten butikslista med pris.
  (DELVIS BYGGT: per-butik-pris-modalen med scope-tabbar - se UI-OMTAG ovan.)
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
- **MÄTSPIKE zon-browse-query (2026-06-06, vid 4,34M ICA-rader / 326 crawlade butiker):** den live-
  aggregerade zon-frågan (`catalog_store_prices` filtrerat till zonens butiker, MIN/MAX/COUNT GROUP BY
  product_id) är **index-känslig och planerar-bräcklig på SQLite**:
  - Med BEFINTLIGA index: planeraren väljer `idx_csp_chain_product` och **fullskannar hela kedjans 4,34M
    rader** (store-filtret som efter-filter) -> **~17-20s oavsett zon-storlek** (5 butiker = 326 butiker).
  - Med täckande index `(chain, store, product_id, price)` TVINGAT (`INDEXED BY`): seeker bara zonens
    rader -> **5 butiker 163ms, 20 → 449ms, 50 → 850ms, 100 → 1448ms**. Skalar med ZON-storlek, INTE
    total tabell -> bör hålla även vid ~20M (seek per butik är O(log n)). Realistisk 15km-stadszon
    (~10-40 butiker) ≈ 300-700ms.
  - MEN planeraren är opålitlig: efter `ANALYZE` valde den index för små zoner (5-20 = ~115-455ms) men
    **flippade tillbaka till 21s-fullskann vid 50-100 butiker**. Dvs SQLite KAN serva frågan snabbt, men
    bara om vi TVINGAR indexet (`INDEXED BY`) - annars är värsta fallet 21s. + fett index (~18s bygge,
    skriv-amplifiering vid varje crawl-upsert).
- **Rekommendation (uppdaterad efter spiken):** zon-browse är exakt den analytiska, index-känsliga,
  samtidiga-last-fråga där SQLite blir BRÄCKLIG (hint-beroende, planerar-footgun med 21s värsta fall,
  fett index) och Postgres tjänar in sig (pålitlig planerare + bitmap-index på IN-listan utan hint,
  parallell aggregering, ingen en-skrivare-lås under tung crawl+serve, framtida PostGIS för geo). Givet
  att (a) spiken visar bräcklighet snarare än ren omöjlighet, (b) datan är liten och användarlös NU
  (billigaste migrations-läget), och (c) den uttalade api/app/admin-splitten ändå pekar mot en DB-server
  -> **lutar beslutet mot Postgres för det här steget.** Migrera medan det är billigt.
- [ ] **TODO: refaktorera datalagret till SQLAlchemy Core (DB-OBEROENDE brygga) - förutsättning för Postgres.**
  Idag är `api/database/` rå `sqlite3` med SQLite-specifik SQL överallt (`INSERT OR REPLACE`, `json_each`,
  `json_extract`, `PRAGMA`, `lastrowid`) -> INTE portabelt. SQLAlchemy **Core** (SQL-uttryckslagret, inte
  nödvändigtvis hela ORM:en) kompilerar frågor till måldialekten -> SQLite↔Postgres blir i princip ett
  connection-URL-byte. CAVEAT: dialekt-specifika bitar måste hanteras medvetet (SQLite `json_each`/`json_extract`
  vs Postgres `jsonb`-operatorer; `INSERT OR REPLACE` vs `ON CONFLICT`; lös typning) - SQLAlchemy ger
  dialekt-konstruktioner men de skrivs avsiktligt. Stor men avgränsad refaktor; gör den FÖRE Postgres-bytet
  (annars är bytet en omskrivning, inte ett dialekt-byte). Migrationer fortsatt utan Alembic tills dess.

### SQLAlchemy Core -> Postgres-refaktor (actionable plan)
Mål: gör `api/database/` DB-oberoende (SQLAlchemy Core) och flytta till Postgres. Behåll den publika
`database.X`-funktions-API:n EXAKT (callers i routes/services rörs inte) - bara implementationen byts.

**STATUS (2026-06-06): Fas A + Fas B KLARA + VALIDERADE (cutover ej gjord).** Datalagret är
DB-oberoende och bevisat på Postgres. Default (ingen `DATABASE_URL`) = SQLite, oförändrat.

- **Fas A (klar):** Bryggan (`_conn.py`-shim) + alla query-moduler + routes/services konverterade till
  `text()` + namngivna params. Avsteg från "Core-uttryck": default `text()`+named (lägre drift-risk);
  Core/dialekt-grenat bara för upserts (`ON CONFLICT`), JSON-funktioner (helpers `json_get`/`json_is_true`/
  `json_array_len`/`json_each_from` i `_conn.py`) och dynamisk IN (`bindparam(expanding=True)`).
- **Fas B (klar):** `tables.py` (MetaData + Table-objekt, Float/Integer-typval, `server_default`, alla index
  + täckande `idx_csp_cover`). schema.py -> `create_schema`(create_all)/`seed`/`init_db` (ALTER-guards +
  engångsbackfills borttagna). apilog -> engine. json_each-PG-faran fixad (filtrera tomma eans i derived
  table FÖRE casten - PG kör FROM-funktioner före WHERE). `lastrowid` -> `RETURNING id` (psycopg saknar
  lastrowid). PG QueuePool (pool_size=20). `api/migrate_to_pg.py` = bulk-kopia + setval + ANALYZE.
- **Verifierat på Postgres:** migrerade 13,8M rader (~15 min); `test_schemas` grönt; json-tunga/LAG/
  ON CONFLICT/expanding-IN + en-butiks-crawl skarpt; uvicorn-lifespan bootar rent mot populerad PG; och
  **zon-browse-aggregatet väljer `idx_csp_cover` UTAN hint (~1,3s mot SQLites planerar-flip på 21s)** -
  exakt bräckligheten som motiverade bytet är borta.
- **KVAR: cutover (driftbeslut).** Kör `migrate_to_pg` mot tom PG + sätt `DATABASE_URL`. Deploy:
  `docker-compose.pg.yml` (se DOCKER.md "Postgres-deploy" för Unraid-uppsättning: api-container +
  db-container på gemensamt nät; frontend-container valfri SENARE).

**Fas A - SQLAlchemy Core på SQLite (beteende-bevarande, ingen Postgres än):**
1. Lägg `sqlalchemy` i `pyproject.toml`. Skapa en Engine i `api/database/_conn.py` (URL ur env
   `DATABASE_URL`, default `sqlite:///stores.db`). Behåll `get_conn()`-signaturen men låt den ge en
   SQLAlchemy-Connection (eller en tunn wrapper med samma `.execute(...).fetchall()/.fetchone()`/`row["x"]`
   -> använd `Row._mapping`). PRAGMA (WAL/busy_timeout) sätts via en `connect`-event-listener (bara sqlite).
2. Definiera tabellerna som `Table`-objekt (Core MetaData) i ETT ställe (`schema.py`) i st.f. CREATE-TEXT.
   `init_db()` -> `metadata.create_all()`. ALTER-guards ersätts av Core-reflektion / create_all (idempotent).
3. Konvertera modul för modul (ean, products, stores, offers, catalog, store_prices, crawl_runs...): byt
   rå SQL-strängar mot Core-uttryck (`select()/insert()/update()`). Kör `tests/test_schemas.py` + import-test
   efter varje modul (de fångar drift). De TUNGA/dialekt-specifika punkterna att hantera medvetet:
   - **JSON-kolumner** (`tags/raw/hours/native` i stores, `eans` i offers, `error_summary` i crawl_runs):
     idag JSON-i-TEXT + `json.loads/dumps` i Python + `json_each/json_extract` i SQL (t.ex. `ean_stats`
     UNION över `json_each(offers.eans)`, favoriter). SQLAlchemy `JSON`-typ funkar på båda; men
     `json_each`-frågorna måste skrivas om dialekt-medvetet (PG: `jsonb_array_elements_text`).
   - **Upsert:** `INSERT OR REPLACE/IGNORE` + `ON CONFLICT DO UPDATE` (catalog_upsert, upsert_store_prices,
     store_price_volume, category_map-seed) -> `sqlite.insert().on_conflict_do_*` vs `postgresql.insert()...`
     (dialekt-grenat, eller en liten helper som väljer rätt).
   - `lastrowid` -> Core `result.inserted_primary_key`. `AUTOINCREMENT` -> `Integer primary_key`.
   - `LOWER()` (ASCII-only i sqlite) - `list_products` gör redan skiftläge i Python; behåll.
4. apilog-anslutningen (egen `_conn` autocommit) + ev. andra direkt-sqlite3-användningar (sök globalt efter
   `sqlite3.connect`/`get_conn(` i hela `api/`) tas med.
**Fas B - Postgres:**
5. `docker-compose`: lägg en `postgres`-service (avsteg från single-container -> app + db). `DATABASE_URL`
   pekar dit. `psycopg`-driver.
6. Data: regenererbart (crawl/sync) -> enklast TÖM och re-crawla i Postgres i st.f. att migrera rader.
   Alternativt en engångs-dump/load om historiken ska bevaras.
7. Index: lägg det täckande `(chain, store, product_id, price)` för zon-browse - PG väljer det via
   bitmap-scan på IN-listan UTAN `INDEXED BY`-hint (det var SQLite-bräckligheten).
8. Verifiera: `tests/test_schemas.py` mot Postgres + manuell rök-test av tunga vägar (crawl, browse, zon).
**Fas C:** bygg zon-browse-endpoint + geo-first-UI ovanpå (se "Kart-appen / UI-OMTAG").

### Faser (resumerbart)
1. ✅ **Datamodell + mät-sweep KLAR** (2026-06-05): `catalog_store_prices` + `store` i observationer +
   `store_crawl` (queryable/enabled/priority/product_count + denormaliserat namn/ort). `store_measure.py`
   probe:ar båda kedjorna (re-runnable, WAF-skydd) + admin-trigger-kort. **Mätt:** Coop 214/722 frågbara
   (30%, lägre än Steg 0:s ~43%), ICA 1288/1289; ICA `SUM(product_count)` = **18,6M latest-rader**, Coop
   ~2,7M (proxy) -> **~21M latest-rader** + per-butik-historik ovanpå. (ICA-data verifierat äkta per-butik
   även för småsortiment-butiker - ingen queryable-tröskel, urvalstabellen styr i stället.)
2. ✅ **Admin butiksväljare KLAR** (2026-06-05): filterbar markera-flera-tabell (`enabled`-urval, per-rad-
   toggle + bulk "alla frågbara"), denormaliserat namn/ort (list ~2ms). `GET/POST /v1/admin/store-prices/stores`.
3. ✅ **Per-butik-crawler KLAR** (2026-06-05): `store_crawl.py` crawlar enabled+frågbara butiker, **ICA**
   (`*` + empirisk kategori-walk förbi 20k-cappen, ~95%) och **Coop** (department-rötter), parametriserade
   på butik. Skriver catalog_store_prices + per-butik append-on-change-historik. **Adaptiv självtunande
   parallellisering** (AIMD: rampar upp, halverar+cooldown vid WAF; tak = säkerhets-guardrail). Färskhets-
   tröskel (`max_age_hours`, default 20 -> "lägg till + crawla" kör bara nya). Konsol-kort + kategori-flöde.
   Återanvänder katalog-crawlens walk (extraherad). Triggerbar via API/konsol.

   **KVAR I FAS 3 (cutover + parallellt) - beständig TODO (2026-06-05):**
   - [x] **ICA+Coop per-butik-crawl PARALLELLT KLAR** (2026-06-05): per-kedja state (`chains.ica/coop`, var
     sin AIMD) + delad feed; `chain=both` kör samtidigt; konsol-kort visar båda + "Crawla båda"-knapp.
   - [x] **Pensionera ICA/Coop ur master-crawlen KLAR** (2026-06-05): `crawl_all` default = `_MASTER_CHAINS`
     (citygross/willys/hemkop); cron + "crawla alla" rör inte ICA/Coop. `_CRAWLERS` kvar för explicit körning.
     Konsol-kort visar bara nationella. OBS: `catalog_mark_unseen` körs ej längre för ICA/Coop.
   - [x] **Allmänt jämförpris = INTERVALL för ICA/Coop KLAR** (2026-06-05): `recompute_store_aggregates` ->
     `catalog_products.price_min/max/price_stores` (bumpar browse-cachen); `catalog_upsert_metadata` (union,
     bevarar representativpris som FALLBACK tills crawlat); `catalog_browse` visar intervall för ICA/Coop,
     national enkelt pris. Sök matchar grupp-vis (EAN) så omvänt-namngivna kedjor kommer med.
   - [x] **Per-butik-pris-modal KLAR** (2026-06-05): klickbart intervall -> modal med butikspriser GRUPPERADE
     på pris (kort + smal lista, utfällbar butikslista). `GET /v1/products/{ean}/store-prices` (levels/total).
4. **Läs-API:** `/v1/products/{ean}/prices` (stores/near) + admin-status.
5. **Kart-app:** favorit-scope:ad jämförelse + per-produkt "billigast hos favoriter".
6. **Matkasse + geo:** `/v1/compare/basket`, prisvärmekarta.
- **DB-beslut (SQLite/Postgres/ORM):** ~21M latest + växande historik -> SQLite (WAL) räcker att bygga Fas
  3 på (inga omskrivningar); förbered ORM-bryggan, migrera när skriv-kontention/historik-tillväxt/geo/tjänste-
  split slår. Beslut togs EFTER mät-siffran (per plan). Tas upp igen innan Fas 3 vid behov.

### Caveats att rama in
- Per-butik-pris finns bara för FRÅGBARA butiker (Coop ~43%); favoritar/väljer man en ej-frågbar butik finns
  inget hyllpris - kommunicera det.
- Hyllpris != kassapris (samma caveat som idag); medlemspris/erbjudanden ovanpå.
- Real begränsning = crawl-tid/WAF (1600 butiker, rate-limitat) -> per-butik-färskhet dagar/veckor, inte daglig.
