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

# Coop personalization-API (produktdetalj: ingredienser/ursprung/förvaring). Egen
# subscription-nyckel (≠ butiks-/dke-nyckeln), i Coops serviceAccess-JSON. Tom =
# skrapas + scrape-on-401 (self-renewing). store-param är produktoberoende.
COOP_PERSO_KEY = os.getenv("COOP_PERSO_KEY", "")
COOP_DETAIL_STORE = os.getenv("COOP_DETAIL_STORE", "251300")

# Schemalagd butikssynk via cron-uttryck (både intervall och bestämd tid).
# Default: dagligen 04:00 svensk tid. Exempel: "0 */6 * * *" = var 6:e timme.
# Tomt / "off" = avstängd. Erbjudanden sköts av sin egen 6h lazy-cache.
SYNC_CRON = os.getenv("SYNC_CRON", "0 4 * * *")
SYNC_TZ = os.getenv("SYNC_TZ", "Europe/Stockholm")

# Session-cookie. SESSION_SECRET löses i main vid import (env eller DB-persisterad).
# https_only måste vara av i normalfallet (lokal Unraid över http).
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"

# Konsol-admin. Seedas i admin_users vid uppstart. Sätt ADMIN_EMAIL/ADMIN_PASSWORD i
# env (image/prod) - default-mejlen nedan är bara en generisk platshållare, INTE en
# instansspecifik adress. Saknas ADMIN_PASSWORD genereras ett som loggas en gång.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

CHAINS = ["ica", "coop", "willys", "hemkop", "lidl"]

# Private-label-brand-rötter per kedja (egna märkesvaror). Editerbar i admin-UI
# (private_brands-tabell), seedas med listan nedan. En produkt räknas som private
# label om dess brand (case-insensitivt) BÖRJAR med någon rot - så "ICA" fångar
# "ICA. Ursprung Sverige", "ICA Selection" osv. Dessa matchar aldrig cross-chain
# via EAN (kedjeinterna), så de paras ihop manuellt i "Märkesvaror"-fliken.
DEFAULT_PRIVATE_BRANDS = {
    "ica": ["ICA", "Eldorado", "Skona", "Rätt Sortiment"],
    "coop": ["Coop", "Änglamark", "Xtra", "X-tra"],
    "willys": ["Garant", "Eldorado", "Såklart", "Falkenberg", "Premiär", "Fixa", "Aleko"],
    "hemkop": ["Garant", "Eldorado", "Såklart", "Falkenberg", "Premiär", "Fixa", "Aleko"],
    "lidl": [],
}

# Statisk beskrivning av datakällorna (för admin-dashboarden).
DATA_SOURCES = [
    {"chain": "ica", "what": "butiker", "url": "apim-pub.gw.ica.se/.../mdsastoresearch/v1/storeslist", "auth": "Bearer (token från ica.se/e11/public-access-token)"},
    {"chain": "ica", "what": "erbjudanden", "url": "ica.se/erbjudanden/{slug}-{id}/ (weeklyOffers, server-renderad)", "auth": "ingen"},
    {"chain": "coop", "what": "butiker", "url": "proxy.api.coop.se/external/store/stores/map", "auth": "Ocp-Apim-Subscription-Key"},
    {"chain": "coop", "what": "tjänster (detalj)", "url": "proxy.api.coop.se/external/store/stores/{ledger}", "auth": "Ocp-Apim-Subscription-Key"},
    {"chain": "coop", "what": "erbjudanden", "url": "external.api.coop.se/dke/offers/{ledger}", "auth": "offers-nyckel (dkeKey)"},
    {"chain": "willys", "what": "butiker", "url": "willys.se/axfood/rest/store", "auth": "ingen"},
    {"chain": "willys", "what": "tjänster", "url": "willys.se/axfoodcommercewebservices/v2/.../cms/components", "auth": "ingen"},
    {"chain": "willys", "what": "erbjudanden + EAN", "url": "willys.se/search/campaigns + /axfood/rest/p/{code}", "auth": "ingen"},
    {"chain": "hemkop", "what": "butiker", "url": "hemkop.se/axfood/rest/store", "auth": "ingen"},
    {"chain": "hemkop", "what": "tjänster", "url": "hemkop.se/axfoodcommercewebservices/v2/.../cms/components", "auth": "ingen"},
    {"chain": "hemkop", "what": "erbjudanden + EAN", "url": "hemkop.se/search/campaigns + /axfood/rest/p/{code}", "auth": "ingen"},
    {"chain": "lidl", "what": "butiker", "url": "live.api.schwarz/odj/stores-api/v2/.../stores (geo_box-svep)", "auth": "x-apikey"},
    {"chain": "lidl", "what": "erbjudanden", "url": "regionalt (offerRegion) - ej byggt", "auth": "-"},
    # Produktinfo (ingredienser/näring/ursprung) per EAN för märkesvaru-paringen.
    {"chain": "willys", "what": "produktinfo (ingredienser/näring)", "url": "willys.se/axfood/rest/p/{code}", "auth": "ingen"},
    {"chain": "hemkop", "what": "produktinfo (ingredienser/näring)", "url": "hemkop.se/axfood/rest/p/{code}", "auth": "ingen"},
    {"chain": "coop", "what": "produktinfo per EAN (+ cross-chain-fallback)", "url": "external.api.coop.se/personalization/search/entities/by-id (POST, EAN-array)", "auth": "personalization-nyckel (skrapas)"},
    {"chain": "ica", "what": "produktinfo", "url": "ehandel bot-skyddad (AWS WAF) - använder Coop-fallback på EAN för branded varor", "auth": "-"},
]

# Kanonisk vokabulär för butikstjänst-taggar. Editerbar i admin-UI (tag_types-tabell),
# seedas med listan nedan. BUILTIN är de typer seed_types kan producera - de skyddas
# från radering (annars blir seed-output ogiltig).
DEFAULT_TAG_TYPES = [
    "pharmacy", "postal", "parcel", "atg", "gambling", "parking",
    "parking_disabled", "e_charging", "bakery", "deli", "self_scan",
    "cash", "click_collect", "loyalty_app", "gift_card", "catering",
    "recycling", "franchise", "togo", "other",
]
BUILTIN_TAG_TYPES = {
    "pharmacy", "postal", "parcel", "atg", "gambling", "bakery",
    "self_scan", "cash", "click_collect", "e_charging", "togo", "other",
}

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
