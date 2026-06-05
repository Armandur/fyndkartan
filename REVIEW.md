# REVIEW.md - Datalager- och strukturöversyn

Genomförd 2026-06-03. Fokuserad genomlysning av `api/` + `web/` inför Steg 5 (fulla sortiment),
för att städa grunden innan ett ~200k-raders subsystem läggs ovanpå. Fynden är rangordnade efter
värde/risk. **Åtgärderna beslutas efter att fynden lagts fram - inget är genomfört ännu.**

## Nuläge (mätt)

| Fil | Rader | Noteringar |
|-----|------:|------------|
| `api/database.py` | 1638 | 96 funktioner, 85 `get_conn()`-anrop, 6 `json_each` |
| `api/main.py` | 1294 | 58 routes, 77 funktioner (app + alla routes + offers-helpers + sweep) |
| `web/admin.html` | 1229 | en fil (konsol-UI + inline-script) |
| `web/app.js` | 1145 | en fil (kart-app) |
| `api/details.py` | 519 | gränsfall mot 400-500-regeln |
| Övriga `api/*.py` | <400 | inom riktlinjen |

Projektregel: filer <400-500 rader. Bara `main.py`, `database.py` (och gränsfall `details.py`,
samt frontend-filerna) bryter mot den - problemet är koncentrerat, inte spritt.

---

## Fynd 1 - Normalisera offer→EAN till en indexerad tabell (HÖG värde, LÅG risk, ENABLER för Steg 5)

**Problem.** EAN-uppslag mot offers görs genom full-scan + korrelerad `json_each` på 382k rader
(~300 ms/anrop), OCH Axfood-specialfallet (offers bär `eans=[]`, EAN ligger i `ean_cache` per kod)
är duplicerat:
- `SELECT code FROM ean_cache WHERE ean=?` reverse-resolve i både `price_history` (rad ~645) och
  `offers_for_eans` (rad ~691), plus en variant i `stores_with_offer`.
- `'willys','hemkop'`-literalen hårdkodad 8 gånger i `database.py` (Axfood-specialfallet utspritt).
- Samma `json_each(o.eans)`-mönster i `stores_with_offer`, `offers_for_eans`, `list_products`,
  `get_store_offers`.

**Fix.** En normaliserad tabell `offer_eans(chain, store_id, offer_id, ean)` (indexerad på `ean`),
fylld vid `replace_store_offers` - där INLINE-EAN (ICA/Coop/CG) OCH Axfood-resolvade EAN (ur
`ean_cache`) skrivs till SAMMA tabell. Då blir alla uppslag ett enkelt indexerat join, Axfood-
specialfallet försvinner ur läsvägen, och `json_each`-scannen ersätts.

**Vinst.** Snabbare `stores_with_offer`/`offers_for_eans`/`price_history`/`list_products`; borttagen
duplicering; och EXAKT det index-mönster Steg 5:s `catalog_products`-läsning återanvänder. Detta är
den enskilt mest värdefulla grund-investeringen och bör göras först.

**Risk.** Låg. Additiv tabell (init_db ALTER-guard), fylls write-path; läsvägarna byts en i taget
med befintliga tester som skydd. `ean_cache` (kod->EAN) behålls som källa för Axfood-resolve.

---

## Fynd 2 - Bryt ut offers/sweep ur `main.py` (HÖG värde, MEDEL risk)

**Problem.** `main.py` (1294 rader) rymmer FastAPI-appen + 58 routes + hela offers-maskineriet +
sweep-logiken i en fil - långt över projektregeln och svårt att navigera.

**Fix.** Ny `api/offers.py` med: `_fetch_offers_for`, `_offers_fresh`/`_offers_expired`,
`_ensure_offers`, `sweep_offers`/`_sweep_chain`/`_sweep_one_store`, `SWEEP_STATE`,
`SWEEP_ERROR_SAMPLE`, `SUPPORTED_OFFER_CHAINS`/`COMPARE_CHAINS`, `OFFERS_TTL`/`OFFERS_MIN_REFRESH`.
`main.py` importerar dem. Ev. senare: route-grupper till `api/routes/` (admin, products, stores...).

**Risk.** Medel - importordning/cykler. `main.py` importerar `sync`; `offers.py` skulle importera
adaptrar + `database` + `config` (inte `main`), och `main` importerar `offers`. Sweep-schemaläggningen
i lifespan refererar `sweep_offers` -> flytta importen. Verifiera med importtest + schema-test + dev.log.

---

## Fynd 3 - `database.py` 1638 rader: dela per domän (MEDEL värde, MEDEL risk)

**Problem.** 96 funktioner i en fil, blandar stores/offers/ean/tags/categories/brands/prishistorik.
85 `get_conn()`-anrop (en connection per funktion, öppna/stäng) - OK för SQLite men värt att notera.

**Fix.** Dela till ett `api/database/`-paket (eller tydliga sektioner): `stores`, `offers`,
`ean`, `catalog` (Steg 5), `history`, `meta`. Gör Steg 5:s tillägg renare. Connection-mönstret kan
lämnas (fungerar) eller centraliseras om profilering visar behov.

**Risk.** Medel - många importställen. Görs säkrast efter Fynd 1 (då offers-läsvägarna redan rörts).

---

## Fynd 4 - Testtäckning runt de tyngsta läs-funktionerna (MEDEL värde, LÅG risk)

**Problem.** Enda testet är `tests/test_schemas.py` (drift mot Pydantic). Inga tester på den
affärslogik Steg 5 lutar sig mot.

**Fix.** Lägg riktade tester: `stores_with_offer`/`offers_for_eans` (inkl. Axfood reverse-resolve),
`price_history` (kollaps + Axfood), `archive_offers` (dedup per prisändring), `normalize_ean`
(GTIN-14->13, 2-prefix-reject), `category_from_name` (frukt/grönt-fallback). Görs INNAN Steg 5 så
refaktoreringen i Fynd 1-3 har skyddsnät.

**Risk.** Låg (rena tilllägg). Kräver en populerad `stores.db` (som test_schemas redan antar).

---

## Fynd 5 - Frontend i enfilsmoduler (LÅG värde, LÅG risk)

`web/app.js` (1145) och `web/admin.html` (1229, inline-script) är stora enfiler. Ingen bundler
(medvetet). Kan delas i flera `<script>`-laddade moduler (t.ex. `app.map.js`, `app.products.js`,
`app.offers.js`; konsolens script till `admin.js`). Lägre prioritet - påverkar inte Steg 5:s
datagrund, mest underhållbarhet.

---

## Fynd 6 - Axfood-specialfallet som magisk literal (LÅG, delvis subsumerad av Fynd 1)

`'willys','hemkop'` hårdkodat 8 ggr i `database.py`. Inför `config.AXFOOD_CHAINS` och använd den.
Försvinner i läsvägarna när Fynd 1 (offer_eans) normaliserar bort Axfood-specialfallet; kvarstår
bara i write-path (`archive_offers`/resolve).

---

## Rekommenderad åtgärdsordning

1. **Fynd 1** (offer_eans-tabell) - enabler + prestanda, låg risk. Gör först.
2. **Fynd 4** (tester runt tunga läsfunktioner) - skyddsnät INNAN mer refaktorering.
3. **Fynd 2** (bryt ut `offers.py`) - struktur.
4. **Fynd 3** (dela `database.py`) - om tid; annars i samband med Steg 5.
5. **Fynd 5/6** - opportunistiskt.

Därefter: **Steg 5 (fulla sortiment)** på en ren grund i stället för en ansträngd.

## Vad som INTE är problem (medvetet bra)

- Derive-at-read (kategorier/taggar/brand/deal_type) är konsekvent och rätt.
- Schema-som-kontrakt (`schemas.py` document-only + drift-test + `OWN_APIS`-derivering) är en stark
  modell - håll den när nya endpoints läggs.
- Modulariteten i `api/` är i grunden god (adapters/, sync/, catalog/, details/, brands/ etc.);
  det är bara `main.py` + `database.py` som svällt.
- Rate-limiting/back-off/circuit-breaker-mönstret i sweepen är återanvändbart rakt av för Steg 5.

---

## Genomförande-status (2026-06-03)

| Fynd | Status | Resultat |
|------|--------|----------|
| **1** Normalisera offer→EAN | ✅ Klar (`c1b3b8b`) | `offer_eans`-tabell; `stores_with_offer` ~350ms → 1-10ms |
| **4** Tester runt tunga läsfunktioner | ✅ Klar (`1e79d27`) | `tests/test_logic.py` |
| **2** Bryt ut `offers.py` ur main.py | ✅ Klar (`548afca`) | main.py 1294 → 1138 rader |
| **6** `config.AXFOOD_CHAINS` | ✅ Klar (`005f5a0`) | (delvis redan i Fynd 1) |
| **3** Dela `database.py` | ✅ Klar (`9a5714c`) | 1655-radersfil → paket, störst submodul ~517 rader |
| **5** Frontend-split | 🟡 Delvis (`ec5bd21`) | admin.html 1229 → 141 + `admin.js`; **app.js-split återstår** |

**Kvar i Fynd 5:** app.js (1145 rader) är inte uppdelad - no-bundler global scope + laddningsordning
gör en multifils-split känslig och den kräver webbläsartest (kan ej köras headless). Görs som en
fokuserad uppgift där resultatet kan verifieras i webbläsare, annars lämnas app.js som appens enda
kärnfil. Påverkar INTE Steg 5:s datagrund.

**Notera:** main.py är 1138 rader (fortfarande >600). Fynd 2 tog ut offers-domänen; ev. nästa steg
är route-grupper till `api/routes/` - lämnat som framtida (ingen brådska, blockerar inget).

Alla genomförda fynd verifierade: import + `test_schemas` + `test_logic` gröna, uppstart (init_db) +
EAN/kategori-warming utan fel, schemaläggare aktiva, healthz 200.

---

## Uppdatering 2026-06-05 - efter en stor feature-runda

Sedan jun-3-översynen har mycket byggts (piggyback-product_info, partial-uppgradering, innehålls-
historik, butiks-tagging av Coop/ICA-hyllpris, prisändrings-logg + produktmodaler i konsolen,
kost-filter, tillverkar-normalisering). Det har växt filerna igen och tillfört ett par nya nyanser.

### Nuläge (mätt 2026-06-05)
| Fil | Rader (jun 3 -> nu) | Not |
|-----|------|-----|
| `web/admin.js` | (split-resultat) -> **1663** | många nya flikar/funktioner |
| `web/app.js` | 1145 -> **1630** | modaler, grafer, filter |
| `api/main.py` | 1138 -> **1446** | nya endpoints (manufacturers/partials/price-changes/admin-product) |
| `api/database/offers.py` | ~517 -> **594** | origin_codes, _norm_eans, piggyback |
| `api/catalog_crawl.py` | -> **538** | piggyback-hooks |
| `api/details.py` | **517** | gränsfall (diet utbruten till `api/diet.py` lättade den) |

### Nya fynd

**A. (P1) `main.py` route-grupper är nu MER motiverat.** 1446 rader, ~70 routes. Bryt till `api/routes/`
i små pass (admin/, products/, compare/, auth/) - lifespan/middleware/app-setup kvar i main. Görs FÖRE
Steg 6 (som lägger på fler endpoints).
- **Pass 1 ✅** (2026-06-05): vokabulär-/normaliseringsadmin (`/v1/admin/categories|manufacturers`,
  `/v1/tags*`, `/v1/providers*`) utbruten till `api/routes/admin_vocab.py` (router utan prefix, fulla
  paths -> byte-identiska URL:er, `require_admin` på router-nivå). main.py 1446 -> 1293 rader. Verifierat
  via route-path-set-snapshot (90 routes, oförändrat) + utökat `test_auth` (probar nu `/v1/tags` +
  `/v1/providers`). Helt självständig (rör bara `database`/`categories`/`manufacturers`/`tags`) - inget
  `require_consumer`/STATE-beroende, så ingen cirkulär import. Nästa pass kräver att `require_consumer`
  flyttas till `api/deps.py` innан konsument-routerna (products/compare/stores) kan brytas ut.

**B. (P1) Testtäckning kraftigt utökad.** `tests/test_reads.py` (catalog_browse/price_changes/
price_history/diet/manufacturers/origins), `tests/test_compare.py` (✅ `build_comparisons`: min_chains/
min_stores-grindar, per-butik-dedup, unit_price-vs-price, manual_groups-merge - syntetiska entries,
ingen DB), `tests/test_auth.py` (✅ gating: 11 gatade endpoints -> 401/403, öppna -> 200, ogiltig
X-API-Key avvisas). (`test_logic.py` täcker normalize_ean/archive/stores_with_offer/category_from_name/
price_history_axfood.) **Kvar otestat:** `_ensure_offers`/sweep-cykeln, catalog_crawl per-kedje-parsers.

**C. (P1) Duplicerat batch-uppslag mot `product_info`.** `get_product_categories`, `get_product_origins`,
`get_product_diets`, `product_info_fresh_set`, `partial_info_counts` gör snarlika `SELECT ...
json_extract(data,...) FROM product_info`-frågor. En delad helper (loader per fält) minskar dubblering
+ ger en plats att optimera.

**D. (P2) `get_product_diets()` klassificerar HELA product_info (~11k) per diet-filtrerad browse.**
Ocachat, ~50-100ms/anrop. Cacha modulnivå (likt `_browse_groups`, invalidera vid product_info-ändring)
om filtret används interaktivt.

**E. (P2) `catalog_summary` (kategori-räknarna) speglar inte diet-filtret** (only_offers/favoriter gör).
Inkonsekvent UX - finputs.

**F. (P2) `catalog_price_observations` saknar `store`-kolumn.** Hyllpris-historiken är implicit enkel-
butik. Inför Steg 6 (per-butik) behövs `store` här - planerat i Steg 6-datamodellen.

**G. (noterat) Butiksscoping-täckningslucka:** Coop-produktinfo/bilder hårdkodade till 251300 (produkter
som bara finns i andra butiker saknar Coop-info/bild). Dokumenterat i Kända datakälle-fakta + Steg 6.

### Rekommenderad ordning härnäst
1. ✅ (B) `build_comparisons`- + auth-tester GJORT (`test_compare.py` + `test_auth.py`).
2. (A) `main.py` -> `api/routes/` i små pass (sänker risk inför Steg 6). **Pågår:** pass 1 (vokabulär-
   admin) ✅ klart. Pass 2: flytta `require_consumer` -> `api/deps.py`, bryt sedan ut konsument-routerna.
3. (C/D) delad product_info-helper + cacha diet-mappen vid behov.
4. Resten (E/F/G) inom respektive feature / Steg 6.
