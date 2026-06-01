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


# ---- Butiker ----
class Address(BaseModel):
    street: str | None = Field(None, description="Gatuadress")
    postal_code: str | None = Field(None, description="Postnummer")
    city: str | None = Field(None, description="Ort")


class Location(BaseModel):
    lat: float = Field(..., description="Latitud")
    lng: float = Field(..., description="Longitud")


class Contact(BaseModel):
    phone: str | None = Field(None, description="Telefon (svenskt standardformat)")
    email: str | None = Field(None, description="E-post")


class Links(BaseModel):
    store_page: str | None = Field(None, description="Butikssida")
    offers: str | None = Field(None, description="Erbjudande-sida")
    online_shopping: str | None = Field(None, description="E-handel")


class Tag(BaseModel):
    types: list[str] = Field(..., description="Kanoniska taggtyper (en etikett kan ha flera)")
    label: str = Field(..., description="Kedjans råetikett")


class WeekDay(BaseModel):
    day: int = Field(..., description="Veckodag, 0=måndag .. 6=söndag")
    closed: bool = Field(..., description="Stängd den dagen")
    opens: str | None = Field(None, description="Öppnar HH:MM")
    closes: str | None = Field(None, description="Stänger HH:MM")


class HoursException(BaseModel):
    date: str | None = Field(None, description="Datum YYYY-MM-DD, eller null")
    label: str | None = Field(None, description="Helg-/avvikelsenamn")
    closed: bool = Field(..., description="Stängd")
    opens: str | None = Field(None, description="Öppnar HH:MM")
    closes: str | None = Field(None, description="Stänger HH:MM")


class OpeningHours(BaseModel):
    today: str | None = Field(None, description="Dagens öppettid HH:MM-HH:MM")
    open_now: bool | None = Field(None, description="Öppet nu (om kedjan rapporterar status)")
    week: list[WeekDay] | None = Field(None, description="Normaliserad vecka")
    exceptions: list[HoursException] | None = Field(None, description="Daterade avvikelser (helgdagar)")
    raw: dict | None = Field(None, description="Kedjans råformat, oförändrat")


class Store(BaseModel):
    chain: str = Field(..., description="Kedja")
    store_id: str = Field(..., description="Butiks-id (per kedja)")
    name: str | None = Field(None, description="Butiksnamn")
    brand: str | None = Field(None, description="Kedjeprofil/varumärke")
    address: Address = Field(..., description="Gata, postnummer, ort")
    location: Location | None = Field(None, description="lat/lng, null om position saknas")
    contact: Contact = Field(..., description="Telefon + e-post")
    opening_hours: OpeningHours = Field(..., description="today, open_now, week, exceptions, raw")
    tags: list[Tag] = Field(..., description="Normaliserade tjänste-taggar")
    links: Links = Field(..., description="Butikssida, erbjudanden, e-handel")
    native: dict | None = Field(None, description="Kedjans sekundär-id:n")
    source: dict | None = Field(None, description="method + fetched_at")


class StoreNear(Store):
    distance_km: float = Field(..., description="Avstånd till sökpunkten (km)")


class StoresResponse(BaseModel):
    count: int = Field(..., description="Antal butiker")
    stores: list[Store] = Field(..., description="Butiker")


class StoresNearResponse(BaseModel):
    count: int = Field(..., description="Antal butiker")
    generated_at: str | None = Field(None, description="Tidsstämpel (om satt)")
    stores: list[StoreNear] = Field(..., description="Butiker, sorterade på avstånd")


# ---- Erbjudanden ----
class Offer(BaseModel):
    chain: str = Field(..., description="Kedja")
    store_id: str = Field(..., description="Butiks-id")
    offer_id: str = Field(..., description="Erbjudande-id (per butik)")
    name: str | None = Field(None, description="Produktnamn")
    brand: str | None = Field(None, description="Varumärke")
    package: str | None = Field(None, description="Förpackning (rå)")
    package_size: str | None = Field(None, description="Normaliserad förpacknings-storlek")
    package_value: float | None = Field(None, description="Förpackningens mängd")
    package_unit: str | None = Field(None, description="Förpackningens enhet")
    package_approx: bool | None = Field(None, description="Cirka-vikt (ca:)")
    price: float | None = Field(None, description="Pris i kr")
    price_text: str | None = Field(None, description="Visningssträng ('2 för 39 kr')")
    comparison_price: str | None = Field(None, description="Jämförpris (rå sträng)")
    comparison_value: float | None = Field(None, description="Jämförpris (numeriskt)")
    comparison_unit: str | None = Field(None, description="Jämförenhet (kg/l/st)")
    category: str | None = Field(None, description="Kanonisk kategori (berikad)")
    category_raw: str | None = Field(None, description="Kedjans råkategori")
    category_id: int | None = Field(None, description="Kedjans kategori-id")
    deal_type: str | None = Field(None, description="flat | multibuy | by_weight")
    multibuy_qty: int | None = Field(None, description="Antal vid multibuy")
    mechanic_type: str | None = Field(None, description="Kedjans råa mekanik-typ (opålitlig)")
    eans: list[str] = Field(..., description="EAN-array (kan vara tom)")
    image: str | None = Field(None, description="Bild-URL")
    valid_to: str | None = Field(None, description="Giltig t.o.m. (ISO)")
    member_price: int | None = Field(None, description="Medlemspris-flagga (Coop/Axfood)")
    savings: float | None = Field(None, description="Besparing i kr")
    origin: list[str] | None = Field(None, description="Ursprungsländer eller null")
    fetched_at: str | None = Field(None, description="Hämtad (ISO)")


class StoreOffersResponse(BaseModel):
    count: int = Field(..., description="Antal erbjudanden")
    cached: bool = Field(..., description="Servades ur cache (annars live-hämtat)")
    offers: list[Offer] = Field(..., description="Erbjudanden")


# ---- Jämförelse ----
class CompareOffer(BaseModel):
    chain: str = Field(..., description="Kedja")
    store_id: str = Field(..., description="Butiks-id")
    store_name: str | None = Field(None, description="Butiksnamn")
    distance_km: float | None = Field(None, description="Avstånd (km), om områdessök")
    price: float | None = Field(None, description="Råpris i kr")
    price_text: str | None = Field(None, description="Visningssträng")
    comparison_value: float | None = Field(None, description="Jämförpris (numeriskt)")
    comparison_unit: str | None = Field(None, description="Jämförenhet")
    member_price: int | None = Field(None, description="Medlemspris-flagga")
    mechanic_type: str | None = Field(None, description="Rå mekanik-typ")
    deal_type: str | None = Field(None, description="Normaliserad deal-typ")
    multibuy_qty: int | None = Field(None, description="Antal vid multibuy")
    valid_to: str | None = Field(None, description="Giltig t.o.m.")


class CompareGroup(BaseModel):
    ean: str | None = Field(None, description="EAN (null vid manuell grupp)")
    match_group: int | None = Field(None, description="Manuell paringsgrupp-id, annars null")
    manual: bool = Field(..., description="Manuellt parad grupp")
    name: str | None = Field(None, description="Produktnamn")
    brand: str | None = Field(None, description="Varumärke")
    image: str | None = Field(None, description="Bild-URL")
    category: str | None = Field(None, description="Kanonisk kategori-nyckel")
    compare_by: str = Field(..., description="unit_price | price")
    unit: str = Field(..., description="Jämförenhet (kr/kg|l|st) eller 'kr'")
    min: float = Field(..., description="Lägsta värde")
    max: float = Field(..., description="Högsta värde")
    spread: float = Field(..., description="Prisskillnad (max-min)")
    chains: int = Field(..., description="Antal olika kedjor i gruppen")
    stores: int = Field(..., description="Antal butiker i gruppen")
    offers: list[CompareOffer] = Field(..., description="Per butik, billigast först")
    variant_count: int = Field(..., description="Antal hopslagna varianter (samma kampanj)")
    variants: list[str] = Field(..., description="Variantnamn")
    eans: list[str] = Field(..., description="EAN:er i gruppen")


class CompareResponse(BaseModel):
    count: int = Field(..., description="Antal produktgrupper")
    stores_compared: int = Field(..., description="Antal jämförda butiker")
    radius_km: float | None = Field(None, description="Radie (km), bara för områdessök")
    products: list[CompareGroup] = Field(..., description="Produktgrupper")


# ---- Metadata ----
class Chain(BaseModel):
    chain: str = Field(..., description="Kedjenyckel")
    label: str = Field(..., description="Visningsnamn")
    color: str = Field(..., description="Kedjefärg (hex)")
    auth: str = Field(..., description="Autentiseringsmetod mot kedjans API")
    offers_supported: bool = Field(..., description="Stöder erbjudande-hämtning")
    store_count: int = Field(..., description="Antal butiker i cachen")
    sync_status: str | None = Field(None, description="Senaste synkstatus")
    last_sync: str | None = Field(None, description="Senaste synk (ISO)")
    error: str | None = Field(None, description="Senaste synkfel, annars null")


class ChainsResponse(BaseModel):
    chains: list[Chain] = Field(..., description="Kedjor med metadata")


class Category(BaseModel):
    key: str = Field(..., description="Kanonisk kategori-nyckel")
    label: str = Field(..., description="Visningsnamn")


class CategoriesResponse(BaseModel):
    categories: list[Category] = Field(..., description="Kanonisk kategori-vokabulär")


# ---- Produktinfo per EAN ----
class ProductInfoData(BaseModel):
    description: str | None = Field(None, description="Beskrivning")
    ingredients: str | None = Field(None, description="Ingredienslista")
    nutrition: list[dict] | None = Field(None, description="Näringsvärden (label/value/unit)")
    nutrition_basis: dict | None = Field(None, description="Näringsbas (per 100 g/ml)")
    allergens: list[str] | None = Field(None, description="Allergener (ur VERSALA ord)")
    origin: str | None = Field(None, description="Ursprung")
    province: str | None = Field(None, description="Provins/region")
    storage: str | None = Field(None, description="Förvaring")
    labels: list[str] | None = Field(None, description="Märkningar")
    sources: list[str] | None = Field(None, description="Bidragande källor")
    category_raw: str | None = Field(None, description="Råkategori (intern berikning)")
    category_source: str | None = Field(None, description="Kategori-källa (intern berikning)")


class ProductInfoResponse(BaseModel):
    ean: str = Field(..., description="Normaliserad EAN/GTIN")
    found: bool = Field(..., description="Om produktinfo hittades")
    info: ProductInfoData | None = Field(None, description="Produktinfo, eller null")
