"""Konsol-justerbara schemaläggnings-inställningar: DB-override (`settings`-tabellen, nyckel
`cfg_<key>`) > env/kod-default (`config`). Resolvas vid LÄSNING -> konsol-ändringar slår igenom utan
omstart (schemaläggar-loopen läser om varje varv via callables; display-endpoints läser färskt).

Tre tillstånd per nyckel: override satt (giltig cron/tz), override = 'off'/tomt (pausad schemaläggning,
ett lagrat värde) eller ingen override (faller tillbaka på env/default). 'off' och 'återställ' är
alltså skilda operationer.
"""
from . import config, database

# nyckel -> env/kod-default (resolverat vid import via config). DB-nyckel: 'cfg_<key>'.
_DEFAULTS = {
    "sync_cron": config.SYNC_CRON,
    "offers_sweep_cron": config.OFFERS_SWEEP_CRON,
    "catalog_crawl_cron": config.CATALOG_CRAWL_CRON,
    "partial_upgrade_cron": config.PARTIAL_UPGRADE_CRON,
    "ica_ecom_cron": config.ICA_ECOM_CRON,
    "sync_tz": config.SYNC_TZ,
}
KEYS = tuple(_DEFAULTS)
CRON_KEYS = ("sync_cron", "offers_sweep_cron", "catalog_crawl_cron", "partial_upgrade_cron", "ica_ecom_cron")


def default(key):
    return _DEFAULTS.get(key)


def get(key):
    """Effektivt värde: DB-override om satt, annars env/kod-default."""
    val = database.get_setting(f"cfg_{key}")
    return val if val is not None else _DEFAULTS.get(key)


def is_overridden(key):
    return database.get_setting(f"cfg_{key}") is not None


def set_override(key, value):
    if key not in _DEFAULTS:
        raise KeyError(key)
    database.set_setting(f"cfg_{key}", value)


def clear_override(key):
    """Ta bort override -> faller tillbaka på env/default."""
    database.delete_setting(f"cfg_{key}")
