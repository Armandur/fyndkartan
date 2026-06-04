import json

from ._conn import _ensure_column, get_conn
from ..categories import raw_key
from ..config import (
    BUILTIN_TAG_TYPES, COOP_DETAIL_STORE, DB_PATH, DEFAULT_CATEGORY_MAP, DEFAULT_PRIVATE_BRANDS,
    DEFAULT_PROVIDERS, DEFAULT_TAG_TYPES,
)


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stores (
            chain       TEXT NOT NULL,
            store_id    TEXT NOT NULL,
            name        TEXT,
            brand       TEXT,
            street      TEXT,
            postal_code TEXT,
            city        TEXT,
            lat         REAL,
            lng         REAL,
            phone       TEXT,
            email       TEXT,
            oh_today    TEXT,
            open_now    INTEGER,
            link_store  TEXT,
            link_offers TEXT,
            link_online TEXT,
            tags        TEXT,
            raw         TEXT,
            native      TEXT,
            method      TEXT,
            fetched_at  TEXT,
            PRIMARY KEY (chain, store_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stores_chain ON stores(chain)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stores_city ON stores(city)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS offers (
            chain            TEXT NOT NULL,
            store_id         TEXT NOT NULL,
            offer_id         TEXT NOT NULL,
            name             TEXT,
            brand            TEXT,
            package          TEXT,
            price            REAL,
            price_text       TEXT,
            comparison_price TEXT,
            comparison_value REAL,
            comparison_unit  TEXT,
            category_raw     TEXT,
            category_id      INTEGER,
            mechanic_type    TEXT,
            valid_to         TEXT,
            eans             TEXT,
            image            TEXT,
            member_price     INTEGER,
            savings          REAL,
            fetched_at       TEXT,
            PRIMARY KEY (chain, store_id, offer_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_offers_store ON offers(chain, store_id)")
    # Prishistorik (steg 4): append-only observationer av offers. offers churnar vid varje synk
    # (replace), så historiken måste skrivas separat. En rad per offer NÄR priset/jämförpriset/
    # valid_to ändrats sedan senaste observationen (dedup) -> en kompakt prisförändrings-logg.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS offer_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain TEXT NOT NULL, store_id TEXT NOT NULL, offer_id TEXT NOT NULL,
            ean TEXT, name TEXT, price REAL, comparison_value REAL, comparison_unit TEXT,
            savings REAL, member_price INTEGER, valid_to TEXT, observed_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_offer ON offer_observations(chain, store_id, offer_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_ean ON offer_observations(ean)")
    # Axfood code -> EAN, butiksoberoende och persistent (överlever offers-refresh).
    # ean = "" markerar "resolvad, ingen EAN" så vi slipper hämta om.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ean_cache (code TEXT PRIMARY KEY, ean TEXT, fetched_at TEXT)"
    )
    # Normaliserat offer -> EAN-index (inline för ICA/Coop/CG, Axfood-kod resolvat ur ean_cache).
    # Fylls write-path i replace_store_offers; ersätter json_each-scans + Axfood-reverse-resolve i
    # läsvägarna (stores_with_offer/offers_for_eans). Indexerat på ean.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS offer_eans (chain TEXT, store_id TEXT, offer_id TEXT, ean TEXT, "
        "PRIMARY KEY (chain, store_id, offer_id, ean))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_offer_eans_ean ON offer_eans(ean)")
    # Engångs-backfill ur befintliga offers (inline + Axfood via ean_cache) om tomt.
    if conn.execute("SELECT COUNT(*) FROM offer_eans").fetchone()[0] == 0:
        conn.execute(
            "INSERT OR IGNORE INTO offer_eans SELECT o.chain, o.store_id, o.offer_id, je.value "
            "FROM offers o, json_each(o.eans) je WHERE o.eans NOT IN ('','[]')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO offer_eans SELECT o.chain, o.store_id, o.offer_id, e.ean "
            "FROM offers o JOIN ean_cache e ON o.offer_id=e.code "
            "WHERE o.chain IN ('willys','hemkop') AND e.ean!=''"
        )
    # Fulla sortiment (steg 5): persistent produktkatalog per kedja (allt de säljer, ej bara offers).
    # En rad per (chain, product_id); EAN-gruppering vid läsning. `origin` = JSON-lista. `available`
    # = sedd i senaste fullständiga crawl (utgångna behålls). Kanonisk kategori härleds vid läsning.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS catalog_products (
            chain TEXT NOT NULL, product_id TEXT NOT NULL,
            ean TEXT, name TEXT, brand TEXT, image TEXT, origin TEXT,
            price REAL, comparison_value REAL, comparison_unit TEXT,
            package_size TEXT, package_value REAL, package_unit TEXT,
            category_raw TEXT, available INTEGER DEFAULT 1,
            first_seen TEXT, last_seen TEXT, fetched_at TEXT,
            PRIMARY KEY (chain, product_id)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_ean ON catalog_products(ean)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_chain_cat ON catalog_products(chain, category_raw)")
    # Hyllpris-historik: append-only observation NÄR ett katalog-pris ändras vid en crawl (speglar
    # offer_observations men för hyllpris). En rad per (chain, product_id) vid prisändring + första pris.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS catalog_price_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain TEXT, product_id TEXT, ean TEXT,
            price REAL, comparison_value REAL, comparison_unit TEXT, observed_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cpo_product ON catalog_price_observations(chain, product_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cpo_ean ON catalog_price_observations(ean)")
    # Engångs-baslinje: seeda nuvarande pris för redan cachade katalogprodukter (annars saknar de
    # startpunkt tills priset ändras). Körs en gång (när observations-tabellen är tom).
    if conn.execute("SELECT COUNT(*) FROM catalog_price_observations").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO catalog_price_observations "
            "(chain, product_id, ean, price, comparison_value, comparison_unit, observed_at) "
            "SELECT chain, product_id, ean, price, comparison_value, comparison_unit, fetched_at "
            "FROM catalog_products WHERE price IS NOT NULL"
        )
    # Editerbar mappning råetikett -> lista av kanoniska typer (JSON, admin-override).
    _cols = {r[1] for r in conn.execute("PRAGMA table_info(tag_map)")}
    if _cols and "types" not in _cols:  # migrera bort gammalt enkel-typ-schema
        conn.execute("DROP TABLE tag_map")
    conn.execute("CREATE TABLE IF NOT EXISTS tag_map (label TEXT PRIMARY KEY, types TEXT)")
    # Kategori-mappning (chain_key, raw_key) -> kanonisk; seedas första gången.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS category_map (chain_key TEXT, raw_key TEXT, canonical TEXT, "
        "PRIMARY KEY (chain_key, raw_key))"
    )
    # Alltid INSERT OR IGNORE -> nya seed-nycklar (t.ex. coop_nav) läggs till vid
    # uppgradering utan att skriva över admin-ändringar.
    conn.executemany(
        "INSERT OR IGNORE INTO category_map (chain_key, raw_key, canonical) VALUES (?,?,?)",
        [(ck, rk, canon) for (ck, rk), canon in DEFAULT_CATEGORY_MAP.items()],
    )
    # Tillverkar-/varumärkesnormalisering: grupperingsnyckel -> kanoniskt display-namn (override).
    # Auto-normalisering (skiftläge/legal-suffix) sker i koden; tabellen är manuella merges.
    conn.execute("CREATE TABLE IF NOT EXISTS manufacturer_map (key TEXT PRIMARY KEY, canonical TEXT)")
    # Editerbar kanonisk vokabulär; seedas med default-listan första gången.
    conn.execute("CREATE TABLE IF NOT EXISTS tag_types (type TEXT PRIMARY KEY)")
    # Tombstone: typer användaren tagit bort. Hindrar att inbyggda återskapas vid omstart.
    conn.execute("CREATE TABLE IF NOT EXISTS tag_types_removed (type TEXT PRIMARY KEY)")
    if not conn.execute("SELECT 1 FROM tag_types LIMIT 1").fetchone():
        conn.executemany("INSERT INTO tag_types (type) VALUES (?)", [(t,) for t in DEFAULT_TAG_TYPES])
    # Säkerställ att inbyggda (seed-producerade) typer finns - utom de användaren tagit bort.
    _removed = {r[0] for r in conn.execute("SELECT type FROM tag_types_removed")}
    conn.executemany(
        "INSERT OR IGNORE INTO tag_types (type) VALUES (?)",
        [(t,) for t in BUILTIN_TAG_TYPES if t not in _removed],
    )
    # Editerbar speditör-vokabulär (seedas en gång) + override-mappning label -> speditör.
    conn.execute("CREATE TABLE IF NOT EXISTS providers (name TEXT PRIMARY KEY)")
    if not conn.execute("SELECT 1 FROM providers LIMIT 1").fetchone():
        conn.executemany("INSERT INTO providers (name) VALUES (?)", [(p,) for p in DEFAULT_PROVIDERS])
    conn.execute("CREATE TABLE IF NOT EXISTS provider_map (label TEXT PRIMARY KEY, provider TEXT)")
    # Persistent anropslogg: ring-buffer (feed, beskärs) + kumulativ statistik per host.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS api_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, "
        "method TEXT, host TEXT, path TEXT, status INTEGER, ms REAL, chain TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS api_call_stats (host TEXT PRIMARY KEY, chain TEXT, "
        "count INTEGER DEFAULT 0, errors INTEGER DEFAULT 0, total_ms REAL DEFAULT 0)"
    )
    # Konton + favoriter + nyckel/värde-settings.
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL,
            chain TEXT NOT NULL,
            store_id TEXT NOT NULL,
            PRIMARY KEY (user_id, chain, store_id)
        )"""
    )
    # Admin-/konsolkonton är helt skilda från app-konton (users): egen tabell,
    # egen session (admin_uid). En app-användare har aldrig admin-behörighet.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT
        )"""
    )
    # Editerbar private-label-vokabulär (brand-rötter per kedja); seedas en gång.
    conn.execute("CREATE TABLE IF NOT EXISTS private_brands (chain TEXT, brand TEXT, PRIMARY KEY (chain, brand))")
    if not conn.execute("SELECT 1 FROM private_brands LIMIT 1").fetchone():
        conn.executemany(
            "INSERT OR IGNORE INTO private_brands (chain, brand) VALUES (?,?)",
            [(ch, b) for ch, bs in DEFAULT_PRIVATE_BRANDS.items() for b in bs],
        )
    # Manuell cross-chain-paring av märkesvaror. EAN-nycklad (stabil, överlever att
    # offers uppdateras). Snapshot av namn/brand/pkg så posten kan visas även när
    # erbjudandet försvunnit. En (chain, ean) tillhör som mest en grupp.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS product_matches (
            group_id INTEGER NOT NULL,
            chain TEXT NOT NULL,
            ean TEXT NOT NULL,
            name TEXT, brand TEXT, package TEXT,
            PRIMARY KEY (chain, ean)
        )"""
    )
    # Produktinfo per EAN (EAN-global: ingredienser/näring/ursprung), lazy-cachad.
    # Källan står i datan (`source`). Regenererbar -> gamla per-kedje-cachen släpps.
    conn.execute("DROP TABLE IF EXISTS product_details")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS product_info (ean TEXT PRIMARY KEY, data TEXT, fetched_at TEXT)"
    )
    # Historik för produktinnehåll PER KÄLLA (recept-/närings-/ursprungsändringar). Append-on-change:
    # en rad när Axf(Coop/ICA)s normaliserade ingredienser/näring/ursprung skiljer sig från den
    # senaste för (ean, source) -> kompakt ändringslogg. Per källa (ej den mergade raden) så
    # källvariation inte ser ut som receptändring (speglar offer_observations per butik). UI senare.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS product_info_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ean TEXT NOT NULL, source TEXT NOT NULL,
            ingredients TEXT, nutrition TEXT, origin TEXT, observed_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pinfo_obs ON product_info_observations(ean, source, id)")
    # EAN(13) -> ICA consumerItemId (detalj-URL:en). Söket scopar på butikssortiment, så
    # resolvern provar flera butiker; cid='' = försökt utan träff (negativ cache).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ica_item_map (ean TEXT PRIMARY KEY, cid TEXT, fetched_at TEXT)"
    )
    # Lokalt cachade produktbilder per (ean, storlek) (bytes på disk, metadata här) -
    # CDN-oberoende + snabbare. Migrera bort gammalt ean-PK-schema (cache regenererbar).
    _icols = {r[1] for r in conn.execute("PRAGMA table_info(product_images)")}
    if _icols and "size" not in _icols:
        conn.execute("DROP TABLE product_images")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS product_images (ean TEXT, size TEXT, content_type TEXT, "
        "source_url TEXT, fetched_at TEXT, PRIMARY KEY (ean, size))"
    )
    # Opaka bearer-tokens för slutanvändare (icke-webb-klienter). Lagras hashade.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            label TEXT, created_at TEXT, last_used TEXT
        )"""
    )
    # API-nycklar för externa integratörer (utfärdas i konsolen). Lagras hashade;
    # validering är valfri (gatar inte de öppna läs-endpoints) - ogiltig nyckel nekas dock.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT UNIQUE NOT NULL,
            prefix TEXT, label TEXT, created_at TEXT,
            revoked INTEGER DEFAULT 0, last_used TEXT
        )"""
    )
    # ALTER TABLE-guards för nya kolumner (ingen Alembic).
    _ensure_column(conn, "offers", "member_price", "INTEGER")
    _ensure_column(conn, "offers", "savings", "REAL")
    _ensure_column(conn, "ean_cache", "category", "TEXT")  # Axfood googleAnalyticsCategory (förvärmd)
    _ensure_column(conn, "ean_cache", "origin", "TEXT")  # Axfood ursprungsland (svenska, förvärmt)
    _ensure_column(conn, "offer_observations", "savings", "REAL")  # för att spåra ordinarie pris
    _ensure_column(conn, "offer_observations", "member_price", "INTEGER")
    _ensure_column(conn, "stores", "hours", "TEXT")  # JSON {week, exceptions} - normaliserad veckoöppettid
    # store: Coop-priser/sortiment är BUTIKSSPECIFIKA (perso-API:t scopar på ledger) - vi crawlar en
    # fast COOP_DETAIL_STORE. Tagga raden med ledger:t så priset inte misstas för nationellt; NULL =
    # nationellt/ej butiksscopat (Axfood/CG). ICA är också flaggskepps-scopat (se Kända datakälle-fakta).
    _ensure_column(conn, "catalog_products", "store", "TEXT")
    conn.execute("UPDATE catalog_products SET store=? WHERE chain='coop' AND store IS NULL",
                 (COOP_DETAIL_STORE,))  # backfill (crawlat med fast butik innan kolumnen fanns)
    conn.commit()
    conn.close()
