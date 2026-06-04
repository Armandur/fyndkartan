# Matbutiker - Unified Store API (master-dokumentation)

Ett gemensamt API som hittar matbutiker (och deras erbjudandesidor) för
sex svenska kedjor: **ICA, Coop, Willys, Hemköp, Lidl, City Gross**. Detta dokument är
specen för steg 1: *hitta butiker + länka till erbjudandesidor*. Själva
erbjudande-innehållet (priser, varor) är steg 2.

> **Status:** Steg 1 är byggt (se denna spec). Steg 2 (erbjudande-innehåll) är
> påbörjat - aktuellt läge, datakällor per kedja och matchningsplan finns i
> [`ROADMAP.md`](ROADMAP.md). Kodbasöversikt i [`CLAUDE.md`](CLAUDE.md).

Alla scheman nedan är **verifierade mot skarpa API-svar 2026-05-31** - alla
sex kedjor nås via REST/HTTP, ingen headless-rendering krävs.

---

## 1. Arkitektur i ett ögonkast

```
                       ┌─────────────────────────────────────────┐
   Kedjornas källor    │            Adapters (per kedja)          │   Normaliserat
   ───────────────     │   ica.py  coop.py  willys.py  hemkop.py  │   ───────────
   ICA storeslist  ───▶│   lidl.py  -> mappar råsvar -> Unified-  │──▶  SQLite-cache
   Coop proxy API  ───▶│   Store enligt §3                        │     (stores)
   Willys REST     ───▶│                                          │
   Hemköp REST     ───▶└─────────────────────────────────────────┘        │
   Lidl geo_box                                                            ▼
                                                              ┌──────────────────────┐
   Sync-jobb (schemalagt, 1 ggr/dygn)                         │  FastAPI serving-lager │
   - fyra kedjor: hämta hela beståndet, filtrera lokalt       │  GET /v1/stores ...    │
   - Lidl: svep landet i ett rutnät av geo_box-anrop          │  (läser ALLTID cachen) │
                                                              └──────────────────────┘
                                                                         │
                                                                         ▼
                                                                   Appen (steg 3)
```

**Grundprincip:** appen pratar *aldrig* direkt med kedjornas API:er. Ett
schemalagt sync-jobb hämtar och normaliserar; FastAPI serverar ur SQLite.
Det skyddar kedjornas servrar, ger snabba svar, och isolerar appen från att
en kedja byter schema eller roterar nyckel.

### Två insamlingsmönster

| Mönster | Kedjor | Strategi |
|---------|--------|----------|
| **Hämta-allt** | ICA, Coop, Willys, Hemköp | Ett (eller få) anrop ger hela beståndet. Geosökning görs lokalt med Haversine. |
| **Geo-svep** | Lidl | API:t ger *bara* butiker inom en `geo_box`. Sync sveper ett rutnät av rutor över Sverige och deduplicerar på `objectNumber`. |

---

## 2. Unified REST-kontrakt (det appen anropar)

Bas: `/v1`. Alla svar är JSON. Butiksobjektet är `UnifiedStore` (§3).

```
GET /v1/stores
    Alla butiker (normaliserade). Filter:
      ?chain=ica,coop,willys,hemkop,lidl   (komma-separerad, default alla)
      ?city=Sundsvall                       (case-insensitive exakt)
      ?q=hagaplan                           (fritext mot namn + adress)
      ?brand=ica_nara,willys_hemma          (se brand-vokabulär §3.2)
      ?features=parking,e_charging          (AND-filter mot tags, §3.4)
      ?has_offers=true                      (bara butiker med links.offers)

GET /v1/stores/near?lat=62.39&lng=17.31&radius_km=10
    Geosökning (Haversine mot cachade koordinater). Samma filter som ovan.
    Sorterat på avstånd; varje träff får ett extra fält distance_km.

GET /v1/stores/{chain}/{store_id}
    En butik. {chain} = kedjenyckel, {store_id} = native id (string).
    Ex: /v1/stores/coop/598 , /v1/stores/ica/2527

GET /v1/chains
    Metadata om varje integration: senaste sync, antal butiker, om
    erbjudande-länk stöds, källmetod, auth-krav. Se §6.
```

### Standard svarsform

```jsonc
// GET /v1/stores?city=Sundsvall&chain=lidl
{
  "count": 3,
  "generated_at": "2026-05-31T19:30:55Z",   // när cachen senast syncades
  "stores": [ /* UnifiedStore[] */ ]
}
```

Felsvar följer FastAPI-standard (`{"detail": "..."}`) med svenska,
icke-tekniska meddelanden för 4xx.

---

## 3. UnifiedStore - den gemensamma datamodellen

Designprincip från uppgiften: **vissa fält mappas till gemensamma fält,
resten blir taggad metadata.** Konkret:

- **Gemensamma fält** (§3.1): finns meningsfullt hos *alla* kedjor -
  identitet, adress, koordinat, kontakt, länkar.
- **Tags** (§3.4): kedjespecifika egenskaper (parkering, apoteksombud,
  laddstation, click & collect...). Modelleras som en lista av *positiva
  påståenden*. **Avsaknad = okänt, inte "nej".** En kedja som inte
  rapporterar parkering ger ingen parking-tag - det betyder inte att
  butiken saknar parkering.
- **native** (§3.5): kedjans råa sekundär-ID:n och egenheter som adaptern
  behöver för att kunna gå tillbaka till källan (t.ex. Coops
  `ledgerAccountNumber`).

### 3.1 Schema

```jsonc
{
  "chain": "coop",                    // enum: ica|coop|willys|hemkop|lidl
  "store_id": "598",                  // kedjans native id, ALLTID string
  "name": "Coop Krylbo",
  "brand": "coop",                    // normaliserad sub-brand, §3.2

  "address": {
    "street": "Järnvägsgatan 16",
    "postal_code": "77571",
    "city": "Krylbo"
  },
  "location": {                       // null om koordinat saknas
    "lat": 60.1307271,
    "lng": 16.213442
  },
  "contact": {
    "phone": "010-7412170",
    "email": null
  },

  "opening_hours": {
    "today": "9-20",                  // kort sträng för UI, §3.3
    "open_now": null,                 // bool om kedjan rapporterar status, annars null
    "week": [                         // normaliserad vecka, §3.3 (null om okänt)
      { "day": 0, "closed": false, "opens": "08:00", "closes": "20:00" }
      // day: 0=måndag .. 6=söndag. closed=true -> opens/closes null.
      // En veckodag kan saknas (avsaknad = okänt; t.ex. Lidl-helg i fönstret)
    ],
    "exceptions": [                   // daterade avvikelser (helgdagar), null om inga
      { "date": "2026-06-06", "label": "Nationaldagen", "closed": false,
        "opens": "09:00", "closes": "18:00" }
      // date: "YYYY-MM-DD" eller null (ICA anger bara helgnamn, inte datum)
    ],
    "raw": { /* kedjans original-struktur, oförändrad */ }
  },

  "links": {
    "store_page": "https://www.coop.se/butiker-erbjudanden/coop/coop-krylbo/",
    "offers": "https://dr.coop.se/butik/196183",   // null om okänd (Hemköp, Lidl)
    "online_shopping": null
  },

  "tags": [                           // positiva påståenden, §3.4
    { "type": "atg", "label": "ATG-ombud" },
    { "type": "parcel", "label": "DHL paket" },
    { "type": "other", "label": "Coop Snabbkassa" }
  ],

  "native": {                         // §3.5 - kedjespecifikt råmaterial
    "ledgerAccountNumber": "196183",
    "concept": "Coop"
  },

  "source": {
    "method": "api",                  // api | scrape | jsonld | sitemap
    "fetched_at": "2026-05-31T19:30:55Z"
  }
}
```

### 3.2 brand-vokabulär

`brand` är normaliserad sub-kedja (för logotyp/filtrering i appen). Härleds
i adaptern; rå-värdet bevaras i `native`.

| brand | Kedja | Härleds ur |
|-------|-------|-----------|
| `maxi_ica` | ICA | `profile == "Maxi"` |
| `ica_kvantum` | ICA | `profile == "Kvantum"` |
| `ica_supermarket` | ICA | `profile == "Supermarket"` |
| `ica_nara` | ICA | `profile == "Nära"` |
| `coop`, `stora_coop`, `coop_nara` | Coop | `concept`-fältet |
| `willys` | Willys | `willysHemma == false` |
| `willys_hemma` | Willys | `willysHemma == true` |
| `hemkop` | Hemköp | konstant (ev. `hemkop_express` om det dyker upp) |
| `lidl` | Lidl | konstant |

### 3.3 opening_hours

Öppettidsformaten skiljer sig **kraftigt** mellan kedjor (ICA: strukturerade
objekt med `regulars`/`deviations`/`divisions`; Lidl: dag-för-dag med
ISO-tidsstämplar; Willys/Hemköp: array av strängar; Coop: etikettgrupperade
objekt med `HH:MM:SS`). Regeln:

1. `opening_hours.raw` = kedjans originalstruktur, oförändrad. Inget tappas.
2. `opening_hours.today` = dagens öppettid normaliserad till **`HH:MM-HH:MM`**
   (t.ex. Coops `9-20` -> `09:00-20:00`). Tas från kedjans eget fält (Coop
   `openingHoursToday`, Willys `openingStoreMessageValue`, ICA
   `openingHours.today`, Lidl härlett ur dagens `timeRanges`). Stängd dag
   bevaras som texten `Stängt`; oparsbara värden lämnas oförändrade; saknas
   det blir fältet `null`.
3. `open_now` = bool om kedjan rapporterar status (Willys `open`, Lidl
   `status.name`, ICA-status härledd), annars `null`.
4. `opening_hours.week` = **normaliserad vecka** parsad ur `raw` per kedja:
   en lista `{day (0=mån..6=sön), closed, opens, closes}`, tider `HH:MM`.
   ICA/Coop expanderar etikettgrupper (`"Måndag-fredag"` -> dag 0-4), Axfood
   per veckodag-sträng, Lidl härleder veckodag ur datum. En veckodag kan
   **saknas** (avsaknad = okänt, t.ex. när en Lidl-helgdag ligger på dagen i
   det 7-dagarsfönster Lidl exponerar). `null` om kedjan inte ger veckodata.
5. `opening_hours.exceptions` = **daterade avvikelser** (helgdagar):
   `{date, label, closed, opens, closes}`. `date` är `YYYY-MM-DD` eller `null`
   (ICA anger bara helgnamn, inte datum). `null` om inga.

### 3.4 tags - taggad metadata

Lista av `{ "type": <normaliserad>, "label": <rå kedjesträng> }`. `type`
mappar mot en gemensam vokabulär där det går; okänt blir `"other"` (men
`label` behålls alltid så inget tappas).

Föreslagen `type`-vokabulär (utöka i takt med att fler dyker upp):

| type | Exempel på källsträngar |
|------|------------------------|
| `pharmacy` | ICA "Apoteksombud", Lidl-avdelning apotek |
| `atg` | "ATG ombud", "ATG-ombud" |
| `postal` | "Postombud", "Posten Brev & paket", "Frimärksombud" |
| `parcel` | "DHL paket", "Schenker Privpak paket", "Instabox", "Bring" |
| `parking` | Lidl `parking` |
| `parking_disabled` | Lidl `parking-disabled-people` |
| `e_charging` | Lidl `e-charging-station` |
| `click_collect` | Willys `clickAndCollect == true` |
| `franchise` | Willys `franchiseStore == true` |
| `loyalty_app` | Lidl `lidl-plus` |
| `other` | allt ej mappat (label bevaras) |

**Källor per kedja:** ICA `services[]`, Coop `services[]` (detalj-anrop),
Willys boolean-fält, Lidl `marketingData.infoIcons[].odsName`. Hemköp
exponerar inga tjänster -> tom `tags`-lista (= okänt, inte "saknar allt").

### 3.5 native - bevara kedjans sekundära ID:n

Adaptrar måste kunna gå tillbaka till källan. Spara det kedjespecifika som
behövs för det:

| Kedja | native-fält | Varför |
|-------|-------------|--------|
| ICA | `accountNumber`, `profile` | `accountNumber` (1004177) används i butiks-/erbjudande-URL; `storeId` (2527) är ett *annat* ID |
| Coop | `ledgerAccountNumber`, `storeId`(int), `concept` | **detalj-anrop kräver `ledgerAccountNumber`, inte `storeId`** |
| Willys | `willysHemma` | brand-härledning |
| Lidl | `objectNumber`, `offerRegion`, `zone` | dedup-nyckel + framtida erbjudande-koppling |
| Hemköp | - | inget sekundärt ID behövs |

---

## 4. Adapter-spec per kedja (verifierat)

Varje adapter exponerar `fetch_all() -> list[UnifiedStore]`. Nedan: endpoint,
auth, råschema och mappning. **Lägg alla nycklar i `.env`** (se §7).

### 4.1 ICA  `chain="ica"`

```
GET https://apim-pub.gw.ica.se/sverige/digx/mdsastoresearch/v1/storeslist
    ?url=%2F&sort=FromStore&skip=0&take=20      (paginera skip += take)
Header: Authorization: Bearer $ICA_TOKEN
```

**Verifierat:** tokenet i källfilerna (`_0XBPWQQ_...`) gav HTTP 200 mot
denna endpoint. Svaret är en JSON-array; varje butik innehåller redan ALLT -
inget detalj-anrop behövs.

Råschema (verifierade fältnamn):

```jsonc
{
  "storeName": "ICA Nära A-Livs",
  "profile": "Nära",                       // -> brand
  "phoneNumber": "044 50392",
  "emailAddress": "kurt.andersson@nara.ica.se",
  "services": ["Apoteksombud","ATG ombud","Postombud",...],   // -> tags
  "openingHours": { "regulars":[...], "deviations":[...],
                    "divisions":[...], "today":{...}, "tomorrow":{...} },
  "address": {
    "street": "Gamla Vägen 91", "city": "Fjälkinge", "postalCode": "29167",
    "coordinates": { "coordinateX": "56.04189", "coordinateY": "14.27984" }
  },
  "highlightUrls": { "offers": { "url": "https://www.ica.se/erbjudanden/ica-nara-a-livs-1004177/" },
                     "online": null },
  "storeId": "2527",
  "accountNumber": "1004177",
  "onlineUrl": null,
  "bhsUrl": "https://www.ica.se/butiker/nara/kristianstad/ica-nara-a-livs-1004177/"
}
```

Mappning - **observera koordinat-fällan**:

| Unified | ICA-källa |
|---------|-----------|
| `store_id` | `storeId` (= "2527") |
| `location.lat` | `address.coordinates.coordinateX`  ⚠ **X är latitud** |
| `location.lng` | `address.coordinates.coordinateY`  ⚠ **Y är longitud** |
| `links.offers` | `highlightUrls.offers.url`  (direkt, ingen härledning!) |
| `links.store_page` | `bhsUrl` |
| `links.online_shopping` | `onlineUrl` (ofta null; fallback `https://handlaprivatkund.ica.se/stores/{storeId}`) |
| `tags` | `services[]` |
| `native.accountNumber` | `accountNumber` |

Doc:en i `ica-butiker.md` gissade på derivering av erbjudande-URL via slug -
**det behövs inte**, `highlightUrls.offers.url` ges direkt. Nyckelfri
fallback (sitemap + JSON-LD) finns kvar om man vill köra utan token.

### 4.2 Coop  `chain="coop"`

```
Lista:  GET https://proxy.api.coop.se/external/store/stores/map
            ?api-version=v2&conceptIds=12,6,95&invertFilter=true
Detalj: GET https://proxy.api.coop.se/external/store/stores/{ledgerAccountNumber}
            ?api-version=v5&includeFlyers=true
Header: Ocp-Apim-Subscription-Key: $COOP_KEY     (publik frontend-nyckel)
```

**Verifierat:** lista = 200 / **722 butiker**; detalj = 200.
**Detalj-anropet kräver `ledgerAccountNumber` (6 siffror), inte `storeId`** -
annars 400.

Listan ger grunddata; **erbjudanden + tjänster + flyers finns bara i
detaljen**. Strategi: lista för alla, sedan detalj per butik (snällt, cachat).

| Unified | Coop-källa |
|---------|-----------|
| `store_id` | `storeId` (int -> string) |
| `location.lat/lng` | `latitude` / `longitude` |
| `links.store_page` | `https://www.coop.se` + `url` |
| `links.offers` | `weeklyOffersLink` (= `https://dr.coop.se/butik/{ledger}`) |
| `tags` | `services[]` (detalj) |
| `native.ledgerAccountNumber` | `ledgerAccountNumber` |
| `native.concept` | `concept` -> brand |

Bonus: `flyers[]` i detaljen har `pdfUrl` + `imageUrl` + `startDate`/`stopDate`
- guld för steg 2. Spara i `native.flyers` om plats finns.

### 4.3 Willys  `chain="willys"`

```
GET https://www.willys.se/axfood/rest/store?online=false      (ingen auth)
```

**Verifierat:** 200, **255 poster (254 med namn)** - filtrera bort den med
tomt `name`. Ett anrop ger allt inkl. öppettider.

| Unified | Willys-källa |
|---------|-----------|
| `store_id` | `storeId` (redan string, t.ex. "2149") |
| `name` | `name` (släng om tom) |
| `brand` | `willysHemma ? willys_hemma : willys` |
| `address.*` | `address.line1` / `address.postalCode` / `address.town` |
| `location.lat/lng` | `geoPoint.{latitude,longitude}` (fallback `address.*`) |
| `contact.phone` | `address.phone` (fallback `customerServicePhone`) |
| `contact.email` | `customerServiceEmail` |
| `opening_hours.today` | `openingStoreMessageValue` |
| `opening_hours.raw` | `openingHours[]` + `specialOpeningHours[]` |
| `open_now` | `open` |
| `links.store_page` | `https://www.willys.se/butik/{storeId}` |
| `links.offers` | `flyerURL` (= `https://viewer.ipaper.io/willys/{storeId}`) |
| `tags` | `clickAndCollect`/`franchiseStore` -> click_collect/franchise |

~20 B2B/leverans-kostnadsfält ignoreras.

### 4.4 Hemköp  `chain="hemkop"`

```
GET https://www.hemkop.se/axfood/rest/store?online=false      (ingen auth)
```

**Verifierat:** 200, **206 butiker** (alla med namn, koordinat och
erbjudande-länk). Hemköp använder **exakt samma Axfood-endpoint som Willys**
(SAP Commerce) - ett anrop ger hela beståndet inkl. öppettider. Ingen
headless-rendering behövs.

> Tidigare antagande (i `hemkop-butiker.md`) att listan bara fanns i React-state
> (`storeMarkers`) och krävde headless-skrap är **inaktuellt** - `/axfood/rest/store`
> ger samma data direkt. Den gamla SAP OCC-endpointen (`/axfoodcommercewebservices`)
> returnerade bara 1 butik, men `/axfood/rest/store` (samma som Willys) ger alla.

Råschemat är identiskt med Willys (samma fältnamn):

| Unified | Hemköp-källa |
|---------|-----------|
| `store_id` | `storeId` (string, t.ex. "4798") |
| `name` | `name` (släng om tom) |
| `brand` | `hemkop` (konstant) |
| `address.*` | `address.line1` / `address.postalCode` / `address.town` |
| `location.lat/lng` | `geoPoint.{latitude,longitude}` (fallback `address.*`) |
| `contact.phone` | `address.phone` (fallback `customerServicePhone`) |
| `contact.email` | `address.email` (fallback `customerServiceEmail`) |
| `opening_hours.today` | `openingStoreMessageValue` |
| `opening_hours.raw` | `openingHours[]` + `specialOpeningHours[]` |
| `open_now` | `open` |
| `links.store_page` | `https://www.hemkop.se/butik/{storeId}` |
| `links.offers` | `https://www.hemkop.se/erbjudanden/{storeId}` (verifierat) |
| `tags` | `clickAndCollect`/`franchiseStore` -> click_collect/franchise |
| `native.flyerPdf` | `flyerURL` (direkt PDF, t.ex. `hemkop.eo.se/hkp/4798.pdf`) |

**Notis om erbjudande-länk:** `flyerURL` är en rå PDF; vi använder istället
den människovänliga `/erbjudanden/{storeId}`-sidan som `links.offers` och
sparar PDF:en i `native.flyerPdf`.

### 4.5 Lidl  `chain="lidl"`  *(geo-svep)*

```
GET https://live.api.schwarz/odj/stores-api/v2/myapi/stores-frontend/stores
    ?country_code=SE&limit=25&offset=0&geo_box=LAT_MIN,LNG_MIN:LAT_MAX,LNG_MAX
Header: x-apikey: $LIDL_KEY          ⚠ "x-apikey", INTE "x-api-key"
```

**Verifierat:** `x-apikey` (nyckeln i källfilerna) gav 200. Svaret är
`{ "meta": {...,"total":N}, "items": [...] }`. API:t ger **bara butiker inom
geo_box** - därför geo-svep i sync (§4.6).

Råschema (verifierat):

```jsonc
{
  "objectNumber": "SE00365",
  "storeName": "Sundsvall Bydalen",
  "distance": 2.5,
  "address": { "streetName":"Norra Vägen","streetNumber":"8",
               "city":"Sundsvall","zip":"856 50",
               "latitude":62.41173,"longitude":17.33483 },
  "status": { "name": "open" },
  "openingHours": { "timeZone":"Europe/Stockholm",
                    "items":[{ "date":"2026-05-31",
                               "timeRanges":[{"from":"...T09:00:00","to":"...T20:00:00"}],
                               "reason":"SUNDAY_REPEAT" }] },
  "marketingData": { "offerRegion":5, "offerRegionName":"Rosersberg 2",
                     "zone":"SE1",
                     "infoIcons":[{"odsName":"parking"},{"odsName":"e-charging-station"},
                                  {"odsName":"lidl-plus"}] },
  "seo": [{ "locale":"sv-SE", "text":"..." }]
}
```

| Unified | Lidl-källa |
|---------|-----------|
| `store_id` | `objectNumber` ("SE00365") |
| `name` | `storeName` |
| `address.street` | `streetName` + " " + `streetNumber` |
| `address.postal_code` | `zip` |
| `location.lat/lng` | `address.{latitude,longitude}` |
| `open_now` | `status.name == "open"` |
| `opening_hours.raw` | `openingHours.items[]` |
| `tags` | `marketingData.infoIcons[].odsName` |
| `links.offers` | **null per butik - erbjudanden är regionala (känd lucka, §5)** |
| `native.objectNumber/offerRegion/zone` | `marketingData.*` |

Ingen direkt `store_page`-URL ges; statisk butikssida finns på
`/s/sv-SE/butiker/{stad}/{adress}/` men är inte deterministiskt byggbar ur
API-svaret. Sätt `store_page` = null eller best-effort.

### 4.6 Lidl geo-svep (sync-detalj)

Sverige täcks av ett rutnät av `geo_box`-rutor (t.ex. 0.3° lat × 0.5° lng).
För varje ruta: paginera `offset += limit` tills `offset >= meta.total`.
Deduplicera på `objectNumber`. Glesa norra rutor ger ofta `total: 0` - hoppa
snabbt vidare. Var snäll: seriellt eller låg parallellism, dygnsvis.

---

## 5. Erbjudande-länkar: täckning och kända luckor

Steg 1 ska leverera *länken till erbjudandesidan*, inte innehållet. Status:

| Kedja | `links.offers` | Källa |
|-------|----------------|-------|
| ICA | ✅ | `highlightUrls.offers.url` (direkt) |
| Coop | ✅ | `weeklyOffersLink` + `flyers[]` (PDF/bild) |
| Willys | ✅ | `flyerURL` (ipaper-visare) |
| Hemköp | ✅ | `https://www.hemkop.se/erbjudanden/{storeId}` (verifierat). PDF även i `flyerURL`. |
| Lidl | ❌ **lucka** | Inget per butik; erbjudanden är *regionala* (`offerRegion`/`zone`). Steg 2: koppla region -> reklamblad/Lidl Plus. |

**Gör inte:** hitta inte på en URL-mall för Lidl. Lämna `null` och
markera luckan. `has_offers`-filtret i API:t låter appen visa "erbjudanden
finns" bara där vi faktiskt har en länk.

---

## 6. /v1/chains - integrationsstatus

```jsonc
{
  "chains": [
    { "chain":"ica",    "store_count":1288, "method":"api",    "auth":"bearer",
      "offers_supported":true,  "last_sync":"2026-05-31T03:00:00Z" },
    { "chain":"coop",   "store_count":722,  "method":"api",    "auth":"subscription_key",
      "offers_supported":true,  "last_sync":"..." },
    { "chain":"willys", "store_count":254,  "method":"api",    "auth":"none",
      "offers_supported":true,  "last_sync":"..." },
    { "chain":"hemkop", "store_count":206,  "method":"api",    "auth":"none",
      "offers_supported":true,  "last_sync":"..." },
    { "chain":"lidl",   "store_count":212,  "method":"api",    "auth":"apikey",
      "offers_supported":false, "last_sync":"..." }
  ]
}
```

Appen använder `offers_supported`/`last_sync` för att visa rätt UI och varna
för inaktuell data.

---

## 7. Nycklar och konfiguration - självförnyande

Ingen kedjas nyckel behöver längre läggas in manuellt. Alla tre publika
frontend-nycklar/tokens **skrapas automatiskt från kedjornas egna sidor** vid
behov. `.env` är helt valfri - bara för att tvinga en egen nyckel.

| Kedja | Nyckel | Källa som skrapas | Strategi |
|-------|--------|-------------------|----------|
| ICA | `publicAccessToken` (Bearer) | `ica.se/butiker/` (inbäddad JSON) | **Kortlivad ~1h** - skrapas proaktivt, cachas till `tokenExpires` minus 5 min, förnyas vid 401 |
| Coop | `storeApiSubscriptionKey` | `coop.se/butiker-erbjudanden/` (`serviceAccess`-JSON) | Statisk - env-nyckel primär, **skrapas om vid 401** |
| Lidl | `x-apikey` | `storesearch-frontend/.../base.js` | Statisk - env-nyckel primär, **skrapas om vid 401** |

Implementation: `app/adapters/ica_token.py` (ICA, proaktiv med utgångscache)
och `app/adapters/keys.py` (Coop + Lidl, scrape-on-401). Willys + Hemköp
kräver ingen nyckel.

```bash
# .env (allt valfritt - lämna tomt för auto-skrapning)
ICA_TOKEN=            # tomt = skrapas från ica.se/butiker/
COOP_KEY=             # valfri override; annars env-nyckel + scrape-on-401
LIDL_KEY=             # valfri override; annars env-nyckel + scrape-on-401
```

**Noteringar:**
- Alla tre är *publika* nycklar som kedjorna serverar till varje besökare -
  vi använder dem på exakt samma sätt som deras egen frontend.
- Vid 401 loggas förnyelsen (svälj inte felet). ICA förnyar dessutom mitt i en
  pågående synk om tokenet hinner gå ut.
- Var snäll mot servrarna: sync dygnsvis, cacha, låg anropsfrekvens. Stäm av
  varje kedjas användarvillkor inför skarp/kommersiell drift.

---

## 8. Implementationsskiss (FastAPI + SQLite)

Följer standardstacken. Föreslagen layout:

```
app/
  main.py            # app, lifespan, router-registrering
  config.py          # env (nycklar), kedjekonstanter, geo-svep-rutnät
  database.py        # SQLite-schema + init_db() (stores-tabell, raw JSON-kolumn)
  schemas.py         # Pydantic: UnifiedStore m.fl.
  deps.py            # gemensamma dependencies
  routes/
    stores.py        # GET /v1/stores, /near, /{chain}/{id}
    chains.py        # GET /v1/chains
  services/
    sync.py          # orkestrerar adaptrar -> normalisering -> SQLite
    geo.py           # Haversine, geo_box-rutnät för Lidl
  adapters/
    ica.py coop.py willys.py hemkop.py lidl.py   # fetch_all() -> UnifiedStore[]
```

- **DB:** en `stores`-tabell. Gemensamma fält som kolumner (för filter/index
  på `chain`, `city`, `lat`, `lng`), plus `tags` och `raw` som JSON-kolumner.
  Migrering via `ALTER TABLE`-guards i `init_db()` - ingen Alembic.
- **Sync:** ett anropbart `run_sync()` (kör alla adaptrar, ersätter cachen
  transaktionellt per kedja). Schemaläggs externt (cron/systemd-timer) eller
  via en intern scheduler. Alla fem adaptrar är rena HTTP-anrop.
- **Geosök:** Haversine i Python mot cachade koordinater (alla utom Lidl har
  koordinater direkt; Lidl får dem via svepet). Bounding-box-förfilter i SQL
  innan exakt Haversine om beståndet växer.

---

## 9. Verifieringsstatus (2026-05-31)

| Kedja | Endpoint testad | Resultat |
|-------|-----------------|----------|
| ICA | `storeslist` m. Bearer | ✅ 200, fullt schema, offers-URL direkt |
| Coop | lista + detalj | ✅ 200 / 722 butiker, offers + flyers bekräftade |
| Willys | `/axfood/rest/store` | ✅ 200 / 254 butiker |
| Hemköp | `/axfood/rest/store` | ✅ 200 / 206 butiker (samma Axfood-endpoint som Willys) |
| Lidl | geo_box m. `x-apikey` | ✅ 200, schema kartlagt, taggar via infoIcons |

Hela beståndet i körande cache: **2682 butiker** (ICA 1288, Coop 722, Willys
254, Lidl 212, Hemköp 206).

Källfilernas största gissningsfel som rättats här: ICA:s erbjudande-URL ges
direkt (ingen slug-derivering), ICA `coordinateX` = latitud, Lidl-headern
heter `x-apikey` (inte `x-api-key`), och Hemköp kräver **ingen** headless-skrap -
`/axfood/rest/store` ger hela listan precis som för Willys.
