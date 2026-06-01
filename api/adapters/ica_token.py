import logging
from datetime import datetime, timezone

log = logging.getLogger("matbutiker")

# ICA:s frontend hämtar ett publikt anonymt token härifrån i runtime. Det är ett
# riktigt token-API (JSON) och ger alltid ett FÄRSKT token - till skillnad från det
# inbäddade tokenet i /butiker/-HTML:en, som ligger CDN-cachat och kan vara utgånget.
TOKEN_URL = "https://www.ica.se/e11/public-access-token"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# Förnya i förväg med denna marginal (sekunder) innan utgång.
_REFRESH_MARGIN = 120

_cache = {"token": None, "expires": None}


def _parse_expires(s):
    # "2026-05-31T21:59:41.0244155Z" -> aware datetime (trunkera till mikrosekunder)
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        s = f"{head}.{frac[:6]}"
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


async def get_token(client, force=False):
    """Returnera ett giltigt publikt ICA-token från token-API:t.

    Cachas till strax före utgång; hämtas på nytt vid behov eller med force=True.
    """
    now = datetime.now(timezone.utc)
    if not force and _cache["token"] and _cache["expires"]:
        if (_cache["expires"] - now).total_seconds() > _REFRESH_MARGIN:
            return _cache["token"]
    r = await client.get(
        TOKEN_URL, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30
    )
    r.raise_for_status()
    data = r.json()
    token = data.get("publicAccessToken")
    if not token:
        raise RuntimeError("ICA token-API gav inget publicAccessToken")
    _cache["token"] = token
    exp = data.get("tokenExpires")
    _cache["expires"] = _parse_expires(exp) if exp else None
    log.info("ICA: nytt publikt token hämtat, giltigt till %s", exp)
    return token
