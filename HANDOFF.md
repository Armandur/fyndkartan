# HANDOFF - återuppta här (matbutiker, fokus Steg 6 per-butik-pris)

Snabb orientering för en NY Claude-session (och för Rasmus). Läs i ordning:
`CLAUDE.md` (kodbas-översikt) -> `ROADMAP.md` (full plan/status) -> detta dokument (var vi är NU + nästa steg).

Senast uppdaterad: 2026-06-06.

## Var vi är nu

Steg 6 (per-butik-pris för ICA/Coop, "var är varan/matkassen billigast nära mig / hos mina favoriter")
är **insamling + konsument-läsväg BYGGD**. **Databas-omtaget (SQLite -> Postgres) är KLART + validerat**
(SQLAlchemy Core Fas A+B, se "Nästa steg" 1-2) - kvar är CUTOVER (driftbeslut) och sedan **UI-omtaget**
(zon-browse + geo-first kart-app, "Nästa steg" 3).

Beslut som styr allt (bekräftat med Rasmus): **vi spårar pris i ALLA frågbara ICA/Coop-butiker** (ICA
100%, Coop ~43% - resten ej e-handelsindexerade). Det gamla "per butik = ogenomförbart"-antagandet är
överspelat (se ROADMAP Steg 5 "Avgörande beslut" + Steg 6 "Konsekvenser av beslutet").

## Vad som byggdes i den senaste session-serien

- **Crawl-härdning (efter en massfel-incident):** dispatchern band samtidigheten fel (`cs["active"]`
  uppdaterades för sent -> hela kön startades samtidigt -> PoolTimeout på allt) - nu gate på `len(tasks)`.
  `_is_waf` täcker hela `httpx.TransportError` (breaker skyddar). ICA-token förnyas vid 401 mitt i en butik.
  `last_error` sparar feltyp (transport-fel har tom `str(e)`).
- **Crawl-effektivisering (empiriskt mätt):** ICA storleks-villkorlig (<=20k butiker: bara `*`; >20k: `*` +
  KOMPLETT kategori-union `ica_walk_categories` -> ~99,7% täckning). Sidstorlekar: `ICA_CRAWL_PAGE`=1000,
  `COOP_CRAWL_PAGE`=300, `CG_CRAWL_PAGE`=1000 (Axfood `/c/slug` låst på 100).
- **Overview-perf:** `store_prices_stats` räknade 3,6M-tabellen per laddning (5,8s) -> materialiserat i
  `store_price_volume` (uppdateras i `recompute_store_aggregates`). `_overview_stats()` cachar tunga stats
  (TTL 30s); varm overview ~0ms. Kvar: kallstart ~7s (`ean_stats` UNION-distinct).
- **Crawl-historik (beständig):** `crawl_runs`-tabell (kind store_prices|catalog, rows/changed/errors/
  stores_ok/total/last_error + `error_summary` JSON {feltyp:antal}). Skrivs vid varje körnings slut.
  `GET /v1/admin/crawl-history` + historik-vy i Sortiment-fliken. `last_crawl_runs()` -> durable
  "ändringar sedan senaste" i Steg 6-korten (överlever omstart).
- **Konsument-läsväg (Steg 6-payoffen):** `GET /v1/products/{ean}/prices` scopat - `lat`/`lng`/`radius`
  (billigast nära), `favorites=true`, `stores=`. `database.store_prices_geo` mappar fysisk butik
  (`stores`.lat/lng + native) -> ledger/account -> `catalog_store_prices`. Frontend: per-butik-pris-modalen
  har tabbar Alla/Nära kartans mitt/Mina favoriter (`web/app.js`). Verifierat live (33,95 vs 34,96 kr).

## Öppet beslut: SQLite -> Postgres (lutar JA)

Mätspike (2026-06-06, 4,34M ICA-rader) på den blivande zon-browse-frågan (live-aggregera pris över zonens
butiker): SQLite är **bräcklig** - med befintliga index fullskannar planeraren hela kedjan (~17-20s oavsett
zon); med tvingat täckande index `(chain,store,product_id,price)` blir det 163ms (5 butiker) - 1,4s (100),
MEN planeraren flippar tillbaka till 21s för medelstora zoner (måste tvinga `INDEXED BY`). Slutsats: zon-
browse är den analytiska/samtidiga-last-frågan där Postgres tjänar in sig. Datan är liten + användarlös nu
= billigaste migrations-läget. Detaljer + siffror: ROADMAP "Databasval".

## Nästa steg (sekvens)

1. ✅ **SQLAlchemy Core-refaktor Fas A (2026-06-06): I PRINCIP KLAR.** Bryggan (`_conn.py`-shim) + ALLA
   query-moduler i `api/database/` + alla direkta `get_conn`-anrop i routes/services konverterade till
   `text()` + namngivna params (dialekt-portabelt). Avsteg från planens "Core-uttryck": default blev
   `text()`+named (lägre risk), Core/dialekt-grenat bara för upserts (`ON CONFLICT`), JSON-funktioner
   (helpers i `_conn.py`) och dynamisk IN (`bindparam(expanding=True)`). **KVAR (Fas B, kräver Postgres):**
   `schema.py` (init_db DDL -> Table-objekt + create_all) och `apilog.py` (autocommit-logger) är fortf.
   SQLite-only. **OBS PG-fara dokumenterad i ROADMAP:** `json_each_from`/`ean_stats` + schema.py offer_eans-
   backfill kraschar på `eans=''` i PG (WHERE skyddar ej casten) - fixa när PG är uppe. Detaljer + de exakta
   kvarvarande punkterna: ROADMAP "SQLAlchemy Core -> Postgres-refaktor" (STATUS-blocket).
2. ✅ **Postgres Fas B (2026-06-06): KLAR + VALIDERAD (ej cutover än).** `tables.py` (Table-objekt +
   `create_all`, Float/Integer-typval, täckande `idx_csp_cover`), schema.py omskriven (`create_schema`/
   `seed`/`init_db`, ALTER-guards borttagna), apilog -> engine, json_each-PG-faran fixad, `lastrowid` ->
   `RETURNING id` (psycopg saknar lastrowid), PG QueuePool. `api/migrate_to_pg.py` = engångs bulk-kopia +
   setval + ANALYZE. **Verifierat:** migrerade 13,8M rader till PG (~15 min), `test_schemas` grönt mot PG,
   json-tunga/LAG/ON CONFLICT/expanding-IN + en-butiks-crawl körda skarpt, uvicorn-lifespan bootar rent mot
   populerad PG, och **zon-browse-aggregatet använder `idx_csp_cover` UTAN hint (~1,3s mot SQLites 21s-footgun)
   - migreringens vinst bekräftad**. Default (ingen `DATABASE_URL`) = SQLite, oförändrat. Deploy:
   `docker-compose.pg.yml` + DOCKER.md "Postgres-deploy".
   - **KVAR: CUTOVER-beslut (ditt).** Att flippa drift till PG: kör `migrate_to_pg` mot tom PG, sätt
     `DATABASE_URL` på api-containern. En test-PG kör i docker-containern `matbutiker-pg` (port 5433) med
     migrerad data + 2 test-crawlade butiker - kasta den och migrera färskt vid riktig cutover.
3. **Zon-browse + geo-first UI** (ROADMAP "Kart-appen / UI-OMTAG"): kart-pil + radie-cirkel + "Bläddra
   zonens sortiment". Semantik bekräftad: union (varor i MINST EN zon-butik), per vara billigast-i-zonen +
   intervall + antal butiker, sorterbart. Bygg zon-aggregat-frågan ovanpå `idx_csp_cover`.

### Datamigrering SQLite -> Postgres (en gång, Fas B)
Datan delar sig: **färska snapshots** (catalog_store_prices ~6M, catalog_products, offers, stores, ean_cache,
product_info, bilder) = återskapbara via crawl/sync; **tidsserie-historik** (catalog_price_observations
~6,07M, offer_observations ~393k, product_info_observations ~24k) = byggs upp över tid, EJ återskapbar.
Slutsats: gör en generisk tabell-för-tabell bulk-kopia av ALLT (inte selektiv re-crawl) så historiken följer
med + ingen kall period. JSON-kolumner: behåll som TEXT (ingen jsonb-migrering i denna refaktor).

## Drift / så kör man

- **DEV KÖR MOT POSTGRES sedan 2026-06-06** (cutover gjord). `DATABASE_URL` i repo-rotens `.env`
  (`postgresql+psycopg://fyndkartan:fyndkartan@localhost:5433/fyndkartan`) -> servern + alla
  `.venv/bin/python`-anrop läser den via `config.load_dotenv`. PG-container: `matbutiker-pg-dev`
  (durabel, volym `matbutiker_pgdev`, port 5433). Crawl/sync/sweep skriver nu till PG.
  **`stores.db` (SQLite) är frusen pre-cutover-snapshot** - uppdateras inte längre; behåll som backup.
  Vill man tillfälligt köra mot SQLite igen: kommentera bort DATABASE_URL i `.env`.
- Dev-server (Claude äger start/stopp i detta projekt): från repo-roten
  `​.venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8700 > dev.log 2>&1` (bakgrund). Reset =
  döda (`ps aux | grep api.main`) + starta om. Ingen `--reload` -> starta om efter kodändring.
  Bekräfta vilken DB: `.venv/bin/python -c "import api.database as db; print(db.dialect_name())"`.
- Verifiera efter ändring: `.venv/bin/python -c "from api.main import app; print('OK')"` +
  `.venv/bin/python tests/test_schemas.py` (kör nu mot PG via .env).
- Konsol: `/admin` (admin-auth, creds i `.env`). Kart-app: `/` (app-användar-auth, inloggnings-vägg).

## KÄND HARNESS-BUGG (inte i koden)

Vissa Bash-kommandon failar med `undefined is not an object (evaluating 'H.replace')` - ett klient-fel i
Claude Code-harnessen (output-processering), INTE i repot. Intermittent. Workarounds: `bash -lc '... | cat'`,
skriv output till fil + läs med Read-verktyget, `git commit -F <fil>` i st.f. heredoc, enkla i st.f.
sammansatta kommandon, eller bara retry. Riktig fix: uppdatera Claude Code / rapportera uppströms.

## Nyckelfiler (Steg 6)

- `api/store_crawl.py` - per-butik-rotations-crawl (AIMD, härdad), skriver crawl_runs.
- `api/catalog_crawl.py` - delad `_ica_fetch_store`/`_coop_fetch_store` (storleks-villkorlig ICA), master-crawl.
- `api/database/store_prices.py` - `catalog_store_prices`, `store_crawl`, `store_prices_for_ean`,
  `store_prices_geo` (geo/favorit-scope), `recompute_store_aggregates`, `store_price_volume`.
- `api/database/crawl_runs.py` - körnings-historik.
- `api/routes/products.py` - `/v1/products/{ean}/store-prices` (alla, grupperat) + `/prices` (scopat).
- `web/app.js` - konsument-modal med scope-tabbar. `web/admin.js` - konsol (crawl-kort, historik, urval).
