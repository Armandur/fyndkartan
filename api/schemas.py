"""Pydantic-responsmodeller - sanningskälla för API-kontraktet.

Modellerna kopplas DOKUMENTERANDE (inte enforcing) till routes via
`responses={200: {"model": M}}`, så de syns i /docs men re-serialiserar inte svaren
(inga fält tappas). Konsolens fält-dokumentation deriveras ur samma modeller
(`fields_doc`), så modellerna är enda källan för både /docs och konsolen. Ett litet
drift-test validerar verkliga svar mot modellerna.
"""

from pydantic import BaseModel, Field


def fields_doc(model):
    """[{field, desc}] ur en modells fält + Field(description=...). Driver konsolens
    'Returnerar'-lista så modellen är enda sanningskällan."""
    return [{"field": n, "desc": f.description or ""} for n, f in model.model_fields.items()]


class Product(BaseModel):
    """Distinkt produkt ur erbjudande-cachen (produktsök + kategori-bläddring)."""

    ean: str | None = Field(None, description="EAN/GTIN, eller null om okänd")
    name: str | None = Field(None, description="Produktnamn")
    brand: str | None = Field(None, description="Varumärke (ursprung utbrutet till origin)")
    origin: list[str] | None = Field(None, description="Ursprungsländer (lista) eller null")
    image: str | None = Field(None, description="Representativ bild-URL")
    category: str | None = Field(None, description="Kanonisk kategori-nyckel (se /v1/categories)")
    package_size: str | None = Field(None, description="Normaliserad förpacknings-storlek (sträng)")
    package_value: float | None = Field(None, description="Förpackningens mängd (numeriskt) eller null")
    package_unit: str | None = Field(None, description="Förpackningens enhet (g/kg/l/st...) eller null")
    deal_type: str | None = Field(None, description="flat | multibuy | by_weight")
    multibuy_qty: int | None = Field(None, description="Antal vid multibuy, annars null")
    chains: list[str] = Field(..., description="Kedjor produkten finns hos")
    offer_count: int = Field(..., description="Antal cachade butiks-erbjudanden (ej totalt antal butiker)")
    price_min: float | None = Field(None, description="Lägsta pris i kr")
    price_max: float | None = Field(None, description="Högsta pris i kr")


class ProductSearchResponse(BaseModel):
    query: str = Field(..., description="Söktexten")
    count: int = Field(..., description="Antal träffar")
    products: list[Product] = Field(..., description="Distinkta produkter")


class ProductCategoryResponse(BaseModel):
    category: str = Field(..., description="Kanonisk kategori-nyckel")
    count: int = Field(..., description="Antal produkter")
    products: list[Product] = Field(..., description="Distinkta produkter i kategorin")
