"""Vokabulär-/normaliseringsadministration för konsolen: kategorier, tillverkare, taggar
(typer + label-override) och speditörer. Alla mappningar är derive-at-read - en POST/DELETE
skriver DB:n OCH laddar om modulens karta (`set_map`/`put`/...) så ändringen slår igenom direkt
utan omsynk.

Router utan prefix med fulla paths (`/v1/admin/...`, `/v1/tags/...`, `/v1/providers/...`) -
URL:erna är byte-identiska med när de bodde i `main.py`. Hela gruppen gatas av `require_admin`
på router-nivå (samma som tidigare per-route).
"""
import re

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from .. import auth, categories, config, database, manufacturers, tags

router = APIRouter(dependencies=[Depends(auth.require_admin)])


# ---- Kategorier ----
@router.get("/v1/admin/categories")
async def admin_categories():
    return {"canonical": categories.CANONICAL, "items": database.category_label_counts()}


@router.post("/v1/admin/categories/map")
async def set_category(payload: dict = Body(...)):
    ck = (payload.get("chain_key") or "").strip()
    rk = (payload.get("raw_key") or "").strip()
    canon = (payload.get("canonical") or "").strip()
    if not ck or not rk or canon not in {c["key"] for c in categories.CANONICAL}:
        return JSONResponse({"detail": "Ogiltig mappning."}, status_code=400)
    database.set_category_map(ck, rk, canon)
    categories.set_map(database.load_category_map())  # ladda om -> slår igenom direkt
    return {"chain_key": ck, "raw_key": rk, "canonical": canon}


# ---- Tillverkare/varumärken ----
@router.get("/v1/admin/manufacturers")
async def admin_manufacturers():
    """Tillverkar-/varumärkesgrupper (auto-normaliserade på nyckel) + ev. kanonisk override - för
    redigering. Auto-normalisering (skiftläge/legal-suffix) sker i koden; här sätts manuella merges."""
    return {"items": database.manufacturer_rows()}


@router.post("/v1/admin/manufacturers/map")
async def set_manufacturer(payload: dict = Body(...)):
    key = (payload.get("key") or "").strip()
    canon = (payload.get("canonical") or "").strip()
    if not key:
        return JSONResponse({"detail": "Nyckel krävs."}, status_code=400)
    if canon:
        database.set_manufacturer_map(key, canon)
    else:
        database.delete_manufacturer_map(key)  # tom -> rensa override (faller till auto-default)
    manufacturers.set_map(database.load_manufacturer_map())  # ladda om -> slår igenom direkt
    return {"key": key, "canonical": canon or None}


# ---- Taggar (label -> typer) ----
@router.get("/v1/tags")
async def list_tags():
    items = []
    for label, info in database.tag_label_counts().items():
        types = tags.effective_types(label)
        items.append(
            {
                "label": label,
                "count": info["count"],
                "chains": sorted(info["chains"]),
                "types": types,
                "provider": tags.effective_provider(label),
                "provider_overridden": label in tags.PROVIDER_MAP,
                "overridden": label in tags.TAG_MAP,
            }
        )
    # Behöver-uppmärksamhet (ej override och bara "other") först, sedan på antal.
    items.sort(key=lambda x: (x["overridden"] or x["types"] != ["other"], -x["count"]))
    return {"types": tags.CANONICAL, "providers": tags.PROVIDERS, "tags": items}


@router.post("/v1/tags/map")
async def set_tag(payload: dict = Body(...)):
    label = (payload.get("label") or "").strip()
    types = [t for t in (payload.get("types") or []) if tags.valid_type(t)]
    if not label or not types:
        return JSONResponse({"detail": "Ogiltig label eller typer."}, status_code=400)
    database.set_tag_map(label, types)
    tags.put(label, types)
    return {"label": label, "types": types}


# ---- Kanonisk vokabulär (typer) ----
@router.get("/v1/tags/types")
async def list_types():
    return {"types": tags.CANONICAL, "builtin": sorted(config.BUILTIN_TAG_TYPES)}


@router.post("/v1/tags/types")
async def add_type(payload: dict = Body(...)):
    raw = (payload.get("type") or "").strip().lower()
    for a, b in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e"), ("ü", "u")):
        raw = raw.replace(a, b)
    type_ = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")
    if not type_:
        return JSONResponse({"detail": "Ogiltig typ."}, status_code=400)
    if type_ not in tags.CANONICAL:
        database.add_tag_type(type_)
        tags.set_types(database.load_tag_types())
    return {"type": type_, "types": tags.CANONICAL}


@router.delete("/v1/tags/types/{type_}")
async def remove_type(type_: str):
    # Även inbyggda typer får tas bort. Följden: en seed-producerad typ utan vokabulär-
    # post faller till 'other' (effective_types filtrerar mot vokabulären). Tombstone
    # (remove_tag_type) hindrar att den återskapas vid omstart. Manuella mappningar
    # (tag_map) skyddas dock fortfarande.
    if database.tag_type_in_use(type_):
        return JSONResponse({"detail": "Typen används i en mappning."}, status_code=400)
    database.remove_tag_type(type_)
    tags.set_types(database.load_tag_types())
    return {"type": type_, "removed": True, "types": tags.CANONICAL}


@router.delete("/v1/tags/map/{label:path}")
async def del_tag(label: str):
    database.delete_tag_map(label)
    tags.remove(label)
    # Returnera auto-typerna så klienten kan uppdatera raden in-place (ingen omladdning).
    return {"label": label, "removed": True, "types": tags.effective_types(label)}


# ---- Speditörer (vokabulär + label-override) ----
@router.get("/v1/providers")
async def list_providers():
    return {"providers": tags.PROVIDERS}


@router.post("/v1/providers")
async def add_provider(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        return JSONResponse({"detail": "Ogiltigt namn."}, status_code=400)
    if name not in tags.PROVIDERS:
        database.add_provider(name)
        tags.set_providers(database.load_providers())
    return {"name": name, "providers": tags.PROVIDERS}


@router.delete("/v1/providers/{name}")
async def remove_provider(name: str):
    if database.provider_in_use(name):
        return JSONResponse({"detail": "Speditören används i en mappning."}, status_code=400)
    database.remove_provider(name)
    tags.set_providers(database.load_providers())
    return {"name": name, "removed": True, "providers": tags.PROVIDERS}


@router.post("/v1/tags/provider")
async def set_tag_provider(payload: dict = Body(...)):
    label = (payload.get("label") or "").strip()
    provider = (payload.get("provider") or "").strip()
    if not label or provider not in tags.PROVIDERS:
        return JSONResponse({"detail": "Ogiltig label eller speditör."}, status_code=400)
    database.set_provider_map(label, provider)
    tags.put_provider(label, provider)
    return {"label": label, "provider": provider}


@router.delete("/v1/tags/provider/{label:path}")
async def del_tag_provider(label: str):
    database.delete_provider_map(label)
    tags.remove_provider(label)
    return {"label": label, "removed": True, "provider": tags.effective_provider(label)}
