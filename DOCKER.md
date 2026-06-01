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
- Erbjudanden hämtas lazy per butik och cachas 6h.
- **Schemalagd omsynk** är inte inbyggd. Lägg t.ex. en cron/systemd-timer på hosten
  som dygnsvis kör `curl -fsS -X POST http://localhost:8000/v1/sync` (i app-containern),
  eller en sidecar. Se ROADMAP.

## Nycklar

Inga hemligheter krävs i drift - ICA-token samt Coop-/Lidl-nycklar skrapas
automatiskt från kedjornas publika sidor. `.env` behövs bara för `DOMAIN` (och
ev. manuella nyckel-overrides, se `.env.example`).
