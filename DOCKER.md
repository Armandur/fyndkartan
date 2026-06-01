# DOCKER.md - Fyndkartan

Containeriserad deployment. Imagen byggs av GitHub Actions till
`ghcr.io/armandur/fyndkartan` (taggar: `latest` på main, SHA, branch, semver).

## Image

`Dockerfile` bygger en `python:3.12-slim`-image: installerar beroenden via `uv`
från `uv.lock`, kopierar in `api/` (FastAPI) + `web/` (statisk frontend), och kör
`uvicorn api.main:app` på port **8000**. Frontend serveras av API:t (single image).

DB:n ligger på `DB_PATH` (default `/data/stores.db`) - montera en volym på `/data`
för att persistera butiks-/erbjudande-cachen mellan omstarter.

## Prod (`docker-compose.yml`)

Två tjänster: `app` (imagen från GHCR) bakom `caddy` (auto-TLS reverse proxy).

```bash
echo "DOMAIN=fyndkartan.dindoman.se" > .env   # domän för Caddy/TLS
docker compose pull
docker compose up -d
```

- `app` exponerar inga portar externt - all trafik går via Caddy (`Caddyfile`
  proxar `{$DOMAIN}` -> `app:8000`).
- Volymer: `data` (SQLite), `caddy_data`/`caddy_config` (certifikat m.m.).
- Healthcheck mot `/healthz`.
- Har du redan en delad Caddy på hosten: ta bort `caddy`-tjänsten och proxa dit
  `app:8000` därifrån istället.

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
