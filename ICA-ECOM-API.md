# ICA e-handels-API (`handlaprivatkund.ica.se`)

Referens över ICA:s nya e-handels-API, kartlagt 2026-07-01 (Playwright-nätverksfångst + JS-bundle-analys +
server-side-verifiering). Bakgrund: ICA tog bort pris ur den gamla `globalsearch`/quicksearch-gatewayen
(~2026-06-16, se `CLAUDE.md` "Kända datakälle-fakta"), så per-butik-pris (Steg 6) för ICA måste hämtas
härifrån i stället. Denna fil är sanningskällan för endpoint-detaljerna; CLAUDE.md har den korta versionen.

## TL;DR

- Bas: `https://handlaprivatkund.ica.se/stores/{accountId}/api/<service>/...` (butiken i URL-pathen).
- `{accountId}` = ICA-kontonumret vi redan har i butiksdatan (fås även ur `handla.ica.se/api/store/v1?zip=NNNNN`).
- **GET fungerar server-side med ren `httpx`** (Firefox-UA + header `ecom-request-source: web`, INGA cookies)
  -> 200 JSON. Ingen browser / xvfb / WAF-token behövs. (Browser-NAVIGERING till HTML-sidan utmanas av
  AWS-WAF, men de lästa API-GET:arna gör det inte.)
- Skrivande **PUT/POST kräver `X-CSRF-TOKEN`** (`Csrf-Token`-header). Läsande GET gör det inte.
- **EAN saknas i svaren** - de bär `productId` (UUID) + `retailerProductId` (ICA-artikelnr). Brygga:
  `retailerProductId` **==** gamla quicksearchens `consumerItemId`, och quicksearch (ej WAF-blockad) ger
  `consumerItemId -> gtin`. Så vår EAN-koppling går via quicksearch/`ean_cache`.

## Auth / headers

```
User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0
Accept: application/json; charset=utf-8
ecom-request-source: web
# PUT/POST dessutom:
X-CSRF-TOKEN: <uuid>        # fångas ur SPA:ns egna request-headers vid behov
```

Klienten prefixar relativa URL:er med `/api/<service>`; på servern går de mot `/core-api`. Det finns även en
GraphQL-endpoint (`{base}/graphql`, via urql) för adverts/redaktionella moduler.

Fullständig service-lista (ur bundeln `index-*.js`): `webproductpagews, search, product-listing-pages,
promotions, promotionspresentation, recipes, meals, adverts, contentfulhelper, visualconfiguration, cart,
order, customer, chat, productquerylayer, ecomdeliverydestinations, ecomslots, walletservice, paymentsws,
receipts, refunds`.

## Endpoints

Bas = `https://handlaprivatkund.ica.se/stores/{accountId}/api`. "V" = verifierad 200 server-side;
"B" = ur JS-bundle (ej separat verifierad).

| Path (efter `/api/`) | Metod | CSRF | Syfte | Källa |
|---|---|---|---|---|
| `webproductpagews/v1/categories?decoration=false&categoryDepth=N` | GET | Nej | **Fullt kategoriträd** för butiken (rekursiva `childCategories`, `categoryId`, `retailerCategoryId`, `productCount`). ~1836 löv på djup 3. | V (37-363KB) |
| `webproductpagews/v6/product-pages?categoryId=X&maxPageSize=N&maxProductsToDecorate=N&pageToken=&tag=web&tag=category-item&sortOptionId=&filters=` | GET | Nej | **Produktlista per kategori** = browse hela sortimentet. Pris, jämförpris, `retailerProductId`, ursprung, bild, **erbjudanden**. Paginering via token i svarets `metadata`/`additionalPageInfo` (EJ offset). `maxProductsToDecorate=maxPageSize` decorerar alla med pris. | V |
| `webproductpagews/v6/product-pages?filters=brands%3DX&...` | GET | Nej | Samma, filtrerat på brand. | V |
| `webproductpagews/v6/product-pages?decoratedOnly=true&limit=27&tag=web&tag=lohp` | GET | Nej | Startsidans produktrutnät. | V |
| `webproductpagews/v6/products` | PUT | **Ja** | Batchpriser: body = JSON-array av `productId` (UUID) -> `{products:[{price, unitPrice, retailerProductId, ...}]}`. Behövs troligen inte (product-pages GET decorerar priser direkt). | V |
| `webproductpagews/v5/products/bop?retailerProductId=X` | GET | Nej | **Produktdetalj**: brand, `ingredients`, `nutritionalData` (HTML-tabell), storage, recycling, contactInfo, categoryPath, bilder, similar/related. Möjlig ersättare för den WAF-känsliga `handla.ica.se/produkt/`-SSR-scrapen i `details.py`. | V (12KB) |
| `webproductpagews/v5/products/alternatives?retailerProductId=X` | GET | Nej | Alternativa produkter. | B |
| `webproductpagews/v5/products/decorate-for-fulfillment` | PUT | Ja? | Lagerstatus/fulfillment. | B |
| `webproductpagews/v5/products/recommendations/promotions` | GET | Nej | Rekommenderade kampanjer. | B |
| `webproductpagews/v6/product-pages/search?searchTerm=X&pageToken=&tag=` | GET | Nej | Sök (mitt testanrop 404:ade - behöver troligen annan service-path/regionId, justera). | B |
| `webproductpagews/v6/product-pages/favorites` | GET | session | Favoritlista. | B |
| `webproductpagews/v5/product-pages/regulars` | GET | session | "Mina varor". | B |
| `productquerylayer/v1/pages/promotions?...` | GET | Nej | **Erbjudande-/kampanjsida** (pageToken-paginerad) - strukturerade erbjudanden. | B |
| `search/v1/suggestions/primary?searchTerm=&limit=&regionId=` | GET | Nej | Autocomplete-förslag (bara namn-strängar, inga priser). | V |
| `search/v1/suggestions/follow-on?searchTerm=&limit=&regionId=` | GET | Nej | Följdförslag. | B |
| `search/v1/redirects/active?regionId=` | GET | Nej | Sök-redirects. | V |
| `recipes/v1/recipes/search`, `/v1/recipes/{id}`, `/v1/recipes/{id}/ingredients`, `/v1/recipes/collections` | GET | Nej | Recept. | B |
| `meals/v1/meals`, `/v1/meals/{id}`, `/v1/meals/plan` | GET/POST | delvis | Måltidsplanering. | B |
| `adverts/...` + GraphQL (`AdvertsAndModulesFor{Category,Home,Search,Promotions}Page`) | GET/GraphQL | Nej | Redaktionella moduler/banners. | B |
| `chat/v1/content`, `chat/v1/chat/{id}/messages` | GET/POST | Ja | Recept-AI-chat. | B |

Mindre relevanta: `cart`, `order`, `checkout*`, `customer*`, `ecomdeliverydestinations`/`ecomslots`
(leveransslots), `walletservice`, `paymentsws`, `receipts`, `refunds`.

Butikslista (annan host, ingen WAF): `GET https://handla.ica.se/api/store/v1?zip=NNNNN` (eller
`?groupby=citygroup&customerType=B2C`) -> `accountId`, `retailerSiteId`, `regionId` (via slug), `slug`.

## Produkt-fält (product-pages `decoratedProducts[]` / bop `product`)

```jsonc
{
  "productId": "b1642032-...",          // ecom-UUID (input till PUT products)
  "retailerProductId": "2129142",       // == quicksearch consumerItemId -> gtin-brygga
  "name": "Proteinshake ... Arla®",
  "brand": "Arla",
  "packSizeDescription": "0.5L",
  "price": {"amount": "22.61", "currency": "SEK"},
  "unitPrice": {"price": {"amount": "45.22"}, "unitName": "PER_LITRE"},  // jämförpris
  "promotions": [{"description": "20 kr/st", "type": "OFFER", "retailerPromotionId": "..."}],
  "promoPrice": {"amount": "20.00", "currency": "SEK"},                 // reapris
  "promoUnitPrice": {"price": {"amount": "40.00"}, "unitName": "PER_LITRE"},
  "available": true,
  "categoryPath": ["Färdigmat & Såser", "Kylda såser", "..."],
  "image": {"src": "https://handlaprivatkund.ica.se/images-v3/.../300x300.jpg", ...}
}
```

## Tänkt crawl-loop (Steg 6 per-butik-pris för ICA)

1. `GET v1/categories` per butik -> kategoriträd -> löv-`categoryId`.
2. Per löv: `GET v6/product-pages?categoryId=X&maxPageSize=100&maxProductsToDecorate=100` -> produkter
   (pris + jämförpris + erbjudanden + `retailerProductId`), paginera via `metadata`-token.
3. Mappa `retailerProductId -> gtin` via quicksearch/`ean_cache` (`consumerItemId == retailerProductId`).
4. Skriv per-butik-pris (+ ev. erbjudanden) till `catalog_store_prices` (upsert, som befintliga crawlen).

Ren async-`httpx` som övriga crawlers; inget browser/WAF-steg. Vid ev. 403 (WAF-drift): backa av/retry.

### GOTCHA: WAF är RATE-BASERAD - challengar under last (verifierat 2026-07-01)

GET fungerar server-side utan token vid LÅG takt, men ICA:s AWS-WAF är rate-baserad: under samtidig
crawl-last börjar den challenga (svarar **`200 text/html`** = CloudFront-challenge-sida i st.f. JSON).
Mätt: sekventiellt ~85% butiker OK, men cap=20 med samtidighet 3-4 -> ~50% butiker fel (`JSONDecodeError`/
"HTTP 200 text/html"). En MINORITET butiker (t.ex. 1003400) challengeas dessutom PERSISTENT (även en headed
browser fick ingen `aws-waf-token` för den). Hantering i `ica_ecom.py`:
- `_get_json` verifierar `content-type: json` (inte bara status) och retar med backoff (transienta löses).
- Låg cross-store-samtidighet (`_STORE_CONC`=2) + pace (`_PAGE_PACE`=0.4, `_STORE_PACE`=0.8) håller under
  tröskeln. Persistent challengeade butiker skippas (`ok_med_fel`) och retas nästa körning - vi har ändå
  quicksearch-närvaro för dem.
- Om högre täckning/takt behövs: hämta `aws-waf-token` via headed browser (xvfb, se prototyp) och skicka
  som cookie i httpx - exempterar från challengen. Ej byggt (låg takt räcker för parallell-fasen).

### GOTCHA: pageToken-paginering fungerar INTE statslöst (verifierat 2026-07-01)

`pageToken` (nästa sida) returnerar **0 produkter på sida 2** med ren httpx utan cookies - SPA:n håller
server-side token-state (cookie/session) som en statslös klient inte replikerar. Gäller både
`product-pages` och promotions-endpointen. -> Med vår statslösa metod får vi bara **första sidan** per vy.
Utvägar för full täckning:
- **Hög `maxPageSize`** (t.ex. 500-1000) på LÖV-kategorier (de flesta av ~1836 löv är små nog att rymmas
  i en sida) -> undvik paginering helt. Verifiera per-kategori `productCount` ur kategoriträdet.
- Alternativt: etablera en session-cookie först (en initial request mot butiken) så pageToken funkar.

## Erbjudanden (offers)

**Behåll `ica_offers.py` (weeklyOffers) - INTE ecom.** ICA:s reklamblad (`/erbjudanden/{slug}-{accountNumber}/`
SSR `window.__INITIAL_DATA__.weeklyOffers`) är oförändrat och friskt (verifierat 2026-07-01: 52/144 offers
på två butiker), med `price_text`/mekanik, `eans[]` INLINE, och `validTo` - allt vår offers-cache behöver.

Ecom-erbjudanden finns (`promotions[]`/`promoPrice` i product-pages + `product-listing-pages/v1/pages/
promotions?regionId={UUID}`, regionId skrapas ur butiks-HTML) men är SÄMRE: ingen giltighetstid (`valid_to`),
EAN ej inline (kräver `retailerProductId->gtin`-brygga via den capade quicksearchen), och pageToken-
begränsningen ovan. Främst intressant som KOMPLEMENT: erbjudande-flaggan (`promotions`/`promoPrice`) fås
gratis i SAMMA product-pages-anrop som hyllpriset, om vi vill baka in den i katalog-crawlen.
