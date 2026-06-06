"""SQLAlchemy Core-tabelldefinitioner (DB-oberoende sanningskälla för schemat).

Ett `MetaData` med alla `Table`-objekt -> `metadata.create_all(engine)` bygger schemat på
valfri dialekt (SQLite/Postgres). Typval medvetna för portabilitet (advisor-granskat):
- Priser/flyttal: `Float` (PG `double precision`, matchar SQLites 8-byte REAL) - INTE `REAL`
  (skulle bli PG `real`/float4, ~7 sig. siffror).
- 0/1-flaggor: `Integer` (koden jämför `=1`/`bool(...)`) - INTE `Boolean`.
- Auto-id: `Integer, primary_key=True` (en kolumn) -> SERIAL på PG, AUTOINCREMENT på SQLite.
- `server_default` där INSERTs utelämnar kolumnen (enabled/priority/revoked/is_admin/available/
  stats-räknare) så DB-defaulten gäller även för rå text()-insert.
"""

from sqlalchemy import Column, Float, Index, Integer, MetaData, Table, Text, text

metadata = MetaData()

_Z = text("0")
_ONE = text("1")

stores = Table(
    "stores", metadata,
    Column("chain", Text, primary_key=True),
    Column("store_id", Text, primary_key=True),
    Column("name", Text), Column("brand", Text), Column("street", Text),
    Column("postal_code", Text), Column("city", Text),
    Column("lat", Float), Column("lng", Float),
    Column("phone", Text), Column("email", Text), Column("oh_today", Text),
    Column("open_now", Integer),
    Column("link_store", Text), Column("link_offers", Text), Column("link_online", Text),
    Column("tags", Text), Column("raw", Text), Column("native", Text),
    Column("method", Text), Column("fetched_at", Text), Column("hours", Text),
    Index("idx_stores_chain", "chain"),
    Index("idx_stores_city", "city"),
)

offers = Table(
    "offers", metadata,
    Column("chain", Text, primary_key=True),
    Column("store_id", Text, primary_key=True),
    Column("offer_id", Text, primary_key=True),
    Column("name", Text), Column("brand", Text), Column("package", Text),
    Column("price", Float), Column("price_text", Text), Column("comparison_price", Text),
    Column("comparison_value", Float), Column("comparison_unit", Text),
    Column("category_raw", Text), Column("category_id", Integer), Column("mechanic_type", Text),
    Column("valid_to", Text), Column("eans", Text), Column("image", Text),
    Column("member_price", Integer), Column("fetched_at", Text), Column("savings", Float),
    Index("idx_offers_store", "chain", "store_id"),
)

offer_observations = Table(
    "offer_observations", metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", Text, nullable=False), Column("store_id", Text, nullable=False),
    Column("offer_id", Text, nullable=False),
    Column("ean", Text), Column("name", Text), Column("price", Float),
    Column("comparison_value", Float), Column("comparison_unit", Text),
    Column("valid_to", Text), Column("observed_at", Text),
    Column("savings", Float), Column("member_price", Integer),
    Index("idx_obs_offer", "chain", "store_id", "offer_id", "id"),
    Index("idx_obs_ean", "ean"),
)

ean_cache = Table(
    "ean_cache", metadata,
    Column("code", Text, primary_key=True),
    Column("ean", Text), Column("fetched_at", Text),
    Column("category", Text), Column("origin", Text),
)

offer_eans = Table(
    "offer_eans", metadata,
    Column("chain", Text, primary_key=True),
    Column("store_id", Text, primary_key=True),
    Column("offer_id", Text, primary_key=True),
    Column("ean", Text, primary_key=True),
    Index("idx_offer_eans_ean", "ean"),
)

catalog_products = Table(
    "catalog_products", metadata,
    Column("chain", Text, primary_key=True),
    Column("product_id", Text, primary_key=True),
    Column("ean", Text), Column("name", Text), Column("brand", Text),
    Column("image", Text), Column("origin", Text),
    Column("price", Float), Column("comparison_value", Float), Column("comparison_unit", Text),
    Column("package_size", Text), Column("package_value", Float), Column("package_unit", Text),
    Column("category_raw", Text), Column("available", Integer, server_default=_ONE),
    Column("first_seen", Text), Column("last_seen", Text), Column("fetched_at", Text),
    Column("store", Text),
    Column("price_min", Float), Column("price_max", Float), Column("price_stores", Integer),
    Index("idx_catalog_ean", "ean"),
    Index("idx_catalog_chain_cat", "chain", "category_raw"),
)

catalog_price_observations = Table(
    "catalog_price_observations", metadata,
    Column("id", Integer, primary_key=True),
    Column("chain", Text), Column("product_id", Text), Column("ean", Text),
    Column("price", Float), Column("comparison_value", Float), Column("comparison_unit", Text),
    Column("observed_at", Text), Column("store", Text),
    Index("idx_cpo_product", "chain", "product_id"),
    Index("idx_cpo_ean", "ean"),
    Index("idx_cpo_store", "chain", "product_id", "store"),
)

catalog_store_prices = Table(
    "catalog_store_prices", metadata,
    Column("chain", Text, primary_key=True),
    Column("product_id", Text, primary_key=True),
    Column("store", Text, primary_key=True),
    Column("ean", Text), Column("price", Float),
    Column("comparison_value", Float), Column("comparison_unit", Text),
    Column("available", Integer, server_default=_ONE),
    Column("first_seen", Text), Column("last_seen", Text),
    Index("idx_csp_ean", "ean"),
    Index("idx_csp_chain_store", "chain", "store"),
    Index("idx_csp_store", "store"),
    Index("idx_csp_chain_product", "chain", "product_id"),
    # Steg 6 zon-browse: täckande index så PG kan bitmap-scanna IN-listan utan hint
    # (SQLite-bräckligheten som motiverade Postgres-bytet). Inkluderar price för index-only scan.
    Index("idx_csp_cover", "chain", "store", "product_id", "price"),
)

store_crawl = Table(
    "store_crawl", metadata,
    Column("chain", Text, primary_key=True),
    Column("store", Text, primary_key=True),
    Column("queryable", Integer), Column("enabled", Integer, server_default=_Z),
    Column("priority", Integer, server_default=_Z),
    Column("last_crawled", Text), Column("product_count", Integer),
    Column("status", Text), Column("checked_at", Text),
    Column("name", Text), Column("city", Text), Column("store_count", Integer),
    Index("idx_store_crawl_enabled", "enabled", "priority"),
)

store_price_volume = Table(
    "store_price_volume", metadata,
    Column("chain", Text, primary_key=True),
    Column("price_rows", Integer), Column("price_stores", Integer), Column("updated", Text),
)

ica_walk_categories = Table(
    "ica_walk_categories", metadata,
    Column("name", Text, primary_key=True), Column("last_seen", Text),
)

crawl_runs = Table(
    "crawl_runs", metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", Text, nullable=False), Column("chain", Text, nullable=False),
    Column("started", Text), Column("finished", Text), Column("status", Text),
    Column("rows", Integer, server_default=_Z), Column("changed", Integer, server_default=_Z),
    Column("errors", Integer, server_default=_Z),
    Column("stores_ok", Integer), Column("stores_total", Integer),
    Column("last_error", Text), Column("error_summary", Text),
    Index("idx_crawl_runs_chain", "kind", "chain", "id"),
)

tag_map = Table(
    "tag_map", metadata,
    Column("label", Text, primary_key=True), Column("types", Text),
)

category_map = Table(
    "category_map", metadata,
    Column("chain_key", Text, primary_key=True),
    Column("raw_key", Text, primary_key=True),
    Column("canonical", Text),
)

manufacturer_map = Table(
    "manufacturer_map", metadata,
    Column("key", Text, primary_key=True), Column("canonical", Text),
)

tag_types = Table("tag_types", metadata, Column("type", Text, primary_key=True))
tag_types_removed = Table("tag_types_removed", metadata, Column("type", Text, primary_key=True))
providers = Table("providers", metadata, Column("name", Text, primary_key=True))
provider_map = Table(
    "provider_map", metadata,
    Column("label", Text, primary_key=True), Column("provider", Text),
)

api_calls = Table(
    "api_calls", metadata,
    Column("id", Integer, primary_key=True),
    Column("ts", Float), Column("method", Text), Column("host", Text), Column("path", Text),
    Column("status", Integer), Column("ms", Float), Column("chain", Text),
)

api_call_stats = Table(
    "api_call_stats", metadata,
    Column("host", Text, primary_key=True), Column("chain", Text),
    Column("count", Integer, server_default=_Z), Column("errors", Integer, server_default=_Z),
    Column("total_ms", Float, server_default=_Z),
)

settings = Table(
    "settings", metadata,
    Column("key", Text, primary_key=True), Column("value", Text),
)

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("email", Text, unique=True, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("is_admin", Integer, server_default=_Z),
    Column("created_at", Text),
)

favorites = Table(
    "favorites", metadata,
    Column("user_id", Integer, primary_key=True),
    Column("chain", Text, primary_key=True),
    Column("store_id", Text, primary_key=True),
)

admin_users = Table(
    "admin_users", metadata,
    Column("id", Integer, primary_key=True),
    Column("email", Text, unique=True, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("created_at", Text),
)

private_brands = Table(
    "private_brands", metadata,
    Column("chain", Text, primary_key=True), Column("brand", Text, primary_key=True),
)

product_matches = Table(
    "product_matches", metadata,
    Column("group_id", Integer, nullable=False),
    Column("chain", Text, primary_key=True),
    Column("ean", Text, primary_key=True),
    Column("name", Text), Column("brand", Text), Column("package", Text),
)

product_info = Table(
    "product_info", metadata,
    Column("ean", Text, primary_key=True), Column("data", Text), Column("fetched_at", Text),
)

product_info_observations = Table(
    "product_info_observations", metadata,
    Column("id", Integer, primary_key=True),
    Column("ean", Text, nullable=False), Column("source", Text, nullable=False),
    Column("ingredients", Text), Column("nutrition", Text), Column("origin", Text),
    Column("observed_at", Text),
    Index("idx_pinfo_obs", "ean", "source", "id"),
)

ica_item_map = Table(
    "ica_item_map", metadata,
    Column("ean", Text, primary_key=True), Column("cid", Text), Column("fetched_at", Text),
)

product_images = Table(
    "product_images", metadata,
    Column("ean", Text, primary_key=True), Column("size", Text, primary_key=True),
    Column("content_type", Text), Column("source_url", Text), Column("fetched_at", Text),
)

user_tokens = Table(
    "user_tokens", metadata,
    Column("id", Integer, primary_key=True),
    Column("token_hash", Text, unique=True, nullable=False),
    Column("user_id", Integer, nullable=False),
    Column("label", Text), Column("created_at", Text), Column("last_used", Text),
)

api_keys = Table(
    "api_keys", metadata,
    Column("id", Integer, primary_key=True),
    Column("key_hash", Text, unique=True, nullable=False),
    Column("prefix", Text), Column("label", Text), Column("created_at", Text),
    Column("revoked", Integer, server_default=_Z), Column("last_used", Text),
)

# Autoincrement-tabeller (id INTEGER PK) - för sequence-reset efter bulk-migrering (se migrate-skriptet).
SERIAL_TABLES = ("offer_observations", "catalog_price_observations", "users",
                 "admin_users", "user_tokens", "api_keys", "crawl_runs", "api_calls",
                 "product_info_observations")
