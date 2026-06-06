# HANDOFF - återuppta här (matbutiker, fokus Steg 6 per-butik-pris)

Snabb orientering för en NY Claude-session (och för Rasmus). Läs i ordning:
`CLAUDE.md` (kodbas-översikt) -> `ROADMAP.md` (full plan/status) -> detta dokument (var vi är NU + nästa steg).

Senast uppdaterad: 2026-06-06.

## Var vi är nu

Steg 6 (per-butik-pris för ICA/Coop, "var är varan/matkassen billigast nära mig / hos mina favoriter")
är **insamling + konsument-läsväg BYGGD**; nästa stora sak är ett **databas-/UI-omtag**.

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

1. **SQLAlchemy Core-refaktor** av `api/database/` (DB-oberoende brygga) - actionable plan i ROADMAP
   "SQLAlchemy Core -> Postgres-refaktor". Behåll `database.X`-API:n exakt; byt implementationen modulvis;
   kör `tests/test_schemas.py` + import efter varje modul.
2. **Postgres** (compose-service + `DATABASE_URL` + `psycopg`), re-crawla data, lägg det täckande indexet.
3. **Zon-browse + geo-first UI** (ROADMAP "Kart-appen / UI-OMTAG"): kart-pil + radie-cirkel + "Bläddra
   zonens sortiment". Semantik bekräftad: union (varor i MINST EN zon-butik), per vara billigast-i-zonen +
   intervall + antal butiker, sorterbart.

## Drift / så kör man

- Dev-server (Claude äger start/stopp i detta projekt): från repo-roten
  `​.venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8700 > dev.log 2>&1` (bakgrund). Reset =
  döda (`ps aux | grep api.main`) + starta om. Ingen `--reload` -> starta om efter kodändring.
- Verifiera efter ändring: `.venv/bin/python -c "from api.main import app; print('OK')"` +
  `.venv/bin/python tests/test_schemas.py`.
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
