import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Konfigurerbar för persistent volym i Docker (DB_PATH=/data/stores.db).
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "stores.db"))

ICA_TOKEN = os.getenv("ICA_TOKEN", "")
COOP_KEY = os.getenv("COOP_KEY", "")
COOP_OFFERS_KEY = os.getenv("COOP_OFFERS_KEY", "")
LIDL_KEY = os.getenv("LIDL_KEY", "")

# Schemalagd butikssynk via cron-uttryck (både intervall och bestämd tid).
# Default: dagligen 04:00 svensk tid. Exempel: "0 */6 * * *" = var 6:e timme.
# Tomt / "off" = avstängd. Erbjudanden sköts av sin egen 6h lazy-cache.
SYNC_CRON = os.getenv("SYNC_CRON", "0 4 * * *")
SYNC_TZ = os.getenv("SYNC_TZ", "Europe/Stockholm")

CHAINS = ["ica", "coop", "willys", "hemkop", "lidl"]

# label = visningsnamn, color = markörfärg (kedjans profil), offers = stöder erbjudande-länk
CHAIN_META = {
    "ica":    {"label": "ICA",    "color": "#e3000b", "auth": "bearer",           "offers": True},
    "coop":   {"label": "Coop",   "color": "#00a651", "auth": "subscription_key", "offers": True},
    "willys": {"label": "Willys", "color": "#b71c1c", "auth": "none",             "offers": True},
    "hemkop": {"label": "Hemköp", "color": "#f57c00", "auth": "none",             "offers": True},
    "lidl":   {"label": "Lidl",   "color": "#0050aa", "auth": "apikey",           "offers": False},
}

# Lidl geo-svep: API:t ger bara butiker inom en geo_box, så vi sveper ett rutnät.
# (lat_min, lng_min, lat_max, lng_max) som täcker Sverige.
SWEDEN_BOUNDS = (55.0, 10.8, 69.2, 24.3)
LIDL_BOX_DLAT = 1.0
LIDL_BOX_DLNG = 2.0
LIDL_SLEEP = 0.12  # snäll paus mellan anrop (sekunder)
