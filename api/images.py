"""Unified produktbild per EAN: väljer bästa bild-URL (resizebar cloudinary ur cachade
offers, annars ICA:s EAN-nyckade bild-CDN), resizar via Cloudinary-transform och cachar
bytes lokalt per (ean, storlek) -> CDN-oberoende + snabbt. Storlekar: thumb/default/full.
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
    """Begränsa storleken via Cloudinary-transform. Coop/ICA tillåter att vi kedjar in en
    c_limit-transform. Axfood AVVISAR godtyckliga (osignerade) transforms (401) - bara deras
    pre-bakade finns: `t_200` (~200px) eller `f_auto` (full-res). Stora storlekar -> full,
    annars behåll t_200. ICA-EAN-CDN är redan storleks-transformerad -> oförändrad."""
    if "assets.axfood.se" in url:
        if px >= 600:  # lightbox/full -> full-res (Axfood saknar en mellan-transform vi får använda)
            head, _, tail = url.partition("/image/upload/")
            _, _, rest = tail.partition("/")  # släng transform-segmentet (t.ex. "f_auto,t_200")
            return f"{head}/image/upload/f_auto/{rest}"
        return url
    marker = "/image/upload/"
    if marker in url:
        head, tail = url.split(marker, 1)
        return f"{head}{marker}c_limit,w_{px},h_{px},f_auto,q_auto/{tail}"
    return url


# Bildkälle-preferens per kedja (lägre = bättre): Coop/Axfood är resizebara cloudinary
# (`/image/upload/`); ICA:s offer-bilder är 200px lpad och ej resizebara -> hellre ICA:s
# EAN-CDN (400px) för dem. Coops rena upload föredras (Axfood har ofta en t_200-transform).
_CHAIN_IMG_PREF = {"coop": 0, "willys": 1, "hemkop": 1, "lidl": 3}


def _resolve_url(ean):
    """Bästa bild-URL:en för EAN:en: en resizebar cloudinary-bild ur cachade offers
    (föredras, Coop före Axfood), annars ICA-detaljens bild ur product_info (resizebar,
    täcker ICA:s egna märken utan offer-bild), annars ICA:s EAN-CDN (400px)."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT chain, image FROM offers WHERE image IS NOT NULL AND image!='' AND eans LIKE ?",
        (f'%"{ean}"%',),
    ).fetchall()
    rows += conn.execute(
        "SELECT o.chain, o.image FROM offers o JOIN ean_cache e ON e.code=o.offer_id "
        "WHERE e.ean=? AND o.image IS NOT NULL AND o.image!=''",
        (ean,),
    ).fetchall()
    pi = conn.execute(
        "SELECT json_extract(data,'$.image') AS img FROM product_info WHERE ean=?", (ean,)
    ).fetchone()
    conn.close()
    resizable = [r for r in rows if "/image/upload/" in (r["image"] or "")]
    if resizable:
        resizable.sort(key=lambda r: _CHAIN_IMG_PREF.get(r["chain"], 5))
        return resizable[0]["image"]
    if pi and pi["img"] and "/image/upload/" in pi["img"]:
        return pi["img"]
    return _ICA_CDN.format(ean=ean)


# Valbara storleksvarianter (max-dimension i px, begränsas via Cloudinary-transform).
SIZES = {"thumb": 150, "default": 400, "full": 800}


async def get_cached_image(ean, size="default"):
    """(path, content_type) för EAN:ens produktbild i vald storlek - hämtar+cachar vid
    behov, None om ingen. Varianter cachas separat per (ean, size)."""
    px = SIZES.get(size) or SIZES["default"]
    path = IMG_DIR / f"{ean}_{size}"
    meta = db.get_image_meta(ean, size)
    if meta and path.exists():
        return path, meta["content_type"]
    url = _sized(_resolve_url(ean), px)
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
    db.save_image_meta(ean, size, ct, url)
    return path, ct
