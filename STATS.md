# STATS.md - brief för statistik-/analysappen

Det här dokumentet är till för en **ny session** som ska bygga en fristående statistik-/analysapp
ovanpå matbutiker-projektets data. Läs det här först, läs sedan `CLAUDE.md` (datamodell + "Kända
datakälle-fakta") och `api/database/tables.py` (exakt schema) vid behov.

Skrivet 2026-06-07. Datan ligger i **Postgres** (matbutiker gick över från SQLite, se `HANDOFF.md`).

## Mål

En app som visualiserar och analyserar svensk dagligvarudata över tid och geografi:
prisutveckling/inflation, prisjämförelse mellan kedjor, sortiment, ursprung, och **geografiska
mönster** (stad/landsbygd, storstad, län, kommun) - ev. med OSM-karta och SCB-statistik.
Funktionsidéer längst ner. Den nya sessionen får gärna **föreslå fler funktioner**.

## Hårda begränsningar (läs noga)

1. **Egen mapp/eget repo.** Appen ska vara så separerad som möjligt - den delar bara **Postgres
   (läs-only)** med matbutiker. Lägg den i en egen katalog (t.ex. `~/workspace/matstatistik`),
   inte inuti `api/`.
2. **Peta inte på matbutiker-API:t.** Inga nya endpoints, inga ändringar i `api/`. Tunga aggregat
   ska gå **direkt mot Postgres** (det var hela poängen med Postgres-flytten). API:ts `/v1`-yta är
   per-produkt och saknar bulk/aggregat - oanvändbar för analys.
3. **Skriv aldrig till databasen.** Använd en **read-only Postgres-roll** (bara SELECT). Behöver
   appen egna tabeller (t.ex. SCB-enrichment) - lägg dem i en **separat databas eller eget schema**,
   inte i matbutikers tabeller.
4. **Om ett starkt behov av att ändra matbutiker uppstår** (t.ex. materialisera en kanonisk kolumn
   eller lägga en endpoint) - **fråga Rasmus först**, gör det inte på eget bevåg.

## Anslutning

Dev-Postgres kör i container `matbutiker-pg-dev`:
- URL (full åtkomst, matbutiker använder den): `postgresql+psycopg://fyndkartan:fyndkartan@localhost:5433/fyndkartan`
- **Skapa en read-only roll för statistikappen** (kör som superuser/fyndkartan en gång):
  ```sql
  CREATE ROLE matstat LOGIN PASSWORD '<välj>';
  GRANT CONNECT ON DATABASE fyndkartan TO matstat;
  GRANT USAGE ON SCHEMA public TO matstat;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO matstat;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO matstat;
  ```
  Statistikappen använder sedan `postgresql+psycopg://matstat:<pw>@localhost:5433/fyndkartan`.

## Datamodell - de analys-relevanta tabellerna

Exakta kolumner nedan (verifierat 2026-06-07). Radantal ger en känsla för skala.

### Tidsserier (byggs upp över tid, EJ återskapbara - guldet för analys)

- **`catalog_price_observations`** (~20,4M rader) - HYLLPRIS-historik.
  `id, chain, product_id, ean, price, comparison_value, comparison_unit, observed_at, store`
  - **APPEND-ON-CHANGE:** en rad skrivs bara NÄR priset (eller jämförvärdet) ändrats sedan förra
    observationen - inte regelbundna snapshots. För en tidsserie måste du LAGga (window) per
    `(chain, product_id[, store])` och förstå att "platta" perioder saknar rader.
  - **`store`-kolumnen är AVGÖRANDE:** ICA/Coop är **butiksspecifika** (`store` = ledger/account, en
    rad per butik). Willys/Hemköp/City Gross är **nationella** (`store` NULL, ett pris för hela landet).
    Blanda inte ihop - ett nationellt prisindex måste vikta/hantera detta korrekt.
- **`offer_observations`** (~395k rader) - ERBJUDANDE-historik (reor, per butik).
  `id, chain, store_id, offer_id, ean, name, price, comparison_value, comparison_unit, valid_to,
  observed_at, savings, member_price`
  - Också append-on-change, per butik. `savings` + `member_price` låter ordinarie pris härledas.
    Fyndspårning -> luckor när varan inte varit nedsatt.
  - **OBS multibuy:** i `offers`/`offer_observations` är `price` TOTALEN för flerköp ("3 för 100" ->
    price=100), inte styckpris. Räkna styckpris via `price_text`/antal (matbutiker gör det i
    `matching._eff_price`). Hyllpriserna (catalog_*) är däremot per styck.
- **`product_info_observations`** (~24k rader) - innehålls-historik (ingrediens/näring/ursprung).
  `id, ean, source, ingredients, nutrition, origin, observed_at`

### Snapshots (återskapbara via crawl - "nuläget")

- **`catalog_store_prices`** (~20,3M rader) - senaste hyllpris **per butik** (ICA/Coop).
  `chain, product_id, store, ean, price, comparison_value, comparison_unit, available, first_seen, last_seen`
  PK `(chain, product_id, store)`. Täckande index `idx_csp_cover (chain, store, product_id, price)`.
- **`catalog_products`** (~190k rader) - katalog per kedja (sortiment + representativt/nationellt
  hyllpris + materialiserat per-butik-intervall).
  `chain, product_id, ean, name, brand, image, origin, price, comparison_value, comparison_unit,
  package_size, package_value, package_unit, category_raw, available, first_seen, last_seen,
  fetched_at, store, price_min, price_max, price_stores`
  - `store` NULL = nationellt pris (Axfood/CG); satt = butiksscopat (ICA/Coop, se CLAUDE.md
    "Coop OCH ICA: pris + sortiment är BUTIKSSPECIFIKT"). `price_min/max/stores` = per-butik-intervall.
- **`offers`** (~387k rader) - cachade erbjudanden per butik (nuläge).
  `chain, store_id, offer_id, name, brand, package, price, price_text, comparison_price,
  comparison_value, comparison_unit, category_raw, category_id, mechanic_type, valid_to, eans,
  image, member_price, fetched_at, savings`. `eans` = JSON-array (Axfood: tom inline, EAN via `ean_cache`).

### Dimensioner / hjälptabeller

- **`stores`** (~2700 rader) - alla butiker, alla 6 kedjor. `chain, store_id, name, brand, street,
  postal_code, city, lat, lng, phone, email, ..., tags(JSON), native(JSON), hours(JSON)`.
  - **Geo-nyckeln:** `lat`/`lng` (0,0 = saknad). `native` (JSON) bär kedjans sekundär-id:n -
    ICA `accountNumber`, Coop `ledgerAccountNumber` - som mappar fysisk butik -> `store`-kolumnen i
    catalog_store_prices/observations. (matbutiker gör mappningen i `database.store_prices_geo` /
    `zone.zone_stores` - läs den om du behöver koppla observationer till fysiska butiker/koordinater.)
- **`store_crawl`** (~2000 rader) - vilka ICA/Coop-butiker som prisspåras (`queryable`, `enabled`,
  `last_crawled`, denormaliserat `name`/`city`).
- **Mappnings-tabeller** (för normalisering, se nedan): `category_map`, `manufacturer_map`,
  `tag_map`, `ean_cache` (Axfood-kod->EAN/kategori/ursprung), `product_matches` (manuella
  private-label-paringar).

## Kritiska semantik-fallgropar (läs innan du skriver SQL)

1. **Observationer = append-on-change, inte snapshots.** (Se ovan.) Tidsserier kräver window-funktioner
   (LAG/`generate_series` för att fylla luckor).
2. **ICA/Coop butiksspecifikt vs Axfood/CG nationellt** (`store` NULL eller ej). Ett "nationellt
   matpris-index" måste bestämma viktning (per butik? per kedja? befolkningsviktat via SCB?).
3. **Normalisering är derive-at-read** (se nästa sektion) - observations-/katalograder bär `category_raw`
   och rå `brand`, INTE kanonisk kategori/tillverkare. Du måste applicera mappningen själv.
4. **EAN-normalisering:** kedjorna lagrar EAN olika (8/12/13/14-siffrigt, GTIN-14 nollpaddat, 2-prefix
   = butiksintern viktvara). Kanonisera till 13 och rejekta 2-prefix innan cross-chain-join (annars
   falska matchningar). Logik: `api/matching.py normalize_ean`.
5. **Multibuy i offers** = `price` är totalen, inte styckpris (se ovan).
6. **`comparison_value`/`comparison_unit`** (jämförpris kr/kg|l|st) är bästa cross-chain-jämförelsen -
   men enheter skrivs olika ("liter"/"l", "kg utan spad"); kanonisera (`matching._norm_unit`).
7. **Coop/ICA prisscope är EN fast butik i katalogen** (`catalog_products.store`): Coop 251300, ICA
   1003647 - katalogradens nationella-känns-pris är egentligen den butikens. Per-butik-bredden ligger
   i catalog_store_prices/observations.

## Normaliserings-strategi (kategori/tillverkare/EAN/diet/land)

matbutiker normaliserar **vid läsning** (derive-at-read) i `api/`-modulerna och returnerar redan
normaliserade värden i sina API-svar - MEN det går inte att bulk-analysera via API:t, och de
kanoniska värdena är inte lagrade på raderna. Statistikappen läser PG direkt och måste själv mappa.
Två vägar:

- **(a) Importera `api/`:s rena hjälpfunktioner som bibliotek** (bäst trohet, mild koppling - inte
  "att peta på API:t"): `api.matching.normalize_ean`/`_norm_unit`, `api.categories.category_for`,
  `api.manufacturers.canonical`/`manufacturer_key`, `api.countries`, `api.diet.classify_diet`.
  De är i stort sett rena funktioner + en map som laddas ur DB (`categories.set_map(load_category_map())`
  m.fl. - se `api/main.py` lifespan). Om du går den här vägen: pip-installera/peka på matbutiker-paketet
  read-only och anropa funktionerna; ändra dem inte.
- **(b) Läsa mappnings-tabellerna ur PG och applicera dem** (helt frikopplat): `category_map`
  (råkategori -> kanonisk), `manufacturer_map`, `ean_cache`. Kategori-deriven är dock mer än en platt
  map (majoritetsröstning över kedjor i `catalog._cat_canonical`, detalj-kategori-override) - en enklare
  map-lookup räcker oftast för statistik men kan avvika något.

Välj efter hur exakt grupperingen måste vara. **Om du gång på gång behöver ett visst normaliserat
aggregat** -> det är "starkt behov"-fallet: fråga Rasmus om att materialisera en kanonisk kolumn på
`catalog_products` / en analys-vy, eller en endpoint. Gör det inte i förväg.

## Geo / SCB / urbanitet (kart-lagret)

- **PostGIS rekommenderas starkt.** För "butik -> tätort/kommun/län" och stad/landsbygd-klassning är
  point-in-polygon mot SCB:s gränser det naturliga. Aktivera PostGIS-extensionen på Postgres-instansen
  (kräver superuser en gång: `CREATE EXTENSION postgis;`) - fråga Rasmus innan, det är en ändring på
  den delade instansen. Alternativt point-in-polygon i Python (shapely) mot GeoJSON, ingen DB-ändring.
- **SCB-data att hämta in** (engångs-enrichment, butikslägen är stabila):
  - Tätortsavgränsning + folkmängd per tätort (SCB) -> stad/landsbygd, storstad/mellanstad/småort.
  - Kommun- och länskoder/gränser (SCB/Lantmäteriet GeoJSON).
  - Ev. DeSO/RegSO-områden för finkornig analys.
- **Modell:** bygg en egen `store_geo`-tabell (i statistikappens EGEN databas/schema) som mappar
  `(chain, store_id)` -> tätort/kommun/län/urbanitetsklass + folkmängd. Joina mot matbutikers `stores`
  via `lat`/`lng`. Skriv INTE in i matbutikers `stores`.
- **Karta:** Leaflet + OSM-tiles (samma som matbutikers kart-app, `web/index.html` visar uppsättningen).

## Funktionsidéer (utgångspunkt - föreslå gärna fler)

Från matbutikers ROADMAP "Statistik-app":
- **Matpris-index över tid:** en kurerad varukorg (EAN-lista) prissatt över kedjor och över tid ->
  vilken kedja är billigast på korgen (nationellt vs butikslokalt), hur utvecklas totalpriset.
  Hantera saknad vara hos en kedja (substitut/exkludering).
- **Prisutveckling/inflation** ur observationerna: per kategori/kedja, störst prisökningar,
  "shrinkflation" (jämförpris upp men styckpris stilla).
- **Erbjudande-intensitet per kedja:** rea-frekvens, snittbesparing, djup - vem rear mest/djupast.
- **Private label vs branded prisgap**; **medlemspris-effekt** (klubbpris-rabatt per kedja);
  **ursprung/svenskandel** per kedja/kategori; **jämförpris-anomalier** (samma EAN, stor skillnad).
- **Sortimentsbredd/täckning** per kedja per kategori (vem har bredast utbud, vilka luckor).
- **Geografiska skillnader + urbanitet:** butikslokala priser vs nationellt -> var avviker priser
  geografiskt; "är landsbygdsbutiker dyrare?", prisspridning per region/län, kedjornas täckning.
  Bygger på SCB-lagret ovan.

## Tech-förslag

- Rasmus stack: **Python 3.12 + FastAPI + Jinja2 + vanilla JS + Leaflet** (se matbutikers `CLAUDE.md`)
  - en separat sådan app som läser PG read-only passar.
- För **utforskande analys utan att bygga eget**: **Metabase eller Grafana** pekat på samma Postgres
  (read-only roll) är nästan gratis och bra för att hitta vad som är värt att bygga en egen vy för.
  Överväg det först.
- Tunga aggregat: överväg **materialiserade vyer i statistikappens egen DB** (eller cachade resultat)
  - att räkna inflation över 20M observationer per sidladdning är inte gratis.

## Arbetsflöde för den nya sessionen

- Läs `CLAUDE.md` (matbutiker) för datakälle-fakta och designbeslut. `api/database/tables.py` för schemat.
  `api/database/store_prices.py` + `zone.py` för hur fysisk butik <-> pris-scope mappas.
- Verifiera mot PG read-only; skriv aldrig till matbutikers tabeller.
- **GDPR:** bara butiks-/produktdata, inga personuppgifter -> okomplicerat. Spara inte annat.
- Starta/stoppa inte matbutikers dev-server; din app är en egen process på en egen port.
- När du är osäker på om något kräver en ändring i matbutiker (endpoint/kolumn/extension) - **fråga Rasmus**.
