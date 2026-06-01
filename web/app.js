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
  favorites: new Set(JSON.parse(localStorage.getItem("favorites") || "[]")),
};

const COMPARE_CHAINS = ["ica", "coop", "willys", "hemkop"];

function favKey(s) { return `${s.chain}:${s.store_id}`; }
function isFav(s) { return state.favorites.has(favKey(s)); }
function toggleFav(s) {
  const k = favKey(s);
  if (state.favorites.has(k)) state.favorites.delete(k);
  else state.favorites.add(k);
  localStorage.setItem("favorites", JSON.stringify([...state.favorites]));
  // Full render bara om synligheten ändras (favoritfiltret på); annars räcker listan.
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
  const offersBtn = ["ica", "willys", "hemkop", "coop"].includes(s.chain)
    ? `<button class="pop-offers-btn" data-chain="${esc(s.chain)}" data-id="${esc(s.store_id)}" data-name="${esc(s.name)}">Visa veckans erbjudanden</button>`
    : "";
  return `<div class="store-pop">
    <button class="pop-fav${isFav(s) ? " on" : ""}" aria-label="Favorit">&#9733;</button>
    <span class="pop-brand" style="background:${color}">${esc(brand)}</span>
    <h6>${esc(s.name)}</h6>
    <div class="pop-addr">${esc(addr)}</div>
    ${oh.today ? `<div class="pop-hours">Idag: ${esc(oh.today)}</div>` : ""}
    ${tags ? `<div class="pop-tags">${tags}</div>` : ""}
    ${offersBtn}
    ${linkHtml ? `<div class="pop-links">${linkHtml}</div>` : ""}
  </div>`;
}

function visibleStores() {
  const q = state.query.toLowerCase();
  return state.stores.filter((s) => {
    if (!state.enabled.has(s.chain)) return false;
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

function updateSyncBadge(st) {
  const badge = document.getElementById("syncBadge");
  if (st.running) {
    const done = Object.values(st.chains).filter((c) => c.status === "ok" || c.status === "error").length;
    badge.className = "sync-badge running";
    badge.textContent = `● synkar… (${done}/5 kedjor klara)`;
    return true;
  }
  const errors = Object.values(st.chains).filter((c) => c.status === "error");
  badge.className = "sync-badge " + (errors.length ? "error" : "ok");
  badge.textContent = errors.length
    ? `● synk klar, ${errors.length} fel`
    : `● synk klar`;
  return false;
}

async function pollSync() {
  const r = await fetch("/v1/sync/status");
  const st = await r.json();
  const running = updateSyncBadge(st);
  await loadChains();
  await loadStores();
  if (running) setTimeout(pollSync, 2500);
}

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
document.getElementById("syncBtn").addEventListener("click", async () => {
  await fetch("/v1/sync", { method: "POST" });
  pollSync();
});

// ---- Erbjudanden ----
let currentOffers = [];

function offerCard(o) {
  const cmp = o.comparison_value
    ? `<span class="o-cmp">${o.comparison_value}/${esc(o.comparison_unit || "")}</span>`
    : "";
  const img = o.image
    ? `<img class="o-img" src="${esc(o.image)}" loading="lazy" alt="">`
    : `<div class="o-img o-img--ph"></div>`;
  const valid = o.valid_to ? `t.o.m. ${esc(o.valid_to)}` : "";
  const member = o.member_price ? `<span class="o-member">Klubbpris</span>` : "";
  const sv = Math.round((o.savings || 0) * 100) / 100;
  const save = sv > 0 ? `<span class="o-save">spar ${sv} kr</span>` : "";
  const foot = [o.category_raw, valid].filter(Boolean).map(esc).join(" &middot; ");
  return `<div class="offer-card">
    ${img}
    <div class="o-body">
      <div class="o-name">${esc(o.name || "")}</div>
      <div class="o-meta">${esc(o.brand || "")}${o.package ? " &middot; " + esc(o.package) : ""}</div>
      <div class="o-price-row">
        <span class="o-price">${esc(o.price_text || "")}</span>
        ${member}
        ${cmp}
        ${save}
      </div>
      ${foot ? `<div class="o-foot">${foot}</div>` : ""}
    </div>
  </div>`;
}

function sortOffers(list, mode) {
  const arr = [...list];
  if (mode === "savings") arr.sort((a, b) => (b.savings || 0) - (a.savings || 0));
  else if (mode === "price") arr.sort((a, b) => (a.price ?? 1e9) - (b.price ?? 1e9));
  else if (mode === "name") arr.sort((a, b) => (a.name || "").localeCompare(b.name || "", "sv"));
  return arr;
}

function renderOffers(filterText) {
  const q = (filterText || "").toLowerCase();
  const mode = document.getElementById("offersSort").value;
  let list = currentOffers.filter((o) =>
    !q || `${o.name} ${o.brand} ${o.category_raw}`.toLowerCase().includes(q));
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
  const sub = [p.brand, p.category].filter(Boolean).map(esc).join(" &middot; ") + variantTag;
  const img = p.image
    ? `<img class="o-img" src="${esc(p.image)}" loading="lazy" alt="">`
    : `<div class="o-img o-img--ph"></div>`;
  const spreadLabel = p.compare_by === "unit_price"
    ? `${p.spread} kr/${esc(p.unit)}` : `${p.spread} kr`;
  const rows = p.offers.map((o, i) => {
    const meta = state.chains[o.chain] || {};
    const big = p.compare_by === "unit_price"
      ? (o.comparison_value != null ? `${o.comparison_value} kr/${esc(o.comparison_unit || "")}` : "–")
      : (o.price != null ? `${o.price} kr` : "–");
    const member = o.member_price ? `<span class="o-member">Klubbpris</span>` : "";
    return `<div class="cmp-row${i === 0 ? " cmp-best" : ""}">
      <span class="dot" style="background:${meta.color || "#666"}"></span>
      <span class="cmp-chain">${esc(meta.label || o.chain)}</span>
      <span class="cmp-big">${big}</span>${member}
      <div class="cmp-sub">${esc(o.price_text || "")} &middot; ${esc(o.store_name || "")}${o.distance_km != null ? " " + o.distance_km + "km" : ""}</div>
    </div>`;
  }).join("");
  return `<div class="offer-card cmp-card">
    <div class="cmp-top">
      ${img}
      <div class="cmp-id"><div class="o-name">${esc(p.name || "")}</div><div class="o-meta">${sub}</div></div>
      <span class="cmp-spread" title="prisskillnad">spar ${spreadLabel}</span>
    </div>
    <div class="cmp-rows">${rows}</div>
  </div>`;
}

function renderCompare(filterText) {
  const q = (filterText || "").toLowerCase();
  const list = currentCompare.filter((p) =>
    !q || `${p.name} ${p.brand} ${p.category}`.toLowerCase().includes(q));
  document.getElementById("compareList").innerHTML = list.length
    ? list.map(compareCard).join("")
    : `<div class="text-muted small p-2">Inga produkter på erbjudande hos flera kedjor här.</div>`;
}

async function showCompare() {
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
    renderCompare("");
  } catch (e) {
    document.getElementById("compareList").innerHTML =
      `<div class="text-danger small p-2">Kunde inte hämta jämförelsen.</div>`;
  }
}

document.getElementById("compareBtn").addEventListener("click", showCompare);
document.getElementById("compareFavBtn").addEventListener("click", showCompareFavorites);
document.getElementById("compareBack").addEventListener("click", () => {
  document.getElementById("comparePanel").classList.add("d-none");
});
document.getElementById("compareFilter").addEventListener("input", (e) => {
  renderCompare(e.target.value.trim());
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

(async function init() {
  await loadChains();
  await loadStores();
  pollSync();
})();
