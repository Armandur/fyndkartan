# Backlog Export

## [P3][todo] [matbutiker] Sanity-tröskel på Prisändringar-sorten (abs_desc) - filtrera skräp-outliers

Sortiment-fliken -> Prisändringar, sorten "störst ändring" (abs_desc) domineras av skräp-outliers från källparse-fel: t.ex. Coop-post på "260915 kr" och Red Bull "5298 kr" i stället för rimliga belopp. Dessa är parse-fel i inläst hyllpris, inte verkliga prisändringar, och tränger undan de intressanta ändringarna.

Åtgärd: lägg en rimlighetsgräns i catalog_price_changes (api/database/catalog.py) så orimliga pris/prev_price (t.ex. > N kr eller ändringsfaktor > X) filtreras bort ur ändrings-delmängden. Alternativt sanera vid skrivning (upsert_catalog/upsert_store_prices) så skräpet aldrig lagras som prev_price. Bestäm tröskelvärde empiriskt mot verklig data.

Fynd från Sortiment-flikens monitoring-omarbetning (session 2026-07-18).

- ID: `01KXV89CRWSE3X9FMTPDXB0JM2`
- Type: improvement
- Actor: ai:claude-opus-4-8

---

