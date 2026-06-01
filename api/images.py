"""Unified produktbild per EAN: hittar en bild-URL (ur cachade offers, annars ICA:s
EAN-nyckade bild-CDN), laddar ner och cachar bytes lokalt -> CDN-oberoende + snabbt.

v1: en storlek per EAN. Variant-/storleksval (thumb/full) är nästa steg.
"""

import logging

import httpx

from . import config, database as db

log = logging.getLogger("matbutiker")

IMG_DIR = config.DB_PATH.parent / "image_cache"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
# ICA hostar manufakturbilder per GTIN -> universell fallback (branded varor).
_ICA_CDN = "https://assets.icanet.se/q_auto,f_auto/c_lpad,h_400,w_400,e_sharpen:70/{ean}.jpg"


def _sized(url, px=400):
    """Begränsa storleken via Cloudinary-transform (Coop/Axfood = `/image/upload/`).
    Annars (ICA-formatet) är URL:en redan storleks-transformerad -> oförändrad."""
    marker = "/image/upload/"
    if marker in url:
        head, tail = url.split(marker, 1)
        return f"{head}{marker}c_limit,w_{px},h_{px},f_auto,q_auto/{tail}"
    return url


def _resolve_url(ean):
    """En bild-URL för EAN:en: först ur cachade offers (kedjornas egna bilder, matchar
    vår data), annars ICA:s EAN-CDN."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT image FROM offers WHERE image IS NOT NULL AND image!='' AND eans LIKE ? LIMIT 1",
        (f'%"{ean}"%',),
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT o.image FROM offers o JOIN ean_cache e ON e.code=o.offer_id "
            "WHERE e.ean=? AND o.image IS NOT NULL AND o.image!='' LIMIT 1",
            (ean,),
        ).fetchone()
    conn.close()
    return row["image"] if row else _ICA_CDN.format(ean=ean)


async def get_cached_image(ean):
    """(path, content_type) för EAN:ens produktbild - hämtar+cachar vid behov, None om ingen."""
    path = IMG_DIR / str(ean)
    meta = db.get_image_meta(ean)
    if meta and path.exists():
        return path, meta["content_type"]
    url = _sized(_resolve_url(ean))
    if not url:
        return None
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as cl:
            r = await cl.get(url, headers={"User-Agent": UA})
    except Exception as e:  # noqa: BLE001
        log.warning("produktbild %s misslyckades: %s", ean, e)
        return None
    ct = r.headers.get("content-type", "").split(";")[0].strip()
    if r.status_code != 200 or not ct.startswith("image/"):
        return None
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    db.save_image_meta(ean, ct, url)
    return path, ct
