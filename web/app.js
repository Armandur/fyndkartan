"use strict";

const BRAND_LABELS = {
  maxi_ica: "Maxi ICA", ica_kvantum: "ICA Kvantum", ica_supermarket: "ICA Supermarket",
  ica_nara: "ICA Nära", ica: "ICA", coop: "Coop", stora_coop: "Stora Coop",
  coop_nara: "Coop Nära", willys: "Willys", willys_hemma: "Willys Hemma",
  hemkop: "Hemköp", lidl: "Lidl",
};

const state = {
  chains: {},          // chain -> meta {label,color,offers_supported,...}
  enabled: new Set(),  // aktiverade kedjor
  stores: [],          // alla butiker
  query: "",
  onlyOffers: false,
  onlyFavorites: false,
  favorites: new Set(),
  user: null,
  productFilter: null,  // {ean, name, stores: Set("chain:store_id"), count} - kartfilter på en vara
};

const COMPARE_CHAINS = ["ica", "coop", "willys", "hemkop", "citygross"];

function favKey(s) { return `${s.chain}:${s.store_id}`; }
function isFav(s) { return state.favorites.has(favKey(s)); }

async function toggleFav(s) {
  if (!state.user) { openAuth("login"); return; }  // favoriter kräver inloggning
  const k = favKey(s);
  const had = state.favorites.has(k);
  if (had) await fetch(`/v1/favorites/${s.chain}/${s.store_id}`, { method: "DELETE" });
  else await fetch("/v1/favorites", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ chain: s.chain, store_id: s.store_id }) });
  if (had) state.favorites.delete(k); else state.favorites.add(k);
  if (state.onlyFavorites) render();
  else renderList();
}

const map = L.map("map", { zoomControl: true }).setView([62.0, 16.5], 5);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap",
}).addTo(map);

const cluster = L.markerClusterGroup({ maxClusterRadius: 50, chunkedLoading: true });
map.addLayer(cluster);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Avrunda pris till max 2 decimaler (strippar avrundnings-artefakter som 8.333333... -> 8.33).
function kr(v) {
  if (v == null || v === "") return v ?? "";
  const n = Number(v);
  return Number.isFinite(n) ? String(Math.round(n * 100) / 100) : v;
}

function chainColor(chain) {
  return (state.chains[chain] && state.chains[chain].color) || "#666";
}

function markerIcon(chain) {
  return L.divIcon({
    className: "",
    html: `<div class="marker-pin" style="background:${chainColor(chain)}"></div>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  });
}

const DOW = ["Mån", "Tis", "Ons", "Tors", "Fre", "Lör", "Sön"];

function fmtDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${+m[3]}/${+m[2]}` : iso;  // 2026-06-06 -> 6/6 (utan inledande nollor)
}

function weekHtml(oh) {
  const week = oh.week || [];
  if (!week.length) return "";
  const todayIdx = (new Date().getDay() + 6) % 7; // JS 0=sön -> 0=mån
  const rows = week.map((d) => {
    const hrs = d.closed ? "Stängt" : `${esc(d.opens || "")}-${esc(d.closes || "")}`;
    return `<tr class="${d.day === todayIdx ? "wk-today" : ""}"><td>${DOW[d.day] || d.day}</td><td></td><td class="wk-hrs">${hrs}</td></tr>`;
  }).join("");
  const exc = (oh.exceptions || []).slice(0, 8).map((e) => {
    const hrs = e.closed ? "Stängt" : `${esc(e.opens || "")}-${esc(e.closes || "")}`;
    return `<tr class="wk-exc"><td>${e.date ? esc(fmtDate(e.date)) : ""}</td><td>${e.label ? esc(e.label) : ""}</td><td class="wk-hrs">${hrs}</td></tr>`;
  }).join("");
  const excBlock = exc ? `<tr class="wk-exc-h"><td colspan="3">Avvikande dagar</td></tr>${exc}` : "";
  return `<details class="pop-week"><summary>Veckans öppettider</summary>
    <table class="wk-table"><tbody>${rows}${excBlock}</tbody></table>
  </details>`;
}

function popupHtml(s) {
  const color = chainColor(s.chain);
  const brand = BRAND_LABELS[s.brand] || (state.chains[s.chain] || {}).label || s.chain;
  const a = s.address || {};
  const addr = [a.street, [a.postal_code, a.city].filter(Boolean).join(" ")]
    .filter(Boolean).join(", ");
  const oh = s.opening_hours || {};
  const tags = (s.tags || []).slice(0, 6)
    .map((t) => `<span class="tag">${esc(t.label)}</span>`).join("");
  const links = s.links || {};
  let linkHtml = "";
  if (links.offers) linkHtml += `<a class="btn-offers" target="_blank" href="${esc(links.offers)}">Erbjudanden</a>`;
  if (links.store_page) linkHtml += `<a class="btn-store" target="_blank" href="${esc(links.store_page)}">Butikssida</a>`;
  // Inläsbara erbjudanden stöds för ICA + Axfood (Willys/Hemköp) + Coop.
  const offersBtn = ["ica", "willys", "hemkop", "coop", "citygross"].includes(s.chain)
    ? `<button class="pop-offers-btn" data-chain="${esc(s.chain)}" data-id="${esc(s.store_id)}" data-name="${esc(s.name)}">Visa veckans erbjudanden</button>`
    : "";
  return `<div class="store-pop">
    <button class="pop-fav${isFav(s) ? " on" : ""}" aria-label="Favorit">&#9733;</button>
    <span class="pop-brand" style="background:${color}">${esc(brand)}</span>
    <h6>${esc(s.name)}</h6>
    <div class="pop-addr">${esc(addr)}</div>
    ${oh.today ? `<div class="pop-hours">Idag: ${esc(oh.today)}</div>` : ""}
    ${weekHtml(oh)}
    ${tags ? `<div class="pop-tags">${tags}</div>` : ""}
    ${offersBtn}
    ${linkHtml ? `<div class="pop-links">${linkHtml}</div>` : ""}
  </div>`;
}

function visibleStores() {
  const q = state.query.toLowerCase();
  const pf = state.productFilter;
  return state.stores.filter((s) => {
    if (!state.enabled.has(s.chain)) return false;
    if (pf && !pf.stores.has(`${s.chain}:${s.store_id}`)) return false;
    if (state.onlyFavorites && !isFav(s)) return false;
    if (state.onlyOffers && !(s.links && s.links.offers)) return false;
    if (q) {
      const hay = `${s.name} ${(s.address || {}).city || ""} ${(s.address || {}).street || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function render() {
  const list = visibleStores();
  cluster.clearLayers();
  const markers = [];
  for (const s of list) {
    if (!s.location) continue;
    const m = L.marker([s.location.lat, s.location.lng], { icon: markerIcon(s.chain) });
    m.bindPopup(popupHtml(s), { closeButton: true });
    s._marker = m;
    m._store = s;
    markers.push(m);
  }
  cluster.addLayers(markers);

  document.getElementById("resultCount").textContent =
    `${list.length} butiker (${markers.length} på kartan)`;

  renderList();
}

// Bara butikslistan (utan att röra kartmarkörerna -> öppen popup bevaras).
function renderList() {
  const list = visibleStores();
  const lc = document.getElementById("storeList");
  lc.innerHTML = "";
  for (const s of list.slice(0, 200)) {
    const el = document.createElement("div");
    el.className = "store-item";
    const city = (s.address || {}).city || "";
    const meta = state.chains[s.chain] || {};
    el.innerHTML = `<div class="s-body">
        <div class="s-top">
          <span class="s-chip" style="background:${meta.color || "#666"}">${esc(meta.label || s.chain)}</span>
          <span class="s-name">${esc(s.name)}</span>
        </div>
        <div class="s-meta">${esc(city)}</div>
      </div>
      <button class="s-fav${isFav(s) ? " on" : ""}" aria-label="Favorit">&#9733;</button>`;
    el.querySelector(".s-fav").onclick = (ev) => {
      ev.stopPropagation();
      toggleFav(s);
    };
    el.onclick = () => {
      if (s.location) {
        closeNav();
        map.setView([s.location.lat, s.location.lng], 14);
        if (s._marker) s._marker.openPopup();
      }
    };
    lc.appendChild(el);
  }
}

function renderChainFilters() {
  const box = document.getElementById("chainFilters");
  box.innerHTML = "";
  const counts = {};
  for (const s of state.stores) counts[s.chain] = (counts[s.chain] || 0) + 1;
  for (const [chain, meta] of Object.entries(state.chains)) {
    const on = state.enabled.has(chain);
    const row = document.createElement("label");
    row.className = "chain-row" + (on ? "" : " off");
    row.innerHTML = `
      <input type="checkbox" ${on ? "checked" : ""}>
      <span class="dot" style="background:${meta.color}"></span>
      <span class="name">${esc(meta.label)}</span>
      <span class="cnt">${counts[chain] || 0}${meta.offers_supported ? "" : " &middot; ⌀"}</span>`;
    row.querySelector("input").onchange = (e) => {
      if (e.target.checked) state.enabled.add(chain);
      else state.enabled.delete(chain);
      row.classList.toggle("off", !e.target.checked);
      render();
    };
    box.appendChild(row);
  }
}

async function loadChains() {
  const r = await fetch("/v1/chains");
  const d = await r.json();
  for (const c of d.chains) {
    state.chains[c.chain] = c;
    state.enabled.add(c.chain);
  }
  renderChainFilters();
  return d.chains;
}

async function loadStores() {
  const r = await fetch("/v1/stores");
  const d = await r.json();
  state.stores = d.stores;
  renderChainFilters();
  render();
}

// Filtrera kartan till butiker som har ETT ERBJUDANDE på en vald vara (EAN). Bygger på
// offers-cachen -> "med erbjudande", inte hyllsortiment (det vet vi inte).
async function filterMapByProduct(ean, name) {
  if (!ean) return;
  const bar = document.getElementById("productFilterBar");
  const txt = document.getElementById("productFilterText");
  bar.classList.remove("d-none");
  txt.textContent = `Laddar butiker med erbjudande på ${name || ean}…`;
  try {
    const d = await (await fetch(`/v1/products/${encodeURIComponent(ean)}/stores`)).json();
    const stores = new Set((d.stores || []).map((s) => `${s.chain}:${s.store_id}`));
    state.productFilter = { ean, name: name || ean, stores, count: d.count || stores.size };
    txt.textContent = state.productFilter.count
      ? `Butiker med erbjudande på: ${state.productFilter.name} (${state.productFilter.count})`
      : `Ingen butik har just nu ${state.productFilter.name} på erbjudande`;
    document.getElementById("productsPanel").classList.add("d-none");
    closeNav();
    render();
    if (state.productFilter.count) fitToVisible();
  } catch (e) {
    txt.textContent = "Kunde inte filtrera på varan.";
  }
}

function clearProductFilter() {
  state.productFilter = null;
  document.getElementById("productFilterBar").classList.add("d-none");
  render();
}

function fitToVisible() {
  const pts = visibleStores().filter((s) => s.location).map((s) => [s.location.lat, s.location.lng]);
  if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 13 });
}

document.getElementById("productFilterClear").addEventListener("click", clearProductFilter);

document.getElementById("search").addEventListener("input", (e) => {
  state.query = e.target.value.trim();
  render();
});
document.getElementById("onlyOffers").addEventListener("change", (e) => {
  state.onlyOffers = e.target.checked;
  render();
});
document.getElementById("onlyFavorites").addEventListener("change", (e) => {
  state.onlyFavorites = e.target.checked;
  render();
});

// ---- Erbjudanden ----
let currentOffers = [];

function dealBadge(o) {
  if (o.deal_type === "multibuy") {
    const q = o.multibuy_qty ? `${o.multibuy_qty} för` : "Flerköp";
    return `<span class="o-deal o-deal--mb">${q}</span>`;
  }
  if (o.deal_type === "by_weight") return `<span class="o-deal o-deal--bw">Per vikt</span>`;
  return "";
}

function offerCard(o) {
  const cmp = o.comparison_value
    ? `<span class="o-cmp"${o.comparison_derived ? ' title="Beräknat ur pris/storlek – ungefärligt"' : ""}>${o.comparison_derived ? "≈ " : ""}${kr(o.comparison_value)} kr/${esc(o.comparison_unit || "")}</span>`
    : "";
  const imgEan = o.eans && o.eans[0];
  const imgSrc = imgEan ? `/v1/products/${encodeURIComponent(imgEan)}/image?size=thumb` : o.image;
  const img = imgSrc
    ? `<img class="o-img" src="${esc(imgSrc)}" loading="lazy" alt=""${imgEan && o.image ? ` onerror="this.onerror=null;this.src='${esc(o.image)}'"` : ""}>`
    : `<div class="o-img o-img--ph"></div>`;
  const valid = o.valid_to ? `t.o.m. ${esc(o.valid_to)}` : "";
  const member = o.member_price ? `<span class="o-member">Klubbpris</span>` : "";
  const sv = Math.round((o.savings || 0) * 100) / 100;
  const save = sv > 0 ? `<span class="o-save">spara ${sv} kr</span>` : "";
  const deal = dealBadge(o);
  const pkg = o.package_size || o.package;
  const origin = (o.origin && o.origin.length) ? o.origin.join("/") : "";
  const meta = [o.brand, pkg, origin].filter(Boolean).map(esc).join(" &middot; ");
  const catChip = o.category ? `<span class="o-cat">${esc(catLabels[o.category] || o.category)}</span>` : "";
  const foot = (catChip || valid)
    ? `${catChip}${valid ? `<span class="o-valid">${valid}</span>` : ""}`
    : "";
  const ean = o.eans && o.eans[0];
  const info = ean
    ? `<button class="o-info" data-ean="${esc(ean)}" data-chain="${esc(o.chain || "")}" data-name="${esc(o.name || "")}">Visa information</button>`
    : "";
  return `<div class="offer-card">
    ${img}
    <div class="o-body">
      <div class="o-name">${esc(o.name || "")}</div>
      <div class="o-meta">${meta}${o.store_name ? ` &middot; <span class="o-store">${esc(o.store_name)}</span>` : ""}</div>
      <div class="o-price-row">
        <span class="o-price">${esc(o.price_text || "")}</span>
        ${deal}
        ${member}
        ${cmp}
        ${save}
      </div>
      ${foot ? `<div class="o-foot">${foot}</div>` : ""}
      ${info}
    </div>
  </div>`;
}

async function openProductModal(ean, chain, name) {
  const modal = document.getElementById("productModal");
  document.getElementById("productModalTitle").textContent = name || "Produktinfo";
  const body = document.getElementById("productModalBody");
  body.innerHTML = '<div class="text-muted small">Laddar produktinfo&hellip;</div>';
  modal.classList.remove("d-none");
  try {
    const d = await (await fetch(`/v1/products/${encodeURIComponent(ean)}?prefer_chain=${encodeURIComponent(chain || "")}`)).json();
    body.innerHTML = renderProductInfo(d, chain) + '<div id="priceHistorySection" class="mt-3 pt-2 border-top"></div>';
  } catch (e) {
    body.innerHTML = '<div class="text-danger small">Kunde inte hämta produktinfo.</div><div id="priceHistorySection" class="mt-3"></div>';
  }
  loadPriceHistory(ean);
}

async function loadPriceHistory(ean) {
  const el = document.getElementById("priceHistorySection");
  if (!el) return;
  el.innerHTML = '<div class="text-muted small">Laddar prishistorik&hellip;</div>';
  try {
    const h = await (await fetch(`/v1/products/${encodeURIComponent(ean)}/history`)).json();
    el.innerHTML = renderPriceHistory(h);
  } catch (e) {
    el.innerHTML = "";  // prishistorik är sekundär - tyst om den fallerar
  }
}

function fmtPHDate(iso) {
  if (!iso) return "";
  const dt = new Date(iso);
  return isNaN(dt) ? "" : dt.toLocaleDateString("sv-SE", { day: "numeric", month: "short" });
}

// Bygger en inline-SVG prishistorik-graf. Erbjudande-data = fyndspårning: varje punkt är en
// prisändring, en punkt hålls (stegfunktion) till sitt valid_to, och linjen BRYTS där nästa
// observation ligger efter valid_to (varan var inte nedsatt -> lucka, inte en rät linje över).
function priceHistorySvg(chains) {
  const W = 320, H = 150, ML = 38, MR = 10, MT = 12, MB = 22;
  const ts = (s) => (s ? new Date(s).getTime() : NaN);
  let tmin = Infinity, tmax = -Infinity, pmin = Infinity, pmax = -Infinity;
  for (const c of chains) for (const p of c.points) {
    if (p.price == null) continue;
    const o = ts(p.observed_at), v = ts(p.valid_to) || o;
    if (!isNaN(o)) { tmin = Math.min(tmin, o); tmax = Math.max(tmax, o); }
    if (!isNaN(v)) tmax = Math.max(tmax, v);
    pmin = Math.min(pmin, p.price); pmax = Math.max(pmax, p.price);
  }
  if (!isFinite(pmin)) return "";
  const DAY = 86400000;
  if (tmax - tmin < DAY) { tmin -= 3 * DAY; tmax += 3 * DAY; }  // singel-tidpunkt: ge bredd
  if (pmax - pmin < 0.5) { pmin -= 1; pmax += 1; }
  pmin = Math.max(0, Math.floor(pmin));
  const x = (t) => ML + ((t - tmin) / (tmax - tmin)) * (W - ML - MR);
  const y = (p) => H - MB - ((p - pmin) / (pmax - pmin)) * (H - MT - MB);
  const parts = [];
  // y-axel: två referenslinjer (min/max-pris)
  for (const pv of [pmin, pmax]) {
    parts.push(`<line x1="${ML}" y1="${y(pv).toFixed(1)}" x2="${W - MR}" y2="${y(pv).toFixed(1)}" stroke="#eee"/>`);
    parts.push(`<text x="${ML - 4}" y="${(y(pv) + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="#999">${kr(pv)}</text>`);
  }
  for (const c of chains) {
    const col = (state.chains[c.chain] || {}).color || "#666";
    const lab = (state.chains[c.chain] || {}).label || c.chain;
    const pts = c.points.filter((p) => p.price != null);
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i], o = ts(p.observed_at), v = ts(p.valid_to) || o, yp = y(p.price);
      // håll priset från observation till valid_to (stegfunktionens horisontella del)
      parts.push(`<line x1="${x(o).toFixed(1)}" y1="${yp.toFixed(1)}" x2="${x(Math.min(v, tmax)).toFixed(1)}" y2="${yp.toFixed(1)}" stroke="${col}" stroke-width="2"/>`);
      // koppla till nästa punkt bara om den börjar senast vid detta valid_to (annars lucka)
      if (i + 1 < pts.length) {
        const n = pts[i + 1], no = ts(n.observed_at);
        if (no <= v + DAY) parts.push(`<line x1="${x(Math.min(v, no)).toFixed(1)}" y1="${yp.toFixed(1)}" x2="${x(no).toFixed(1)}" y2="${y(n.price).toFixed(1)}" stroke="${col}" stroke-width="2"/>`);
      }
      const ring = p.member_price ? `<circle cx="${x(o).toFixed(1)}" cy="${yp.toFixed(1)}" r="5" fill="none" stroke="${col}" stroke-width="1"/>` : "";
      const tip = `${lab} ${fmtPHDate(p.observed_at)}: ${kr(p.price)} kr${p.member_price ? " (medlem)" : ""}${p.comparison_value ? ` · ${kr(p.comparison_value)} kr/${p.comparison_unit || ""}` : ""}${p.stores > 1 ? ` · ${p.stores} butiker` : ""}`;
      parts.push(`${ring}<circle cx="${x(o).toFixed(1)}" cy="${yp.toFixed(1)}" r="2.6" fill="${col}"><title>${esc(tip)}</title></circle>`);
    }
  }
  // x-axel: tidsspann
  parts.push(`<text x="${ML}" y="${H - 6}" font-size="9" fill="#999">${fmtPHDate(new Date(tmin).toISOString())}</text>`);
  parts.push(`<text x="${W - MR}" y="${H - 6}" text-anchor="end" font-size="9" fill="#999">${fmtPHDate(new Date(tmax).toISOString())}</text>`);
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:360px" role="img">${parts.join("")}</svg>`;
}

function renderPriceHistory(h) {
  const chains = (h && h.chains) || [];
  const total = chains.reduce((a, c) => a + (c.points ? c.points.length : 0), 0);
  if (!total) {
    return '<div class="small"><strong>Prishistorik</strong><div class="text-muted">Ingen prishistorik än - byggs upp vid kommande synkar.</div></div>';
  }
  const svg = priceHistorySvg(chains);
  const legend = chains.map((c) => {
    const m = state.chains[c.chain] || {};
    const last = c.points[c.points.length - 1];
    const px = last && last.price != null ? ` ${kr(last.price)} kr` : "";
    return `<span class="badge" style="background:${m.color || "#666"};color:#fff">${esc(m.label || c.chain)}${px}</span>`;
  }).join(" ");
  const single = total <= chains.length;
  const note = single
    ? "Bara en observation per kedja än - kurvan växer vid kommande synkar."
    : "Varje punkt = en prisändring. Ring = medlemspris. Linjen bryts där varan inte var nedsatt.";
  return `<div class="small"><strong>Prishistorik</strong> <span class="text-muted">(kampanjpris, fyndspårning)</span>
    <div class="my-1">${legend}</div>${svg}
    <div class="text-muted" style="font-size:11px">${note}</div></div>`;
}

function fmtInfoDate(iso) {
  if (!iso) return "";
  const dt = new Date(iso);
  return isNaN(dt) ? "" : dt.toLocaleDateString("sv-SE");
}

function renderProductInfo(d, chain) {
  if (!d.found || !d.info) {
    const checked = d.fetched_at ? ` <span class="text-muted">(kontrollerad ${fmtInfoDate(d.fetched_at)})</span>` : "";
    return `<div class="text-muted small">Ingen produktinfo hittades för den här varan.${checked}</div>`;
  }
  const x = d.info, P = [];
  if (x.description) P.push(`<p class="small">${esc(x.description)}</p>`);
  if (x.ingredients) P.push(`<p class="small mb-1"><strong>Innehåll:</strong> ${esc(x.ingredients)}</p>`);
  if (x.allergens && x.allergens.length) P.push(`<p class="small mb-1"><strong>Allergener:</strong> ${x.allergens.map(a => `<span class="badge bg-warning text-dark">${esc(a)}</span>`).join(" ")}</p>`);
  const orig = [x.origin, x.province].filter(Boolean).join(" · ");
  if (orig) P.push(`<p class="small mb-1"><strong>Ursprung:</strong> ${esc(orig)}</p>`);
  if (x.storage) P.push(`<p class="small mb-1"><strong>Förvaring:</strong> ${esc(x.storage)}</p>`);
  if (x.nutrition && x.nutrition.length) {
    const b = x.nutrition_basis ? ` (per ${esc(x.nutrition_basis.value || "")} ${esc(x.nutrition_basis.unit || "")})` : "";
    P.push(`<p class="small mb-1"><strong>Näring${b}:</strong> ${x.nutrition.map(n => `${esc(n.label)} ${esc(n.value)}${esc(n.unit || "")}`).join(", ")}</p>`);
  }
  if (x.sources && x.sources.length) {
    const chips = x.sources.map((c) => {
      const m = state.chains[c] || {};
      return `<span class="badge" style="background:${m.color || "#666"};color:#fff">${esc(m.label || c)}</span>`;
    }).join(" ");
    P.push(`<p class="small mb-0 mt-1"><span class="text-muted">Källa:</span> ${chips}</p>`);
  }
  const upd = fmtInfoDate(d.fetched_at);
  if (upd) P.push(`<p class="small text-muted mb-0 mt-1">Uppdaterad ${upd}</p>`);
  return P.join("") || '<div class="text-muted small">Ingen detaljdata.</div>';
}

function closeProductModal() { document.getElementById("productModal").classList.add("d-none"); }
document.getElementById("productClose").addEventListener("click", closeProductModal);
document.getElementById("productModal").addEventListener("click", (e) => { if (e.target.id === "productModal") closeProductModal(); });

// Lightbox: klick på en produktbild visar den i full storlek (för att granska förpackningen).
function openLightbox(src) {
  let lb = document.getElementById("lightbox");
  if (!lb) {
    lb = document.createElement("div");
    lb.id = "lightbox"; lb.className = "lb-overlay"; lb.hidden = true;
    lb.innerHTML = '<img class="lb-img" alt="">';
    lb.addEventListener("click", () => { lb.hidden = true; });
    document.body.appendChild(lb);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") lb.hidden = true; });
  }
  lb.querySelector(".lb-img").src = src;
  lb.hidden = false;
}
document.addEventListener("click", (e) => {
  const img = e.target.closest("img.o-img");
  if (!img) return;
  const src = img.getAttribute("src") || "";
  openLightbox(src.includes("size=thumb") ? src.replace("size=thumb", "size=full") : src);
});
document.getElementById("offersList").addEventListener("click", (e) => {
  const b = e.target.closest(".o-info");
  if (b) openProductModal(b.dataset.ean, b.dataset.chain, b.dataset.name);
});

function sortOffers(list, mode) {
  const arr = [...list];
  if (mode === "savings") arr.sort((a, b) => (b.savings || 0) - (a.savings || 0));
  else if (mode === "price") arr.sort((a, b) => (a.price ?? 1e9) - (b.price ?? 1e9));
  else if (mode === "name") arr.sort((a, b) => (a.name || "").localeCompare(b.name || "", "sv"));
  return arr;
}

function sortCompareCards(list, mode) {
  const arr = [...list];
  if (mode === "savings") arr.sort((a, b) => (b.spread || 0) - (a.spread || 0));
  else if (mode === "price") arr.sort((a, b) => (a.min ?? 1e9) - (b.min ?? 1e9));
  else if (mode === "name") arr.sort((a, b) => (a.name || "").localeCompare(b.name || "", "sv"));
  return arr;
}

let catLabels = {};

async function loadCategories() {
  try {
    const d = await (await fetch("/v1/categories")).json();
    catLabels = Object.fromEntries((d.categories || []).map((c) => [c.key, c.label]));
    populateProductsCategory();
    if (document.body.classList.contains("browse-mode")) renderBrowseCats();  // deep-link: fyll chips
  } catch (e) { catLabels = {}; }
}

function fillCatSelect(selId, items) {
  const sel = document.getElementById(selId);
  if (!sel) return;
  const counts = {};
  items.forEach((o) => { if (o.category) counts[o.category] = (counts[o.category] || 0) + 1; });
  const opts = Object.keys(counts)
    .map((k) => ({ k, label: catLabels[k] || k, n: counts[k] }))
    .sort((a, b) => a.label.localeCompare(b.label, "sv"));
  sel.innerHTML = `<option value="">Alla kategorier (${items.length})</option>` +
    opts.map((o) => `<option value="${esc(o.k)}">${esc(o.label)} (${o.n})</option>`).join("");
  sel.value = "";
}

function populateOffersCategory() {
  fillCatSelect("offersCategory", currentOffers);
}

function populateCompareCategory() {
  const items = compareRender === renderFavOffers
    ? [...((favOffersData || {}).offers || []), ...((favOffersData || {}).compared || [])]
    : currentCompare;
  fillCatSelect("compareCategory", items);
  document.getElementById("compareDeal").value = "";
  document.getElementById("favSort").value = "default";
}

function renderOffers(filterText) {
  const q = (filterText || "").toLowerCase();
  const mode = document.getElementById("offersSort").value;
  const cat = document.getElementById("offersCategory").value;
  const deal = document.getElementById("offersDeal").value;
  let list = currentOffers.filter((o) =>
    (!q || `${o.name} ${o.brand} ${o.category_raw}`.toLowerCase().includes(q)) &&
    (!cat || o.category === cat) &&
    (!deal || o.deal_type === deal));
  list = sortOffers(list, mode);
  const el = document.getElementById("offersList");
  el.innerHTML = list.length
    ? list.map(offerCard).join("")
    : `<div class="text-muted small p-2">Inga erbjudanden.</div>`;
}

async function showOffers(chain, storeId, name) {
  const panel = document.getElementById("offersPanel");
  document.getElementById("offersTitle").textContent = name;
  document.getElementById("offersList").innerHTML =
    `<div class="text-muted small p-2">Laddar erbjudanden&hellip;</div>`;
  document.getElementById("offersFilter").value = "";
  document.getElementById("comparePanel").classList.add("d-none");
  panel.classList.remove("d-none");
  openNav();
  try {
    const r = await fetch(`/v1/stores/${chain}/${storeId}/offers`);
    const d = await r.json();
    currentOffers = d.offers || [];
    document.getElementById("offersTitle").textContent = `${name} (${currentOffers.length})`;
    populateOffersCategory();
    if (d.note && !currentOffers.length) {
      document.getElementById("offersList").innerHTML =
        `<div class="text-muted small p-2">${esc(d.note)}</div>`;
      return;
    }
    renderOffers("");
  } catch (e) {
    document.getElementById("offersList").innerHTML =
      `<div class="text-danger small p-2">Kunde inte hämta erbjudanden.</div>`;
  }
}

document.getElementById("offersBack").addEventListener("click", () => {
  document.getElementById("offersPanel").classList.add("d-none");
});

// ---- Prisjämförelse i närheten ----
let currentCompare = [];

function compareCard(p) {
  const variantTag = p.variant_count > 1
    ? ` <span class="cmp-variants" title="${esc((p.variants || []).join(", "))}">${p.variant_count} sorter</span>`
    : "";
  const catLabel = p.category ? (catLabels[p.category] || p.category) : "";
  const sub = [p.brand, catLabel].filter(Boolean).map(esc).join(" &middot; ") + variantTag;
  const cmpSrc = p.ean ? `/v1/products/${encodeURIComponent(p.ean)}/image?size=thumb` : p.image;
  const img = cmpSrc
    ? `<img class="o-img" src="${esc(cmpSrc)}" loading="lazy" alt=""${p.ean && p.image ? ` onerror="this.onerror=null;this.src='${esc(p.image)}'"` : ""}>`
    : `<div class="o-img o-img--ph"></div>`;
  const anyDerived = p.compare_by === "unit_price" && p.offers.some(o => o.comparison_derived);
  const spreadLabel = p.compare_by === "unit_price"
    ? `${anyDerived ? "≈ " : ""}${kr(p.spread)} kr/${esc(p.unit)}` : `${kr(p.spread)} kr`;
  const rows = p.offers.map((o, i) => {
    const meta = state.chains[o.chain] || {};
    const big = p.compare_by === "unit_price"
      ? (o.comparison_value != null ? `${o.comparison_derived ? "≈ " : ""}${kr(o.comparison_value)} kr/${esc(o.comparison_unit || "")}` : "–")
      : (o.price != null ? `${kr(o.price)} kr` : "–");
    const member = o.member_price ? `<span class="o-member">Klubbpris</span>` : "";
    return `<div class="cmp-row${i === 0 ? " cmp-best" : ""}">
      <span class="dot" style="background:${meta.color || "#666"}"></span>
      <span class="cmp-chain">${esc(meta.label || o.chain)}</span>
      <span class="cmp-big">${big}</span>${member}${dealBadge(o)}
      <div class="cmp-sub">${esc(o.price_text || "")} &middot; ${esc(o.store_name || "")}${o.distance_km != null ? " " + o.distance_km + "km" : ""}</div>
    </div>`;
  }).join("");
  return `<div class="offer-card cmp-card">
    <div class="cmp-top">
      ${img}
      <div class="cmp-id"><div class="o-name">${esc(p.name || "")}</div><div class="o-meta">${sub}</div></div>
      <span class="cmp-spread" title="prisskillnad">spara ${spreadLabel}</span>
    </div>
    <div class="cmp-rows">${rows}</div>
  </div>`;
}

function renderCompare(filterText) {
  const q = (filterText || "").toLowerCase();
  const cat = document.getElementById("compareCategory").value;
  const deal = document.getElementById("compareDeal").value;
  let list = currentCompare.filter((p) =>
    (!q || `${p.name} ${p.brand} ${p.category}`.toLowerCase().includes(q)) &&
    (!cat || p.category === cat) &&
    (!deal || (p.offers || []).some((o) => o.deal_type === deal)));
  list = sortCompareCards(list, document.getElementById("favSort").value);
  document.getElementById("compareList").innerHTML = list.length
    ? list.map(compareCard).join("")
    : `<div class="text-muted small p-2">Inga produkter på erbjudande hos flera kedjor här.</div>`;
}

async function showCompare() {
  compareRender = renderCompare;
  const c = map.getCenter();
  const radius = document.getElementById("compareRadius").value;
  const panel = document.getElementById("comparePanel");
  document.getElementById("compareTitle").textContent = "Jämför…";
  document.getElementById("compareFilter").value = "";
  document.getElementById("compareList").innerHTML =
    `<div class="text-muted small p-2">Laddar erbjudanden för butiker i närheten&hellip;</div>`;
  document.getElementById("offersPanel").classList.add("d-none");
  panel.classList.remove("d-none");
  openNav();
  try {
    const r = await fetch(
      `/v1/compare/near?lat=${c.lat.toFixed(5)}&lng=${c.lng.toFixed(5)}&radius_km=${radius}&min_chains=2`);
    const d = await r.json();
    currentCompare = d.products || [];
    document.getElementById("compareTitle").textContent =
      `${currentCompare.length} produkter (${d.stores_compared} butiker, ${radius} km)`;
    populateCompareCategory();
    renderCompare("");
  } catch (e) {
    document.getElementById("compareList").innerHTML =
      `<div class="text-danger small p-2">Kunde inte hämta jämförelsen.</div>`;
  }
}

async function showCompareFavorites() {
  const favs = [...state.favorites].filter((k) => COMPARE_CHAINS.includes(k.split(":")[0]));
  const panel = document.getElementById("comparePanel");
  document.getElementById("offersPanel").classList.add("d-none");
  document.getElementById("compareFilter").value = "";
  document.getElementById("compareTitle").textContent = "Jämför favoriter…";
  panel.classList.remove("d-none");
  openNav();
  if (!favs.length) {
    currentCompare = [];
    document.getElementById("compareTitle").textContent = "Jämför favoriter";
    document.getElementById("compareList").innerHTML =
      `<div class="text-muted small p-2">Inga jämförbara favoriter valda. Markera ICA/Coop/Willys/Hemköp-butiker med &#9733; i listan.</div>`;
    return;
  }
  document.getElementById("compareList").innerHTML =
    `<div class="text-muted small p-2">Laddar erbjudanden för dina favoriter&hellip;</div>`;
  try {
    const r = await fetch(`/v1/compare/stores?stores=${favs.join(",")}&min_chains=2`);
    const d = await r.json();
    currentCompare = d.products || [];
    document.getElementById("compareTitle").textContent =
      `${currentCompare.length} produkter (${d.stores_compared} favoriter)`;
    populateCompareCategory();
    renderCompare("");
  } catch (e) {
    document.getElementById("compareList").innerHTML =
      `<div class="text-danger small p-2">Kunde inte hämta jämförelsen.</div>`;
  }
}

document.getElementById("compareBtn").addEventListener("click", showCompare);
let favOffersData = null;
let compareRender = renderCompare;

async function showFavoriteOffers() {
  const panel = document.getElementById("comparePanel");
  document.getElementById("offersPanel").classList.add("d-none");
  document.getElementById("compareFilter").value = "";
  document.getElementById("compareTitle").textContent = "Mina favoriters erbjudanden…";
  panel.classList.remove("d-none");
  openNav();
  if (!state.favorites.size) {
    document.getElementById("compareList").innerHTML =
      `<div class="text-muted small p-2">Inga favoritbutiker valda. Markera butiker med &#9733; i listan.</div>`;
    return;
  }
  document.getElementById("compareList").innerHTML =
    `<div class="text-muted small p-2">Laddar dina favoriters erbjudanden&hellip;</div>`;
  compareRender = renderFavOffers;
  try {
    const d = await (await fetch("/v1/favorites/offers")).json();
    favOffersData = d;
    document.getElementById("compareTitle").textContent =
      `${d.count} erbjudanden (${(d.stores || []).length} favoriter)`;
    populateCompareCategory();
    renderFavOffers("");
  } catch (e) {
    document.getElementById("compareList").innerHTML =
      `<div class="text-danger small p-2">Kunde inte hämta dina favoriters erbjudanden.</div>`;
  }
}

function renderFavOffers(filterText) {
  const q = (filterText || "").toLowerCase();
  const cat = document.getElementById("compareCategory").value;
  const deal = document.getElementById("compareDeal").value;
  const d = favOffersData || { offers: [], compared: [] };
  const hit = (s) => !q || s.toLowerCase().includes(q);
  const okCat = (o) => !cat || o.category === cat;
  const okDealCard = (p) => !deal || (p.offers || []).some((o) => o.deal_type === deal);
  const mode = document.getElementById("favSort").value;
  let compared = (d.compared || []).filter((p) => hit(`${p.name} ${p.brand} ${p.category}`) && okCat(p) && okDealCard(p));
  compared = sortCompareCards(compared, mode);
  let offers = (d.offers || []).filter((o) => hit(`${o.name} ${o.brand} ${o.category_raw} ${o.store_name}`) && okCat(o) && (!deal || o.deal_type === deal));
  offers = sortOffers(offers, mode);
  const parts = [];
  if (compared.length)
    parts.push(`<div class="fav-sec">Finns hos flera av dina favoriter</div>` + compared.map(compareCard).join(""));
  parts.push(`<div class="fav-sec">Alla erbjudanden (${offers.length})</div>` +
    (offers.length ? offers.map(offerCard).join("") : `<div class="text-muted small p-2">Inga erbjudanden.</div>`));
  document.getElementById("compareList").innerHTML = parts.join("");
}

document.getElementById("compareFavBtn").addEventListener("click", () => {
  compareRender = renderCompare;
  showCompareFavorites();
});
document.getElementById("favOffersBtn").addEventListener("click", showFavoriteOffers);
document.getElementById("favSort").addEventListener("change", () => {
  compareRender(document.getElementById("compareFilter").value.trim());
});
document.getElementById("compareBack").addEventListener("click", () => {
  document.getElementById("comparePanel").classList.add("d-none");
});
document.getElementById("compareFilter").addEventListener("input", (e) => {
  compareRender(e.target.value.trim());
});
document.getElementById("compareCategory").addEventListener("change", () => {
  compareRender(document.getElementById("compareFilter").value.trim());
});
document.getElementById("compareDeal").addEventListener("change", () => {
  compareRender(document.getElementById("compareFilter").value.trim());
});

// ---- Global produktsök + kategori-bläddring (ur erbjudande-cachen) ----
function productCard(p) {
  const imgSrc = p.ean ? `/v1/products/${encodeURIComponent(p.ean)}/image?size=thumb` : p.image;
  const img = imgSrc
    ? `<img class="o-img" src="${esc(imgSrc)}" loading="lazy" alt=""${p.ean && p.image ? ` onerror="this.onerror=null;this.src='${esc(p.image)}'"` : ""}>`
    : `<div class="o-img o-img--ph"></div>`;
  const origin = (p.origin && p.origin.length) ? p.origin.join("/") : "";
  const meta = [p.brand, p.package_size, origin].filter(Boolean).map(esc).join(" &middot; ");
  const catChip = p.category ? `<span class="o-cat">${esc(catLabels[p.category] || p.category)}</span>` : "";
  const chains = (p.chains || []).map((c) => {
    const m = state.chains[c] || {};
    return `<span class="o-chainchip" style="background:${m.color || "#666"}">${esc(m.label || c)}</span>`;
  }).join("");
  const price = p.price_min != null
    ? (p.price_min === p.price_max ? `${kr(p.price_min)} kr` : `${kr(p.price_min)}–${kr(p.price_max)} kr`)
    : "";
  const stores = p.offer_count ? `<span class="o-stores">${p.offer_count} butik${p.offer_count === 1 ? "" : "er"}</span>` : "";
  const actions = p.ean
    ? `<div class="o-actions"><button class="o-info" data-ean="${esc(p.ean)}" data-chain="" data-name="${esc(p.name || "")}">Visa information</button><button class="o-map" data-ean="${esc(p.ean)}" data-name="${esc(p.name || "")}">Visa på karta</button></div>`
    : "";
  return `<div class="offer-card">
    ${img}
    <div class="o-body">
      <div class="o-name">${esc(p.name || "")}</div>
      <div class="o-meta">${meta}</div>
      <div class="o-price-row"><span class="o-price">${esc(price)}</span>${dealBadge(p)}</div>
      <div class="o-foot">${catChip}${chains}${stores}</div>
      ${actions}
    </div>
  </div>`;
}

// Katalog-kort: hela sortimentet, nationellt HYLLPRIS per kedja (ej erbjudanden -> ingen
// deal-badge/offer_count). Jämförpris kan vara beräknat (≈) när kedjan inte gav det.
function catalogCard(p) {
  const imgSrc = p.ean ? `/v1/products/${encodeURIComponent(p.ean)}/image?size=thumb` : p.image;
  const img = imgSrc
    ? `<img class="o-img" src="${esc(imgSrc)}" loading="lazy" alt=""${p.ean && p.image ? ` onerror="this.onerror=null;this.src='${esc(p.image)}'"` : ""}>`
    : `<div class="o-img o-img--ph"></div>`;
  const origin = (p.origin && p.origin.length) ? p.origin.join("/") : "";
  const meta = [p.brand, p.package_size, origin].filter(Boolean).map(esc).join(" &middot; ");
  const catChip = p.category ? `<span class="o-cat">${esc(catLabels[p.category] || p.category)}</span>` : "";
  const prices = (p.prices || []).map((pr) => {
    const m = state.chains[pr.chain] || {};
    const cmp = pr.comparison_value != null
      ? ` <span class="text-muted">(${kr(pr.comparison_value)}${pr.comparison_derived ? "≈" : ""} kr/${esc(pr.comparison_unit || "")})</span>`
      : "";
    const hasOffer = pr.offer_price != null;
    // hyllpris (struket om erbjudande finns) + ev. aktuellt erbjudandepris
    const shelf = pr.price != null ? (hasOffer ? `<s class="text-muted">${kr(pr.price)} kr</s>` : `${kr(pr.price)} kr`) : (hasOffer ? "" : "-");
    const offer = hasOffer
      ? `<span class="o-offer">rea ${kr(pr.offer_price)} kr${pr.offer_member ? " klubb" : ""}${pr.offer_valid_to ? ` <span class="text-muted">t.o.m. ${esc(pr.offer_valid_to)}</span>` : ""}</span>`
      : "";
    return `<div class="o-shelf-row"><span class="o-chainchip" style="background:${m.color || "#666"}">${esc(m.label || pr.chain)}</span><span>${shelf}${shelf && offer ? " " : ""}${offer}${cmp}</span></div>`;
  }).join("");
  const range = p.price_min != null
    ? (p.price_min === p.price_max ? `${kr(p.price_min)} kr` : `${kr(p.price_min)}–${kr(p.price_max)} kr`)
    : "";
  const offerBadge = p.on_offer
    ? `<span class="o-offer-badge">På erbjudande${p.offer_min != null ? ` fr. ${kr(p.offer_min)} kr` : ""}</span>`
    : "";
  const actions = p.ean
    ? `<div class="o-actions"><button class="o-info" data-ean="${esc(p.ean)}" data-chain="" data-name="${esc(p.name || "")}">Visa information</button><button class="o-map" data-ean="${esc(p.ean)}" data-name="${esc(p.name || "")}">Visa på karta</button></div>`
    : "";
  return `<div class="offer-card">
    ${img}
    <div class="o-body">
      <div class="o-name">${esc(p.name || "")}</div>
      <div class="o-meta">${meta}</div>
      <div class="o-price-row"><span class="o-price">${esc(range)}</span> <span class="o-shelf-tag">hyllpris</span>${offerBadge}</div>
      <div class="o-shelf">${prices}</div>
      <div class="o-foot">${catChip}</div>
      ${actions}
    </div>
  </div>`;
}

function populateProductsCategory() {
  const sel = document.getElementById("productsCategory");
  const opts = Object.entries(catLabels)
    .map(([k, label]) => ({ k, label }))
    .sort((a, b) => a.label.localeCompare(b.label, "sv"));
  sel.innerHTML = `<option value="">Alla kategorier</option>` +
    opts.map((o) => `<option value="${esc(o.k)}">${esc(o.label)}</option>`).join("");
}

function openProductsPanel() {
  document.getElementById("offersPanel").classList.add("d-none");
  document.getElementById("comparePanel").classList.add("d-none");
  document.getElementById("productsPanel").classList.remove("d-none");
  openNav();
}

let productsMode = "offers";   // "offers" (cache, snabb) | "catalog" (live fan-out, hyllpris)
let productsToken = 0;          // race-guard: bara senaste sökningen får skriva resultat

async function loadProducts() {
  const q = document.getElementById("productsFilter").value.trim();
  const cat = document.getElementById("productsCategory").value;
  const list = document.getElementById("productsList");
  const title = document.getElementById("productsTitle");
  const token = ++productsToken;
  if (productsMode === "catalog") {
    if (q.length < 2) {
      title.textContent = "Hela sortimentet";
      list.innerHTML = `<div class="text-muted small p-2">Skriv minst 2 tecken. Söker hela sortimentet (hyllpris, inte erbjudanden).</div>`;
      return;
    }
    list.innerHTML = `<div class="text-muted small p-2">Söker hela sortimentet&hellip;</div>`;
    try {
      const d = await (await fetch(`/v1/products/catalog/browse?q=${encodeURIComponent(q)}&limit=60`)).json();
      if (token !== productsToken) return;  // en nyare sökning har redan tagit över
      const products = d.products || [];
      title.textContent = `Hela sortimentet (${products.length})`;
      list.innerHTML = products.length
        ? `<div class="text-muted xsmall px-2 pb-1">Hyllpris per kedja ur sortiment-katalogen, med aktuella erbjudanden markerade.</div>` + products.map(catalogCard).join("")
        : `<div class="text-muted small p-2">Inga träffar i sortimentet.</div>`;
    } catch (e) {
      if (token === productsToken) list.innerHTML = `<div class="text-danger small p-2">Kunde inte söka sortimentet.</div>`;
    }
    return;
  }
  let url;
  if (cat) url = `/v1/products/by-category?category=${encodeURIComponent(cat)}&limit=100`;
  else if (q.length >= 2) url = `/v1/products/search?q=${encodeURIComponent(q)}&limit=60`;
  else {
    title.textContent = "Produkter";
    list.innerHTML = `<div class="text-muted small p-2">Skriv minst 2 tecken eller välj kategori.</div>`;
    return;
  }
  list.innerHTML = `<div class="text-muted small p-2">Söker&hellip;</div>`;
  try {
    const d = await (await fetch(url)).json();
    if (token !== productsToken) return;
    let products = d.products || [];
    if (cat && q) {
      const ql = q.toLowerCase();
      products = products.filter((p) => `${p.name} ${p.brand}`.toLowerCase().includes(ql));
    }
    title.textContent = `Produkter (${products.length})`;
    list.innerHTML = products.length
      ? products.map(productCard).join("")
      : `<div class="text-muted small p-2">Inga produkter.</div>`;
  } catch (e) {
    if (token === productsToken) list.innerHTML = `<div class="text-danger small p-2">Kunde inte hämta produkter.</div>`;
  }
}

let productsTimer = null;
function scheduleProducts() {
  clearTimeout(productsTimer);
  productsTimer = setTimeout(loadProducts, 250);  // katalogen läses nu ur lokal DB (snabb)
}
function setProductsMode(mode) {
  if (mode === productsMode) return;
  productsMode = mode;
  document.getElementById("prodModeOffers").classList.toggle("active", mode === "offers");
  document.getElementById("prodModeCatalog").classList.toggle("active", mode === "catalog");
  document.getElementById("productsCategory").classList.toggle("d-none", mode === "catalog");  // katalogen saknar kategori-bläddring
  loadProducts();
}
document.getElementById("prodModeOffers").addEventListener("click", () => setProductsMode("offers"));
document.getElementById("prodModeCatalog").addEventListener("click", () => setProductsMode("catalog"));
document.getElementById("productSearch").addEventListener("input", (e) => {
  document.getElementById("productsFilter").value = e.target.value.trim();
  openProductsPanel();
  scheduleProducts();
});
document.getElementById("productsFilter").addEventListener("input", scheduleProducts);
document.getElementById("productsCategory").addEventListener("change", loadProducts);
document.getElementById("productsBack").addEventListener("click", () => {
  document.getElementById("productsPanel").classList.add("d-none");
});
document.getElementById("productsList").addEventListener("click", (e) => {
  const info = e.target.closest(".o-info");
  if (info) { openProductModal(info.dataset.ean, info.dataset.chain, info.dataset.name); return; }
  const mapBtn = e.target.closest(".o-map");
  if (mapBtn) filterMapByProduct(mapBtn.dataset.ean, mapBtn.dataset.name);
});

// ---- Bläddra-vy: kategori-navigering + produktrutnät (alternativ till kartan) ----
// Hash-driven: #sortiment[/k/<kategori>|/s/<sök>]. Kedje-/erbjudande-filter är transient UI-state.
const browseState = { q: "", category: "", chain: "", onlyOffers: false, limit: 60 };
let browseTimer = null, browseToken = 0, browseProducts = [];

function showBrowseUI() {
  document.body.classList.add("browse-mode");
  document.getElementById("map").classList.add("d-none");
  document.getElementById("browseView").classList.remove("d-none");
  document.getElementById("viewToggle").textContent = "Till kartan";
}
function showMapUI() {
  document.body.classList.remove("browse-mode");
  document.getElementById("browseView").classList.add("d-none");
  document.getElementById("map").classList.remove("d-none");
  document.getElementById("viewToggle").textContent = "Bläddra sortiment";
  map.invalidateSize();  // kartan var dold -> räkna om storlek
}

function applyBrowseHash() {
  const h = location.hash.slice(1);
  if (!h.startsWith("sortiment")) {
    if (document.body.classList.contains("browse-mode")) showMapUI();
    return;
  }
  showBrowseUI();
  const rest = h.slice("sortiment".length).replace(/^\//, "");
  browseState.category = rest.startsWith("k/") ? decodeURIComponent(rest.slice(2)) : "";
  browseState.q = rest.startsWith("s/") ? decodeURIComponent(rest.slice(2)) : "";
  browseState.limit = 60;
  document.getElementById("browseSearch").value = browseState.q;
  loadBrowseSummary();  // räknare per kategori + totaler (renderar chips)
  loadBrowse();
}
// Navigera: sätt hash OCH rendera direkt (vänta inte på hashchange, som kan vara opålitlig).
// _selfNav hindrar att vår egen hash-ändring trigger:ar en andra render; nollställs efter ticken
// (så en oförändrad hash inte låser kommande back/forward).
let _selfNav = false;
function browseGo(hash) {
  _selfNav = true;
  location.hash = hash;
  applyBrowseHash();
  setTimeout(() => { _selfNav = false; }, 0);
}
window.addEventListener("hashchange", () => {
  if (_selfNav) { _selfNav = false; return; }
  applyBrowseHash();  // back/forward/deep-link
});
document.getElementById("viewToggle").addEventListener("click", () =>
  browseGo(document.body.classList.contains("browse-mode") ? "" : "sortiment"));
// Force-refresh in i #sortiment: visa shellet direkt (produkterna fylls när bootdatan laddats).
if (location.hash.slice(1).startsWith("sortiment")) showBrowseUI();

function populateBrowseChain() {
  const sel = document.getElementById("browseChain");
  sel.innerHTML = `<option value="">Alla kedjor</option>` +
    Object.entries(state.chains).map(([c, m]) => `<option value="${esc(c)}">${esc(m.label || c)}</option>`).join("");
}

let browseSummary = null, _summaryChain = " ";  // sentinel: ingen summary hämtad än
async function loadBrowseSummary() {
  if (_summaryChain === browseState.chain && browseSummary) { renderBrowseCats(); renderBrowseSummary(); return; }
  _summaryChain = browseState.chain;
  try {
    const p = new URLSearchParams();
    if (browseState.chain) p.set("chain", browseState.chain);
    browseSummary = await (await fetch(`/v1/products/catalog/summary?${p}`)).json();
  } catch (e) { browseSummary = null; }
  renderBrowseCats();
  renderBrowseSummary();
}

function renderBrowseSummary() {
  const el = document.getElementById("browseSummary");
  if (!el) return;
  if (!browseSummary || !browseSummary.total) { el.innerHTML = ""; return; }
  const chains = Object.entries(browseSummary.by_chain || {}).map(([c, n]) => {
    const m = state.chains[c] || {};
    return `<span class="o-chainchip" style="background:${m.color || "#666"}">${esc(m.label || c)} ${n}</span>`;
  }).join("");
  el.innerHTML = `<strong>${browseSummary.total}</strong> produkter i katalogen` +
    (chains ? `<span class="browse-summary-chains">${chains}</span>` : "");
}

function renderBrowseCats() {
  const box = document.getElementById("browseCats");
  if (!box) return;
  const counts = (browseSummary && browseSummary.categories) || {};
  const entries = Object.entries(catLabels)
    .map(([k, label]) => ({ k, label, n: counts[k] }))
    .filter((e) => !browseSummary || e.n)  // göm tomma kategorier när antalen är kända
    .sort((a, b) => (b.n || 0) - (a.n || 0) || a.label.localeCompare(b.label, "sv"));
  box.innerHTML = entries.map((e) =>
    `<span class="browse-cat${browseState.category === e.k ? " on" : ""}" data-cat="${esc(e.k)}">${esc(e.label)}` +
    `${e.n != null ? ` <span class="browse-cat-n">${e.n}</span>` : ""}</span>`).join("");
}

function renderBrowseGrid() {
  const grid = document.getElementById("browseGrid");
  const title = document.getElementById("browseTitle");
  const more = document.getElementById("browseMore");
  let products = browseProducts;
  if (browseState.onlyOffers) products = products.filter((p) => p.on_offer);
  const head = browseState.q ? `Sök: "${browseState.q}"` : (catLabels[browseState.category] || browseState.category);
  title.textContent = head ? `${head} (${products.length}${browseState.onlyOffers ? " med erbjudande" : ""})` : "";
  grid.innerHTML = products.length
    ? products.map(catalogCard).join("")
    : `<div class="text-muted p-3">${browseState.onlyOffers && browseProducts.length ? "Inga produkter med erbjudande i urvalet." : "Inga produkter i sortiment-katalogen (kör en crawl i konsolen om den är tom)."}</div>`;
  more.innerHTML = browseProducts.length >= browseState.limit
    ? `<button id="browseMoreBtn" class="btn btn-sm btn-outline-dark">Visa fler</button>` : "";
  const mb = document.getElementById("browseMoreBtn");
  if (mb) mb.onclick = () => { browseState.limit += 60; loadBrowse(); };
}

async function loadBrowse() {
  const grid = document.getElementById("browseGrid");
  if (!browseState.q && !browseState.category) {
    browseProducts = [];
    document.getElementById("browseTitle").textContent = "";
    grid.innerHTML = `<div class="text-muted p-3">Välj en kategori ovan eller sök för att bläddra hela sortimentet.</div>`;
    document.getElementById("browseMore").innerHTML = "";
    return;
  }
  grid.innerHTML = `<div class="text-muted p-3">Laddar&hellip;</div>`;
  const p = new URLSearchParams({ limit: browseState.limit });
  if (browseState.q) p.set("q", browseState.q);
  if (browseState.category) p.set("category", browseState.category);
  if (browseState.chain) p.set("chain", browseState.chain);
  const token = ++browseToken;  // race-guard: bara senaste laddningen renderar
  try {
    const d = await (await fetch(`/v1/products/catalog/browse?${p}`)).json();
    if (token !== browseToken) return;  // en nyare laddning tog över (snabb växling)
    browseProducts = d.products || [];
    renderBrowseGrid();
  } catch (e) {
    if (token === browseToken) grid.innerHTML = `<div class="text-danger p-3">Kunde inte ladda sortimentet.</div>`;
  }
}

document.getElementById("browseSearch").addEventListener("input", (e) => {
  const q = e.target.value.trim();
  clearTimeout(browseTimer);
  browseTimer = setTimeout(() =>
    browseGo(q.length >= 2 ? "sortiment/s/" + encodeURIComponent(q) : "sortiment"), 250);
});
document.getElementById("browseChain").addEventListener("change", (e) => {
  browseState.chain = e.target.value;
  browseState.limit = 60;
  loadBrowseSummary();  // räknare/totaler för vald kedja
  loadBrowse();
});
document.getElementById("browseOffers").addEventListener("change", (e) => {
  browseState.onlyOffers = e.target.checked;
  renderBrowseGrid();  // re-filtrera den redan hämtade sidan, ingen ny fetch
});
document.getElementById("browseCats").addEventListener("click", (e) => {
  const c = e.target.closest(".browse-cat");
  if (!c) return;
  const cat = browseState.category === c.dataset.cat ? "" : c.dataset.cat;
  browseGo(cat ? "sortiment/k/" + encodeURIComponent(cat) : "sortiment");
});
document.getElementById("browseGrid").addEventListener("click", (e) => {
  const info = e.target.closest(".o-info");
  if (info) { openProductModal(info.dataset.ean, info.dataset.chain, info.dataset.name); return; }
  const mapBtn = e.target.closest(".o-map");
  if (mapBtn) { _selfNav = true; location.hash = ""; showMapUI(); filterMapByProduct(mapBtn.dataset.ean, mapBtn.dataset.name); setTimeout(() => { _selfNav = false; }, 0); }
});

// ---- Mobil: sidopanel som overlay ----
function openNav() { document.body.classList.add("nav-open"); }
function closeNav() { document.body.classList.remove("nav-open"); }
document.getElementById("navToggle").addEventListener("click", () => {
  document.body.classList.toggle("nav-open");
});
document.getElementById("navBackdrop").addEventListener("click", closeNav);
document.getElementById("offersFilter").addEventListener("input", (e) => {
  renderOffers(e.target.value.trim());
});
document.getElementById("offersSort").addEventListener("change", () => {
  renderOffers(document.getElementById("offersFilter").value.trim());
});
document.getElementById("offersCategory").addEventListener("change", () => {
  renderOffers(document.getElementById("offersFilter").value.trim());
});
document.getElementById("offersDeal").addEventListener("change", () => {
  renderOffers(document.getElementById("offersFilter").value.trim());
});
map.on("popupopen", (e) => {
  const root = e.popup.getElement();
  const s = e.popup._source && e.popup._source._store;
  const fav = root.querySelector(".pop-fav");
  if (fav && s) {
    fav.addEventListener("click", () => {
      toggleFav(s);
      fav.classList.toggle("on", isFav(s));
    });
  }
  const btn = root.querySelector(".pop-offers-btn");
  if (btn) {
    btn.addEventListener("click", () => {
      showOffers(btn.dataset.chain, btn.dataset.id, btn.dataset.name);
      map.closePopup();
    });
  }
});

// ---- Konton ----
function renderAuthArea() {
  document.body.classList.toggle("logged-in", !!state.user);
  const el = document.getElementById("authArea");
  if (state.user) {
    el.innerHTML = `<div class="acct">
        <button id="acctBtn" class="acct-btn">
          <span class="acct-email">${esc(state.user.email)}</span><span class="acct-caret">&#9662;</span>
        </button>
        <div id="acctMenu" class="acct-menu d-none">
          <button id="acctSettings" class="acct-item">Kontoinställningar</button>
          <button id="acctLogout" class="acct-item">Logga ut</button>
        </div>
      </div>`;
    el.querySelector("#acctBtn").onclick = (e) => { e.stopPropagation(); document.getElementById("acctMenu").classList.toggle("d-none"); };
    el.querySelector("#acctSettings").onclick = () => { closeAcctMenu(); openSettings(); };
    el.querySelector("#acctLogout").onclick = () => { closeAcctMenu(); doLogout(); };
  } else {
    el.innerHTML = `<button id="loginBtn" class="btn btn-sm btn-light">Logga in</button>`;
    el.querySelector("#loginBtn").onclick = () => openAuth();
  }
}

function closeAcctMenu() { const m = document.getElementById("acctMenu"); if (m) m.classList.add("d-none"); }
document.addEventListener("click", closeAcctMenu);  // klick utanför stänger menyn

async function loadUser() {
  try { state.user = await (await fetch("/v1/auth/me")).json(); } catch (e) { state.user = null; }
  renderAuthArea();
}

async function loadFavorites() {
  if (!state.user) { state.favorites = new Set(); return; }
  try {
    const d = await (await fetch("/v1/favorites")).json();
    state.favorites = new Set(d.favorites || []);
  } catch (e) { state.favorites = new Set(); }
}

async function doLogout() {
  await fetch("/v1/auth/logout", { method: "POST" });
  state.user = null;
  state.onlyFavorites = false;
  document.getElementById("onlyFavorites").checked = false;
  renderAuthArea();
  await loadFavorites();
  showWall();  // tillbaka till inloggnings-väggen
}

let authMode = "login";
function openAuth(mode = "login") {
  authMode = mode;
  document.getElementById("authError").classList.add("d-none");
  document.getElementById("authPass").value = "";
  document.getElementById("authTitle").textContent = mode === "login" ? "Logga in" : "Skapa konto";
  document.getElementById("authSubmit").textContent = mode === "login" ? "Logga in" : "Registrera";
  document.getElementById("authToggleText").textContent = mode === "login" ? "Inget konto?" : "Har du konto?";
  document.getElementById("authToggle").textContent = mode === "login" ? "Registrera dig" : "Logga in";
  document.getElementById("authModal").classList.remove("d-none");
  document.getElementById("authEmail").focus();
}
function closeAuth() {
  if (state.user) document.getElementById("authModal").classList.add("d-none");  // väggen går ej att stänga utan inloggning
}

// Hela appen kräver inloggning: authModal används som icke-stängbar vägg när utloggad.
function showWall() {
  document.getElementById("authClose").classList.add("d-none");
  openAuth("login");
}
function hideWall() {
  document.getElementById("authClose").classList.remove("d-none");
  document.getElementById("authModal").classList.add("d-none");
}

async function submitAuth() {
  const errEl = document.getElementById("authError");
  errEl.classList.add("d-none");
  const r = await fetch(`/v1/auth/${authMode}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: document.getElementById("authEmail").value.trim(), password: document.getElementById("authPass").value }),
  });
  if (!r.ok) { errEl.textContent = (await r.json()).detail || "Något gick fel."; errEl.classList.remove("d-none"); return; }
  state.user = await r.json();
  hideWall();
  renderAuthArea();
  await loadFavorites();
  await loadCategories();
  await loadChains();   // data laddas först efter inloggning (endpoints är gatade)
  await loadStores();
  render();
  populateBrowseChain();
  applyBrowseHash();    // återställ bläddra-vyn om URL:en har #sortiment
}

// ---- Kontoinställningar (utbyggbar; rymmer just nu lösenordsbyte) ----
function openSettings() {
  document.getElementById("setEmail").textContent = state.user.email;
  document.getElementById("setCur").value = "";
  document.getElementById("setNew").value = "";
  document.getElementById("setMsg").classList.add("d-none");
  document.getElementById("settingsModal").classList.remove("d-none");
}
function closeSettings() { document.getElementById("settingsModal").classList.add("d-none"); }

async function saveSettingsPassword() {
  const msg = document.getElementById("setMsg");
  const show = (txt, ok) => { msg.textContent = txt; msg.className = "small mb-2 " + (ok ? "text-success" : "text-danger"); };
  const r = await fetch("/v1/auth/password", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: document.getElementById("setCur").value, new_password: document.getElementById("setNew").value }),
  });
  if (!r.ok) { show((await r.json()).detail || "Något gick fel.", false); return; }
  document.getElementById("setCur").value = "";
  document.getElementById("setNew").value = "";
  show("Lösenordet är bytt.", true);
}

document.getElementById("authClose").addEventListener("click", closeAuth);
document.getElementById("authModal").addEventListener("click", (e) => { if (e.target.id === "authModal") closeAuth(); });
document.getElementById("authSubmit").addEventListener("click", submitAuth);
document.getElementById("authPass").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });
document.getElementById("authToggle").addEventListener("click", (e) => { e.preventDefault(); openAuth(authMode === "login" ? "register" : "login"); });

document.getElementById("setClose").addEventListener("click", closeSettings);
document.getElementById("settingsModal").addEventListener("click", (e) => { if (e.target.id === "settingsModal") closeSettings(); });
document.getElementById("setSave").addEventListener("click", saveSettingsPassword);
document.getElementById("setNew").addEventListener("keydown", (e) => { if (e.key === "Enter") saveSettingsPassword(); });

(async function init() {
  await loadUser();
  if (state.user) {
    await loadFavorites();
    await loadCategories();
    await loadChains();
    await loadStores();
    populateBrowseChain();
    applyBrowseHash();   // återställ bläddra-vyn om URL:en har #sortiment
  } else {
    showWall();  // hela appen kräver inloggning
  }
})();
