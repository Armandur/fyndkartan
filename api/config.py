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

# Static-embeddings-modell (model2vec, CPU/numpy) för semantiska märkesvaru-paringsförslag.
# Lazy-laddad; degraderar tyst till lexikal matchning om den ej kan laddas (offline/saknas).
EMBED_MODEL = os.getenv("EMBED_MODEL", "minishlab/potion-multilingual-128M")

# Schemalagd butikssynk via cron-uttryck (både intervall och bestämd tid).
# Default: dagligen 04:00 svensk tid. Exempel: "0 */6 * * *" = var 6:e timme.
# Tomt / "off" = avstängd. Erbjudanden sköts av sin egen 6h lazy-cache.
SYNC_CRON = os.getenv("SYNC_CRON", "0 4 * * *")
SYNC_TZ = os.getenv("SYNC_TZ", "Europe/Stockholm")

# Bulk-förhämtning av erbjudanden (sweep): går igenom alla offer-stödda butiker och hämtar
# de som inte är färska (_offers_fresh). Egen cadence - tätare än butikssynken, men billig
# eftersom färska butiker hoppas och offers refetchas vid utgång (valid_to). Tomt/"off" = av.
OFFERS_SWEEP_CRON = os.getenv("OFFERS_SWEEP_CRON", "0 * * * *")  # varje hel timme
OFFERS_SWEEP_CONCURRENCY = int(os.getenv("OFFERS_SWEEP_CONCURRENCY", "4"))  # samtidiga butiker/kedja
OFFERS_SWEEP_PACE = float(os.getenv("OFFERS_SWEEP_PACE", "0.25"))  # paus mellan hämtningar (s)
OFFERS_SWEEP_RETRIES = int(os.getenv("OFFERS_SWEEP_RETRIES", "3"))  # försök per butik vid fel
OFFERS_SWEEP_BACKOFF = float(os.getenv("OFFERS_SWEEP_BACKOFF", "1.5"))  # bas-back-off (s, exponentiell)
OFFERS_SWEEP_CIRCUIT = int(os.getenv("OFFERS_SWEEP_CIRCUIT", "12"))  # fel i rad/kedja -> pausa kedjan

# Fulla sortiment-crawl (steg 5): walk:ar kedjornas kategoriträd och persistar hela sortimentet.
# Tung körning (~74k produkter) -> gles default (veckovis, mån 03:00). Tomt/'off' = av (manuell).
CATALOG_CRAWL_CRON = os.getenv("CATALOG_CRAWL_CRON", "0 3 * * 1")
CATALOG_CRAWL_PAGE = int(os.getenv("CATALOG_CRAWL_PAGE", "100"))      # produkter per sida (take)
CATALOG_CRAWL_PACE = float(os.getenv("CATALOG_CRAWL_PACE", "0.3"))   # paus mellan sidor (s)
CATALOG_CRAWL_RETRIES = int(os.getenv("CATALOG_CRAWL_RETRIES", "3"))
CATALOG_CRAWL_BACKOFF = float(os.getenv("CATALOG_CRAWL_BACKOFF", "1.5"))

# Session-cookie. SESSION_SECRET löses i main vid import (env eller DB-persisterad).
# https_only måste vara av i normalfallet (lokal Unraid över http).
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"

# CORS: komma-separerad allowlist av tillåtna origins för en separat frontend-app.
# Tom (default) = ingen CORS-middleware (oförändrat same-origin-beteende). ALDRIG "*"
# tillsammans med credentials - bara explicita origins (cookie/auth-säkerhet).
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

# Konsol-admin. Seedas i admin_users vid uppstart. Sätt ADMIN_EMAIL/ADMIN_PASSWORD i
# env (image/prod) - default-mejlen nedan är bara en generisk platshållare, INTE en
# instansspecifik adress. Saknas ADMIN_PASSWORD genereras ett som loggas en gång.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

CHAINS = ["ica", "coop", "willys", "hemkop", "lidl", "citygross"]
# Axfood-kedjorna: erbjudandena bär ingen inline-EAN (resolvas via ean_cache code->EAN).
AXFOOD_CHAINS = ("willys", "hemkop")

# Kanonisk kategori-vokabulär (platt) för att normalisera kedjornas olika taxonomier.
# Platt och grov (~ICA-nivå) - taket datan tillåter (Coop har bara 3 kategorier,
# Axfood 18 fina). Editerbar i admin-UI (category_types-tabell), seedas med listan.
CANONICAL_CATEGORIES = [
    ("frukt_gront", "Frukt & Grönt"), ("mejeri_agg", "Mejeri & Ägg"),
    ("kott_chark", "Kött & Chark"), ("fisk_skaldjur", "Fisk & Skaldjur"),
    ("brod_bageri", "Bröd & Bageri"), ("skafferi", "Skafferi"),
    ("dryck", "Dryck"), ("fryst", "Fryst"), ("godis_snacks", "Godis & Snacks"),
    ("fardigmat", "Färdigmat"), ("vegetariskt", "Vegetariskt"),
    ("farskvaror", "Färskvaror (övr.)"), ("halsa_skonhet", "Hälsa & Skönhet"),
    ("hem_hushall", "Hem & Hushåll"), ("barn", "Barn"), ("djur", "Djur"),
    ("ovrigt", "Övrigt"),
]

# Seed-mappning råkategori -> kanonisk. Nyckel = (chain_key, raw_key). Axfood
# (willys+hemkop) delar taxonomi -> chain_key "axfood", raw_key = första pipe-segmentet.
# ICA/Coop: chain_key = kedjan, raw_key = hela råsträngen. Editerbar (category_map).
DEFAULT_CATEGORY_MAP = {
    ("axfood", "skafferi"): "skafferi",
    ("axfood", "mejeri-ost-och-agg"): "mejeri_agg",
    ("axfood", "hem-och-hushall"): "hem_hushall",
    ("axfood", "halsa-och-skonhet"): "halsa_skonhet",
    ("axfood", "frukt-och-gront"): "frukt_gront",
    ("axfood", "kott-fagel-och-chark"): "kott_chark",
    ("axfood", "fryst"): "fryst",
    ("axfood", "dryck"): "dryck",
    ("axfood", "godis-snacks-och-glass"): "godis_snacks",
    ("axfood", "brod-och-kakor"): "brod_bageri",
    ("axfood", "djur"): "djur",
    ("axfood", "barn"): "barn",
    ("axfood", "fardigmat"): "fardigmat",
    ("axfood", "fisk-och-skaldjur"): "fisk_skaldjur",
    ("axfood", "vegetariskt"): "vegetariskt",
    ("axfood", "apotek-och-lakemedel"): "halsa_skonhet",
    ("axfood", "blommor-och-tillbehor"): "hem_hushall",
    ("axfood", "delikatessen"): "skafferi",
    # Produktdetaljens googleAnalyticsCategory använder delvis andra segment-namn än
    # kampanjen (samma kategorier, annan stavning) - mappa även dem.
    ("axfood", "kott-chark-och-fagel"): "kott_chark",
    ("axfood", "glass-godis-och-snacks"): "godis_snacks",
    ("axfood", "hem-och-stad"): "hem_hushall",
    ("axfood", "apotek"): "halsa_skonhet",
    ("axfood", "blommor-och-tradgard"): "hem_hushall",
    ("axfood", "kott-och-chark"): "kott_chark",
    ("ica", "Färskvaror"): "farskvaror",
    ("ica", "Mejeri"): "mejeri_agg",
    ("ica", "Frukt & Grönt"): "frukt_gront",
    ("ica", "Skafferivaror"): "skafferi",
    ("ica", "Djupfryst"): "fryst",
    ("ica", "Bröd, kex & bageri"): "brod_bageri",
    ("ica", "Hälsa & skönhet"): "halsa_skonhet",
    ("ica", "Hem & fritid"): "hem_hushall",
    ("ica", "Ospecificerat"): "ovrigt",
    # Offer-nivåns 3 buckets är opålitliga (Färsk blandar kött+ost; Kolonial blandar
    # skafferi+glass; Nonfood är en grab-bag med grönsaker+kaffe). Färsk/Kolonial är
    # försvarbara grova fallbacks; Nonfood mappas till ovrigt (innehållet är inte non-food).
    # Den finare coop_nav (produktdetalj) overridar dessa när den finns.
    ("coop", "Kolonial"): "skafferi",
    ("coop", "Färsk"): "farskvaror",
    ("coop", "Nonfood"): "ovrigt",
    # Coop navCategories topp-namn (produktdetalj). Namnen nedan är de personalization-API:t
    # faktiskt returnerar (verifierat mot 449 EAN) - tidigare seedade namn var fel-gissade.
    ("coop_nav", "Frukt & Grönsaker"): "frukt_gront",
    ("coop_nav", "Mejeri & Ägg"): "mejeri_agg",
    ("coop_nav", "Ost"): "mejeri_agg",
    ("coop_nav", "Kött, Fågel & Chark"): "kott_chark",
    ("coop_nav", "Fisk & Skaldjur"): "fisk_skaldjur",
    ("coop_nav", "Bröd & Bageri"): "brod_bageri",
    ("coop_nav", "Skafferi"): "skafferi",
    ("coop_nav", "Kryddor & Smaksättare"): "skafferi",
    ("coop_nav", "Dryck"): "dryck",
    ("coop_nav", "Frys"): "fryst",
    ("coop_nav", "Godis, Glass & Snacks"): "godis_snacks",
    ("coop_nav", "Färdigmat & Mellanmål"): "fardigmat",
    ("coop_nav", "Vegetariskt"): "vegetariskt",
    ("coop_nav", "Djurmat & Tillbehör"): "djur",
    ("coop_nav", "Skönhet & Hygien"): "halsa_skonhet",
    ("coop_nav", "Apotek, hälsa & tillskott"): "halsa_skonhet",
    ("coop_nav", "Hushåll"): "hem_hushall",
    ("coop_nav", "Hem & inredning"): "hem_hushall",
    ("coop_nav", "Fritid"): "hem_hushall",
    # ICA produktdetaljens breadcrumb-topp (categories[0], rikare än offer-nivåns articleGroupName).
    # Strängarna nedan är de handla.ica.se faktiskt renderar (verifierade) + uppenbara ICA-toppar.
    ("ica_nav", "Frukt & Grönt"): "frukt_gront",
    ("ica_nav", "Mejeri & Ost"): "mejeri_agg",
    ("ica_nav", "Kött, Chark & Fågel"): "kott_chark",
    ("ica_nav", "Fisk & Skaldjur"): "fisk_skaldjur",
    ("ica_nav", "Bröd & Bageri"): "brod_bageri",
    ("ica_nav", "Skafferi"): "skafferi",
    ("ica_nav", "Dryck"): "dryck",
    ("ica_nav", "Glass, Godis & Snacks"): "godis_snacks",
    ("ica_nav", "Färdigmat & Såser"): "fardigmat",
    ("ica_nav", "Fryst"): "fryst",
    ("ica_nav", "Djupfryst"): "fryst",
    ("ica_nav", "Vegetariskt"): "vegetariskt",
    ("ica_nav", "Barn"): "barn",
    ("ica_nav", "Djur"): "djur",
    ("ica_nav", "Hälsa & Skönhet"): "halsa_skonhet",
    ("ica_nav", "Hem"): "hem_hushall",
    ("ica_nav", "Fritid"): "hem_hushall",
    # City Gross superCategory (offers) -> kanonisk.
    ("citygross", "Bröd & bageri"): "brod_bageri",
    ("citygross", "Frukt & grönt"): "frukt_gront",
    ("citygross", "Mejeri, ost & ägg"): "mejeri_agg",
    ("citygross", "Snacks"): "godis_snacks",
    ("citygross", "Godis"): "godis_snacks",
    ("citygross", "Kött & fågel"): "kott_chark",
    ("citygross", "Chark & pålägg"): "kott_chark",
    ("citygross", "Chark"): "kott_chark",
    ("citygross", "Manuell delikatess"): "farskvaror",
    ("citygross", "Fryst"): "fryst",
    ("citygross", "Fisk & skaldjur"): "fisk_skaldjur",
    ("citygross", "Kyld färdigmat"): "fardigmat",
    ("citygross", "Vegetariskt"): "vegetariskt",
    ("citygross", "Skönhet & hygien"): "halsa_skonhet",
    ("citygross", "Hem & fritid"): "hem_hushall",
    ("citygross", "Köket"): "hem_hushall",
    ("citygross", "Blommor"): "hem_hushall",
    # Katalog-sök (api/catalog.py) - kedjornas sök-API:er använder delvis andra/finare
    # kategori-vokabulärer än offers. City Gross superCategory ur search/quick:
    ("citygross", "Skafferiet"): "skafferi",
    ("citygross", "Dryck"): "dryck",
    ("citygross", "Barn"): "barn",
    # ICA mainCategoryName ur globalsearch (helt egen vokabulär, finare än offer-nivån):
    ("ica", "Ost"): "mejeri_agg",
    ("ica", "Fågel"): "kott_chark",
    ("ica", "Pålägg & deli chark"): "kott_chark",
    ("ica", "Drycker varma"): "dryck",
    ("ica", "Drycker"): "dryck",
    ("ica", "Pasta, ris, mos & matgryner"): "skafferi",
    ("ica", "All världens mat"): "skafferi",
    ("ica", "Matöverkänslighet kolonial"): "skafferi",
    ("ica", "Baka"): "skafferi",
    ("ica", "Skafferiet sylt, mos, marmelad & dessert"): "skafferi",
    ("ica", "Mjukt fikabröd, kex & kakor"): "brod_bageri",
    ("ica", "Bröd"): "brod_bageri",
    ("ica", "Bröd hårt"): "brod_bageri",
    ("ica", "Butiksbakat bröd & konditori"): "brod_bageri",
    ("ica", "Frukt"): "frukt_gront",
    ("ica", "Grönsaker"): "frukt_gront",
    ("ica", "Skönhet"): "halsa_skonhet",
    ("ica", "Blöjor & babyvård"): "barn",
    ("ica", "Konfektyr"): "godis_snacks",
    ("ica", "Glass"): "godis_snacks",
    ("ica", "Färdigmat"): "fardigmat",
    ("ica", "Färdigmat fryst"): "fardigmat",
    ("ica", "Fisk & skaldjur"): "fisk_skaldjur",
}

# Private-label-brand-rötter per kedja (egna märkesvaror). Editerbar i admin-UI
# (private_brands-tabell), seedas med listan nedan. En produkt räknas som private
# label om dess brand (case-insensitivt) BÖRJAR med någon rot - så "ICA" fångar
# "ICA. Ursprung Sverige", "ICA Selection" osv. Dessa matchar aldrig cross-chain
# via EAN (kedjeinterna), så de paras ihop manuellt i "Märkesvaror"-fliken.
DEFAULT_PRIVATE_BRANDS = {
    "ica": ["ICA", "Skona"],  # Eldorado är Axfood; Rätt Sortiment/igenkänns ej -> bort
    "coop": ["Coop", "Änglamark", "Xtra", "X-tra"],
    "willys": ["Garant", "Eldorado", "Såklart", "Premiär", "Fixa"],  # Falkenberg/Aleko ej private label
    "hemkop": ["Garant", "Eldorado", "Såklart", "Premiär", "Fixa"],
    # City Gross ingår i Axfood-koncernen (sedan 2024) -> delar Axfoods private labels.
    "citygross": ["City Gross", "Garant", "Eldorado", "Såklart", "Premiär", "Fixa"],
    "lidl": [],
}

# Statisk beskrivning av datakällorna (för admin-dashboarden). `auth_kind` + `example`
# låter konsolens API-testare köra dem via /v1/admin/proxy (rätt nyckel/token läggs
# på server-side). Tomt `example` = ej direkt GET-testbar (POST/bot-skyddad/ej byggt).
DATA_SOURCES = [
    {"chain": "ica", "what": "access-token", "url": "ica.se/e11/public-access-token", "auth": "ingen", "auth_kind": "none", "example": "https://www.ica.se/e11/public-access-token"},
    {"chain": "ica", "what": "butiker", "url": "apim-pub.gw.ica.se/.../mdsastoresearch/v1/storeslist", "auth": "Bearer (token från ica.se/e11/public-access-token)", "auth_kind": "ica", "example": "https://apim-pub.gw.ica.se/sverige/digx/mdsastoresearch/v1/storeslist?url=/&sort=FromStore&skip=0&take=5"},
    {"chain": "ica", "what": "erbjudanden", "url": "ica.se/erbjudanden/{slug}-{id}/ (weeklyOffers, server-renderad)", "auth": "ingen", "auth_kind": "none", "example": "https://www.ica.se/erbjudanden/ica-nara-a-livs-1004177/"},
    {"chain": "coop", "what": "butiker", "url": "proxy.api.coop.se/external/store/stores/map", "auth": "Ocp-Apim-Subscription-Key", "auth_kind": "coop_store", "example": "https://proxy.api.coop.se/external/store/stores/map?api-version=v2&conceptIds=12,6,95&invertFilter=true"},
    {"chain": "coop", "what": "butiksdetalj (tjänster, concept, öppettider)", "url": "proxy.api.coop.se/external/store/stores/{ledger}", "auth": "Ocp-Apim-Subscription-Key", "auth_kind": "coop_store", "example": "https://proxy.api.coop.se/external/store/stores/196183?api-version=v5"},
    {"chain": "coop", "what": "erbjudanden", "url": "external.api.coop.se/dke/offers/{ledger}", "auth": "offers-nyckel (dkeKey)", "auth_kind": "coop_dke", "example": "https://external.api.coop.se/dke/offers/196183?api-version=v2"},
    {"chain": "willys", "what": "butiker", "url": "willys.se/axfood/rest/store", "auth": "ingen", "auth_kind": "none", "example": "https://www.willys.se/axfood/rest/store?online=false"},
    {"chain": "willys", "what": "tjänster", "url": "willys.se/axfoodcommercewebservices/v2/.../cms/components", "auth": "ingen", "auth_kind": "none", "example": "https://www.willys.se/axfoodcommercewebservices/v2/willys/cms/components?componentIds=WillysDefaultRightColumnStoreInfoComponent&storeId=2102&pageSize=1"},
    {"chain": "willys", "what": "erbjudanden", "url": "willys.se/search/campaigns", "auth": "ingen", "auth_kind": "none", "example": "https://www.willys.se/search/campaigns?storeId=2102&size=20"},
    {"chain": "hemkop", "what": "butiker", "url": "hemkop.se/axfood/rest/store", "auth": "ingen", "auth_kind": "none", "example": "https://www.hemkop.se/axfood/rest/store?online=false"},
    {"chain": "hemkop", "what": "tjänster", "url": "hemkop.se/axfoodcommercewebservices/v2/.../cms/components", "auth": "ingen", "auth_kind": "none", "example": "https://www.hemkop.se/axfoodcommercewebservices/v2/hemkop/cms/components?componentIds=HemkopDefaultRightColumnStoreInfoComponent&storeId=4102&pageSize=1"},
    {"chain": "hemkop", "what": "erbjudanden", "url": "hemkop.se/search/campaigns", "auth": "ingen", "auth_kind": "none", "example": "https://www.hemkop.se/search/campaigns?storeId=4102&size=20"},
    {"chain": "lidl", "what": "butiker", "url": "live.api.schwarz/odj/stores-api/v2/.../stores (geo_box-svep)", "auth": "x-apikey", "auth_kind": "lidl", "example": "https://live.api.schwarz/odj/stores-api/v2/myapi/stores-frontend/stores?country_code=SE&limit=5&offset=0&geo_box=59.0,17.8:60.0,18.5"},
    {"chain": "lidl", "what": "erbjudanden", "url": "regionalt (offerRegion) - ej byggt", "auth": "-", "auth_kind": "none", "example": ""},
    {"chain": "willys", "what": "produktinfo (ingredienser/näring)", "url": "willys.se/axfood/rest/p/{code}", "auth": "ingen", "auth_kind": "none", "example": "https://www.willys.se/axfood/rest/p/100053344_ST"},
    {"chain": "hemkop", "what": "produktinfo (ingredienser/näring)", "url": "hemkop.se/axfood/rest/p/{code}", "auth": "ingen", "auth_kind": "none", "example": "https://www.hemkop.se/axfood/rest/p/100053344_ST"},
    {"chain": "coop", "what": "produktinfo per EAN (POST)", "url": "external.api.coop.se/personalization/search/entities/by-id", "auth": "personalization-nyckel (skrapas)", "auth_kind": "coop_perso", "method": "POST", "body": "[\"7311870010970\"]", "example": "https://external.api.coop.se/personalization/search/entities/by-id?api-version=v1&store=251300&groups=CUSTOMER_PRIVATE&direct=false"},
    {"chain": "ica", "what": "produktinfo (bot-skyddad, Coop-fallback på EAN)", "url": "ehandel AWS-WAF-skyddad", "auth": "-", "auth_kind": "none", "example": ""},
    {"chain": "citygross", "what": "butiker", "url": "citygross.se/api/v1/PageData/stores", "auth": "ingen", "auth_kind": "none", "example": "https://www.citygross.se/api/v1/PageData/stores"},
    {"chain": "citygross", "what": "erbjudanden", "url": "citygross.se/api/v1/Loop54/category/2930/products (veckans erbjudanden, EAN+jämförpris inline)", "auth": "ingen", "auth_kind": "none", "example": "https://www.citygross.se/api/v1/Loop54/category/2930/products?currentWeekDiscountOnly=true&skip=0&take=5"},
]

# Svenska landnamn för att skilja ursprung från varumärke i offers.brand (ICA skriver
# "BRAND. Ursprung LAND", Coop "LAND/BRAND" eller bara varumärken). Hämtas från CLDR via
# babel (alla ISO-3166-länder på svenska) + vardagliga varianter som inte är egna CLDR-
# länder. Matchas skiftlägesokänsligt. Flerordsländer ("Costa Rica") behålls hela.
def _origin_countries():
    from babel import Locale
    names = {v.lower() for k, v in Locale("sv").territories.items() if len(k) == 2}
    names |= {"holland", "england"}  # vardagliga/historiska varianter, ej egna CLDR-länder
    return frozenset(names)


ORIGIN_COUNTRIES = _origin_countries()

# Fält-dokumentation som delas av flera endpoints (en sanningskälla för konsolens
# per-endpoint-utfällning). Där en Pydantic-modell finns deriveras fälten ur den
# (schemas.fields_doc) så modellen är enda källan för både /docs och konsolen.
from . import schemas

_RET_PRODUCT = schemas.fields_doc(schemas.Product)
_RET_STORE = schemas.fields_doc(schemas.Store)
_RET_STORE_NEAR = schemas.fields_doc(schemas.StoreNear)
_RET_OFFER = schemas.fields_doc(schemas.Offer)
_RET_PRODUCT_INFO = schemas.fields_doc(schemas.ProductInfoResponse)
_RET_COMPARE = schemas.fields_doc(schemas.CompareGroup)
_RET_CHAIN = schemas.fields_doc(schemas.Chain)
_RET_CATEGORY = schemas.fields_doc(schemas.Category)

# Egna /v1-endpoints som konsolen katalogiserar: beskrivning + parametrar + returnerade
# fält (per-endpoint-utfällning). Speglar DATA_SOURCES. `path` är ett körbart exempel.
_P_LIMIT = {"name": "limit", "desc": "Max antal (cappas server-side)"}
_P_CHAIN = {"name": "chain", "desc": "Begränsa till en kedja (valfritt)"}
OWN_APIS = [
    {"group": "Butiker", "method": "GET", "path": "/v1/stores/near?lat=59.33&lng=18.06&radius_km=5",
     "desc": "Butiker inom radie (km) runt en punkt, sorterade på avstånd.",
     "params": [{"name": "lat, lng", "desc": "Mittpunkt (obligatoriskt)"},
                {"name": "radius_km", "desc": "Radie i km"}, _P_CHAIN],
     "returns": _RET_STORE_NEAR},
    {"group": "Butiker", "method": "GET", "path": "/v1/stores?chain=lidl",
     "desc": "Hela butiksbeståndet, filtrerbart på chain och city.",
     "params": [_P_CHAIN, {"name": "city", "desc": "Filtrera på ort"}], "returns": _RET_STORE},
    {"group": "Butiker", "method": "GET", "path": "/v1/stores/ica/2527",
     "desc": "En butik med all metadata inkl. normaliserad veckoöppettid (week/exceptions).",
     "params": [{"name": "chain, store_id", "desc": "Path: kedja + butiks-id"}], "returns": _RET_STORE},
    {"group": "Butiker", "method": "GET", "path": "/v1/stores/ica/2527/offers",
     "desc": "Butikens erbjudanden (hämtas live första gången, cachas sedan).",
     "params": [{"name": "chain, store_id", "desc": "Path: kedja + butiks-id"}], "returns": _RET_OFFER},
    {"group": "Produkter", "method": "GET", "path": "/v1/products/search?q=mj%C3%B6lk",
     "desc": "Sök produkter på namn (ur erbjudande-cachen, EAN-grupperat).",
     "params": [{"name": "q", "desc": "Söktext mot produktnamn (min 2 tecken)"}, _P_LIMIT, _P_CHAIN],
     "returns": _RET_PRODUCT},
    {"group": "Produkter", "method": "GET", "path": "/v1/products/by-category?category=mejeri_agg",
     "desc": "Bläddra produkter i en kanonisk kategori (ur erbjudande-cachen).",
     "params": [{"name": "category", "desc": "Kanonisk kategori-nyckel"}, _P_LIMIT, _P_CHAIN],
     "returns": _RET_PRODUCT},
    {"group": "Produkter", "method": "GET", "path": "/v1/products/catalog?q=kaffe",
     "desc": "Live katalog-sök mot kedjornas NATIVA sök-API:er (hela sortimentet, nationellt "
             "hyllpris - ej offers). EAN-grupperat cross-chain. Lidl saknas.",
     "params": [{"name": "q", "desc": "Söktext mot kedjornas katalog (min 2 tecken)"}, _P_LIMIT],
     "returns": schemas.fields_doc(schemas.CatalogSearchResponse)},
    {"group": "Produkter", "method": "GET", "path": "/v1/products/catalog/browse?q=kaffe",
     "desc": "Sök/bläddra den PERSISTERADE katalogen (crawlad, hela sortimentet + hyllpris, EAN-"
             "grupperat) med aktuella erbjudanden överlagrade. Snabbare än live-/catalog.",
     "params": [{"name": "q", "desc": "Namn-filter (min 2 tecken)"},
                {"name": "category", "desc": "Kanonisk kategori-nyckel"}, _P_CHAIN, _P_LIMIT],
     "returns": schemas.fields_doc(schemas.CatalogSearchResponse)},
    {"group": "Produkter", "method": "GET", "path": "/v1/products/7311870010970",
     "desc": "Produktinfo per EAN (ingredienser/näring/ursprung/allergener), sammanslagen över källor.",
     "params": [{"name": "ean", "desc": "Path: EAN/GTIN"},
                {"name": "prefer_chain", "desc": "Hinta rikare native-källa (valfritt)"}],
     "returns": _RET_PRODUCT_INFO},
    {"group": "Produkter", "method": "GET", "path": "/v1/products/7311870010970/image",
     "desc": "Produktbild per EAN (resizad via CDN-transform, cachad lokalt). Returnerar bild-bytes.",
     "params": [{"name": "ean", "desc": "Path: EAN/GTIN"},
                {"name": "size", "desc": "thumb | default | full"}],
     "returns": [{"field": "(binär)", "desc": "image/* - inte JSON"}]},
    {"group": "Produkter", "method": "GET", "path": "/v1/products/7311870010970/history",
     "desc": "Prishistorik (tidsserie) per EAN ur arkiverade erbjudande-observationer, "
             "grupperad per kedja och kollapsad på lika prisnivå. Fyndspårning -> luckor när "
             "varan inte varit nedsatt.",
     "params": [{"name": "ean", "desc": "Path: EAN/GTIN"}],
     "returns": schemas.fields_doc(schemas.PriceHistoryResponse)},
    {"group": "Produkter", "method": "GET", "path": "/v1/products/7311870010970/stores",
     "desc": "Butiker som just nu har ett ERBJUDANDE på EAN:en (billigaste per butik) - för "
             "kartfilter. Bara erbjudande-cachen, inte hyllsortiment.",
     "params": [{"name": "ean", "desc": "Path: EAN/GTIN"}],
     "returns": schemas.fields_doc(schemas.ProductStoresResponse)},
    {"group": "Jämförelse", "method": "GET", "path": "/v1/compare/near?lat=59.33&lng=18.06&radius_km=5",
     "desc": "Prisjämför matchande EAN mellan kedjor nära en punkt.",
     "params": [{"name": "lat, lng, radius_km", "desc": "Område runt punkt"},
                {"name": "min_chains", "desc": "Minsta antal olika kedjor (default 2)"}],
     "returns": _RET_COMPARE},
    {"group": "Jämförelse", "method": "GET", "path": "/v1/compare/stores?stores=ica:2527,coop:598",
     "desc": "Prisjämför erbjudanden bland specifika butiker (t.ex. favoriter).",
     "params": [{"name": "stores", "desc": "Komma-separerat chain:store_id"},
                {"name": "min_chains", "desc": "Minsta antal olika kedjor (default 2)"}],
     "returns": _RET_COMPARE},
    {"group": "Metadata", "method": "GET", "path": "/v1/chains",
     "desc": "Kedjor med metadata + antal butiker.",
     "params": [], "returns": _RET_CHAIN},
    {"group": "Metadata", "method": "GET", "path": "/v1/categories",
     "desc": "Kanonisk produktkategori-vokabulär (för filtrering i erbjudande-vyer).",
     "params": [], "returns": _RET_CATEGORY},
    {"group": "Märkesvaror", "method": "GET", "path": "/v1/admin/match/suggestions?ean=7340191177482",
     "desc": "Paringsförslag för en privat märkesvara (namn-/förpackningsbaserat).",
     "params": [{"name": "ean", "desc": "EAN för den privata varan"}],
     "returns": [{"field": "suggestions[]", "desc": "Kandidatprodukter med likhet (namn/förpackning)"}]},
    {"group": "Märkesvaror", "method": "GET", "path": "/v1/admin/private-products?q=mj%C3%B6lk",
     "desc": "Privata märkesvaror ur erbjudanden (sökbar lista).",
     "params": [{"name": "q", "desc": "Söktext (valfri)"}],
     "returns": [{"field": "products[]", "desc": "Privata märkesvaror (EAN, namn, kedja, märke)"}]},
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

# Speditörer/aktörer för paket-/post-taggar (editerbar vokabulär, seedas en gång).
# classify_provider (regelbaserad seed) kan producera dessa; admin kan lägga till/ta bort
# och mappa råetiketter -> rätt speditör (provider_map-override, derive-at-read).
DEFAULT_PROVIDERS = ["PostNord", "DHL", "Schenker", "DSV", "Instabox", "Budbee", "Bring"]

# label = visningsnamn, color = markörfärg (kedjans profil), offers = stöder erbjudande-länk
CHAIN_META = {
    "ica":    {"label": "ICA",    "color": "#e3000b", "auth": "bearer",           "offers": True},
    "coop":   {"label": "Coop",   "color": "#00a651", "auth": "subscription_key", "offers": True},
    "willys": {"label": "Willys", "color": "#b71c1c", "auth": "none",             "offers": True},
    "hemkop": {"label": "Hemköp", "color": "#f57c00", "auth": "none",             "offers": True},
    "lidl":   {"label": "Lidl",   "color": "#0050aa", "auth": "apikey",           "offers": False},
    "citygross": {"label": "City Gross", "color": "#6a3d9a", "auth": "none",      "offers": True},
}

# Lidl geo-svep: API:t ger bara butiker inom en geo_box, så vi sveper ett rutnät.
# (lat_min, lng_min, lat_max, lng_max) som täcker Sverige.
SWEDEN_BOUNDS = (55.0, 10.8, 69.2, 24.3)
LIDL_BOX_DLAT = 1.0
LIDL_BOX_DLNG = 2.0
LIDL_SLEEP = 0.12  # snäll paus mellan anrop (sekunder)
