# DOCKER.md - Fyndkartan

Containeriserad deployment. Imagen byggs av GitHub Actions till
`ghcr.io/armandur/fyndkartan` (taggar: `latest` på main, SHA, branch, semver).

## Image

`Dockerfile` bygger en `python:3.12-slim`-image: installerar beroenden via `uv`
från `uv.lock`, kopierar in `api/` (FastAPI) + `web/` (statisk frontend), och kör
`uvicorn api.main:app` på port **8000**. Frontend serveras av API:t (single image).

DB:n ligger på `DB_PATH` (default `/data/stores.db`) - montera en volym på `/data`
för att persistera butiks-/erbjudande-cachen mellan omstarter.

## Normalfall: single container (`docker-compose.yml`)

Monolitisk single-container (t.ex. lokal Unraid-server). Hela appen i en
container, porten exponeras direkt.

```bash
docker compose pull
docker compose up -d
# -> http://<host>:8700
```

- Port `8700` -> containerns `8000`. Volym `${DATA_DIR:-./data}` -> `/data` (SQLite).
- Healthcheck mot `/healthz`.

**På Unraid** kan containern lika gärna läggas till direkt i Dockers UI utan compose:
- Repository: `ghcr.io/armandur/fyndkartan:latest`
- Port: `8700` -> `8000`
- Path: `/mnt/user/appdata/fyndkartan` -> `/data`
- (valfri var: `DB_PATH=/data/stores.db`)

## Undantag: externt hostad med TLS (`docker-compose.hetzner.yml`)

För externt hostade tjänster (t.ex. på Hetzner) med publik domän: `app` bakom
`caddy` (auto-TLS reverse proxy), inga portar exponerade direkt.

```bash
echo "DOMAIN=fyndkartan.dindoman.se" > .env
docker compose -f docker-compose.hetzner.yml up -d
```

Caddy (`Caddyfile`) proxar `{$DOMAIN}` -> `app:8000`. Har du redan en delad Caddy:
ta bort `caddy`-tjänsten och proxa dit `app:8000` därifrån.

## Postgres-deploy: app + databas (Steg 6-skalan)

När per-butik-pris-skalan (~14M rader + zon-browse-aggregat) motiverar det körs appen
mot **Postgres** i stället för SQLite. Datalagret är DB-oberoende (SQLAlchemy Core), så
det enda som byter är `DATABASE_URL`. SQLite-deployen ovan finns kvar oförändrad.

### Container-topologi (separationen)

```
[ db ]  Postgres 16           <- persistent volym (pgdata)
   ^
   | DATABASE_URL (internt nät)
[ api ]  ghcr.io/armandur/fyndkartan   <- FastAPI + serverar web/ statiskt, port 8700
   ^
   | (idag: API:t serverar web/ självt - ingen separat frontend-container behövs)
[ webbläsare ]
```

- **db-container** (Postgres): egen container + volym. Klart rätt - en DB-server hör inte
  ihop med app-processen.
- **api-container** (FastAPI/uvicorn): kör API:t. **Serverar i dagsläget även `web/`
  statiskt** (kart-app + konsol), så ingen separat frontend-container krävs ännu.
- **frontend-container (SENARE, valfritt):** att bryta ut `web/` till en egen container
  (t.ex. nginx som serverar statiska filer + proxar `/v1` -> api) är billigt att göra
  senare (REST-ytan är redan ren, frontend är bundler-lös statisk) men ger lite NU för en
  enanvändar-homelab. Gör splitten när det finns ett konkret skäl: en andra konsument, en
  byggpipeline för frontend, eller api/app/admin-separation som säkerhetsgräns. Tills dess
  = en moving part mindre.

### Med compose (rekommenderat på Unraid - Compose Manager-pluginet)

```bash
echo "POSTGRES_PASSWORD=<välj-ett>" > .env
docker compose -f docker-compose.pg.yml up -d   # -> http://<host>:8700
```

`docker-compose.pg.yml` definierar `db` + `app` på ett gemensamt Docker-nät: appen når
DB:n på hostnamnet `db` (`DATABASE_URL=postgresql+psycopg://fyndkartan:<pw>@db:5432/...`).
Compose sköter nätet + DNS automatiskt - därför är det enklare än att handkoppla i GUI:t
för en flercontainer-app.

### Via Unraids Docker-GUI (utan compose)

Går också, men de två containrarna måste kunna prata med varandra över ett **gemensamt
Docker-nät** (annars hittar api:t inte db:n på namn):

1. Skapa ett custom Docker-nät i Unraid (t.ex. `fyndkartan-net`).
2. **db-container:** image `postgres:16-alpine`, nät `fyndkartan-net`, namn `db`,
   - env: `POSTGRES_USER=fyndkartan`, `POSTGRES_PASSWORD=<pw>`, `POSTGRES_DB=fyndkartan`
   - volym: `/mnt/user/appdata/fyndkartan-db` -> `/var/lib/postgresql/data`
   - (ingen port behöver exponeras till hosten om bara api:t pratar med den)
3. **api-container:** image `ghcr.io/armandur/fyndkartan:latest`, nät `fyndkartan-net`,
   - port `8700` -> `8000`
   - env: `DATABASE_URL=postgresql+psycopg://fyndkartan:<pw>@db:5432/fyndkartan`
   - (ingen `/data`-volym behövs - datan ligger i db-containern)

Alternativ utan custom nät: exponera Postgres port `5432` på hosten och sätt
`DATABASE_URL=...@<host-ip>:5432/...` - funkar men mindre rent.

### Migrera befintlig SQLite-data EN gång

Datan är mestadels regenererbar (crawl/sync), MEN prishistoriken (~6,5M observationer)
byggs upp över tid och kan inte återskapas -> kopiera ALLT en gång:

```bash
DATABASE_URL=postgresql+psycopg://fyndkartan:<pw>@<host>:5432/fyndkartan \
  .venv/bin/python -m api.migrate_to_pg
```

Skriptet skapar schemat på Postgres (`create_all`), bulk-kopierar alla tabeller, nollställer
SERIAL-sekvenserna och kör `ANALYZE`. Kör mot en TOM Postgres (annars PK-krockar). ~15 min
för ~14M rader. Efter migreringen: starta api-containern med samma `DATABASE_URL`.

## Dev (`docker-compose.dev.yml`)

Bygger lokalt, bind-mountar `api/` + `web/` och kör med `--reload`, exponerar `8700`:

```bash
docker compose -f docker-compose.dev.yml up --build
# -> http://localhost:8700
```

(För snabb iterering går det fortfarande lika bra att köra nativt:
`uv run uvicorn api.main:app --host 0.0.0.0 --port 8700`.)

## Synk / data

- Vid första start (tom DB) körs en butikssynk automatiskt i bakgrunden (~20-30s).
- **Schemalagd omsynk är inbyggd** i appen (intern asyncio-task, ingen extra
  container): butikssynken körs enligt **`SYNC_CRON`** (default `0 4 * * *` =
  dagligen 04:00, tidszon `SYNC_TZ` = `Europe/Stockholm`). Tomt = av.
  Cron ger både bestämd tid (`0 4 * * *`) och intervall (`0 */6 * * *`).
- Erbjudanden hämtas lazy per butik och cachas 6h (ingår inte i den schemalagda
  synken). Manuell omsynk: `POST /v1/sync`.

## Nycklar

Inga hemligheter krävs i drift - ICA-token samt Coop-/Lidl-nycklar skrapas
automatiskt från kedjornas publika sidor. `.env` behövs bara för `DOMAIN` (och
ev. manuella nyckel-overrides, se `.env.example`).
