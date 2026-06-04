    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const CHAIN_COLOR = { ica:"#e3000b", coop:"#00a651", willys:"#b71c1c", hemkop:"#f57c00", lidl:"#0050aa", citygross:"#6a3d9a", egen:"#3b4a5a", other:"#777" };
    const CHAIN_LABELS = { ica:"ICA", coop:"Coop", willys:"Willys", hemkop:"Hemköp", lidl:"Lidl", citygross:"City Gross", egen:"Egen", other:"Övrigt" };
    const chip = (c) => `<span class="badge badge-chain" style="background:${CHAIN_COLOR[c]||'#777'}">${esc(CHAIN_LABELS[c]||c)}</span>`;
    const ago = (ts) => { const s = Math.round(Date.now()/1000 - ts); return s<60?`${s}s`:s<3600?`${Math.round(s/60)}m`:`${Math.round(s/3600)}h`; };

    const gate = document.getElementById("loginGate");
    const consoleEl = document.getElementById("console");
    let active = "overview", callsTimer = null, syncTimer = null, catalogTimer = null, sweepTimer = null;
    let callsData = null, callsFilter = { source: "", status: "" };

    // Alla konsol-anrop går via api(): 403 => sessionen är borta, visa login.
    async function api(url, opts) {
      const r = await fetch(url, opts);
      if (r.status === 403) { showGate(); throw new Error("403"); }
      return r;
    }

    async function loadOverview() {
      const d = await (await api("/v1/admin/overview")).json();
      const storeTot = d.chains.reduce((a, c) => a + (c.store_count || 0), 0);
      const sw = d.offers_sweep || {};  // bara nästa-körning-kortet kvar i översikten; resten i Erbjudanden-fliken
      // Alla schemalagda jobb -> ett kort, soonest överst (next_run = "YYYY-MM-DD HH:MM", strängsortbart).
      const jobs = [
        { name: "Butikssynk", next: (d.scheduler || {}).next_run, cron: (d.scheduler || {}).cron },
        { name: "Erbjudande-sweep", next: sw.next_run, cron: sw.cron },
        { name: "Sortiment-crawl", next: (d.catalog_crawl || {}).next_run, cron: (d.catalog_crawl || {}).cron },
      ].filter(j => j.next).sort((a, b) => a.next.localeCompare(b.next));
      const catTot = Object.values(d.catalog || {}).reduce((a, s) => a + (s.total || 0), 0);
      const catAvail = Object.values(d.catalog || {}).reduce((a, s) => a + (s.available || 0), 0);
      const nChains = d.chains.filter(c => c.store_count).length;
      // Per kedja: butiker + crawlat sortiment (listat, inte inline). Kedjeordning = config.CHAINS.
      const perChainRows = d.chains.map(c => {
        const cat = (d.catalog || {})[c.chain] || {};
        return `<tr><td>${chip(c.chain)}</td><td>${c.store_count || 0}</td>
          <td>${cat.total || 0}</td><td class="text-muted">${cat.eans || 0}</td></tr>`;
      }).join("");
      document.getElementById("overview").innerHTML = `
        <h5 class="mb-3">Översikt</h5>
        <div class="row g-3 mb-3 stats-row">
          <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted small">Butiker</div><div class="stat">${storeTot}</div><div class="small text-muted">${nChains} kedjor</div></div></div>
          <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted small">Erbjudanden cachade</div><div class="stat">${d.offers.rows}</div><div class="small text-muted">${d.offers.stores_cached} butiker</div></div></div>
          <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted small">Sortimentprodukter (crawlade)</div><div class="stat">${catTot}</div><div class="small text-muted">${catAvail} tillgängliga</div></div></div>
          <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted small">Distinkta EAN</div><div class="stat">${d.ean_stats.distinct}</div><div class="small text-muted">${d.ean_stats.with_info} med produktinfo · ${d.ean_stats.axfood_cache} Axfood-resolvade</div></div></div>
          <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted small">Prishistorik (observationer)</div><div class="stat">${d.price_history.rows}</div><div class="small text-muted">${d.price_history.products} produkter${d.price_history.since ? ` sedan ${esc((d.price_history.since || "").slice(0, 10))}` : ""}</div></div></div>
          <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted small">Lagring på disk</div><div class="stat">${fmtBytes((d.storage || {}).total_bytes || 0)}</div><div class="small text-muted">DB ${fmtBytes((d.storage || {}).db_bytes || 0)} · bilder ${fmtBytes((d.storage || {}).image_bytes || 0)} (${(d.storage || {}).image_count || 0} st)</div></div></div>
          <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted small">Nästa schemalagda körning</div>
            ${jobs.length
              ? `<div class="fw-bold mt-1">${esc(jobs[0].next)}</div><div class="small text-muted">${esc(jobs[0].name)}</div>`
                + (jobs.length > 1 ? `<div class="small text-muted mt-1" style="line-height:1.5">${jobs.slice(1).map(j => `${esc(j.name)}: ${esc(j.next)}`).join("<br>")}</div>` : "")
              : `<div class="fw-bold mt-1">-</div><div class="small text-muted">inga schemalagda körningar</div>`}
          </div></div>
        </div>
        <div class="card p-3"><h6 class="mb-2">Per kedja</h6>
          <table class="table table-sm align-middle mb-0">
            <thead><tr><th>Kedja</th><th>Butiker</th><th>Sortiment (crawlat)</th><th>Distinkta EAN</th></tr></thead>
            <tbody>${perChainRows}</tbody>
            <tfoot><tr class="fw-semibold"><td>Totalt</td><td>${storeTot}</td><td>${catTot}</td><td class="text-muted">-</td></tr></tfoot>
          </table></div>`;
    }

    async function loadKedjor() {
      const d = await (await api("/v1/admin/overview")).json();
      const rows = d.chains.map(c => `<tr>
        <td>${chip(c.chain)}</td><td>${c.store_count}</td>
        <td class="st-${c.status}">${esc(c.status)}</td>
        <td class="mono">${esc(fmtTs(c.last_sync))}</td>
        <td class="text-danger small">${esc(c.error || "")}</td></tr>`).join("");
      const done = d.chains.filter(c => c.status === "ok" || c.status === "error").length;
      const syncState = d.syncing
        ? `<span class="st-running">● synkar… (${done}/${d.chains.length} klara)</span>`
        : `<span class="text-muted">senast: ${esc(fmtTs(d.chains.map(c=>c.last_sync).filter(Boolean).sort().pop()))}</span>`;
      document.getElementById("kedjor").innerHTML = `
        <div class="d-flex align-items-center mb-3">
          <h5 class="mb-0">Kedjor</h5>
          <span class="ms-3 small">${syncState}</span>
          <button id="syncNow" class="btn btn-sm btn-dark ms-auto" ${d.syncing ? "disabled" : ""}>Synka om</button>
        </div>
        <div class="text-muted small mb-2">Synkar butiksbeståndet (steg 1) för alla kedjor. Rör inte erbjudanden eller sortiment.</div>
        <div class="card p-3">
          <table class="table table-sm align-middle mb-0">
            <thead><tr><th>Kedja</th><th>Butiker</th><th>Synkstatus</th><th>Senast synk</th><th>Fel</th></tr></thead>
            <tbody>${rows}</tbody></table></div>`;
      document.getElementById("syncNow").addEventListener("click", triggerSync);
      clearTimeout(syncTimer);
      if (d.syncing && active === "kedjor") syncTimer = setTimeout(loadKedjor, 2500);
    }

    async function loadSweep() {
      const d = await (await api("/v1/admin/overview")).json();
      const sw = d.offers_sweep || { chains: {}, running: false };
      const swCov = sw.coverage || {}, swStores = sw.store_counts || {};
      const swList = sw.supported_chains || Object.keys(sw.chains || {});
      const swErrLines = [];
      const swRows = swList.map((c) => {
        const s = (sw.chains || {})[c] || { fetched: 0, skipped: 0, errors: 0, status: "idle", last_errors: [] };
        const cov = swCov[c] || { stores_with_offers: 0, offers: 0 };
        const tot = swStores[c] || 0;
        const pct = tot ? Math.round((cov.stores_with_offers / tot) * 100) : 0;
        const errs = s.last_errors || [];
        if (errs.length) swErrLines.push(`<div><strong>${esc(c)}:</strong> ${errs.map(esc).join("; ")}${s.errors > errs.length ? ` … (+${s.errors - errs.length})` : ""}</div>`);
        const errCell = s.errors
          ? `<span class="text-danger" title="${esc(errs.join("\n"))}">${s.errors}</span>`
          : "0";
        return `<tr>
          <td>${chip(c)}</td><td>${tot}</td>
          <td>${cov.stores_with_offers} <span class="text-muted">(${pct}%)</span></td>
          <td>${cov.offers}</td>
          <td>${s.fetched}</td><td>${s.skipped}</td>
          <td>${errCell}</td>
          <td class="st-${s.status === "tripped" ? "error" : s.status}">${esc(s.status)}</td></tr>`;
      }).join("");
      const swTotals = Object.values(sw.chains || {}).reduce((a, s) => ({
        fetched: a.fetched + s.fetched, skipped: a.skipped + s.skipped, errors: a.errors + s.errors,
      }), { fetched: 0, skipped: 0, errors: 0 });
      const swState = sw.running
        ? `<span class="st-running">● hämtar erbjudanden… (${swTotals.fetched} hämtade, ${swTotals.skipped} hoppade${swTotals.errors ? `, ${swTotals.errors} fel` : ""})</span>`
        : `<span class="text-muted">senast: ${esc(fmtTs(sw.finished_at))}</span>`;
      document.getElementById("sweep").innerHTML = `
        <div class="d-flex align-items-center mb-3">
          <h5 class="mb-0">Erbjudanden</h5>
          <span class="ms-3 small">${swState}</span>
          <div class="form-check form-check-inline ms-auto mb-0"><input class="form-check-input" type="checkbox" id="sweepForce"><label class="form-check-label small" for="sweepForce">Tvinga om allt</label></div>
          <button id="sweepNow" class="btn btn-sm btn-dark" ${sw.running ? "disabled" : ""}>Hämta alla erbjudanden</button>
        </div>
        <div class="row g-3 mb-3 stats-row">
          <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted small">Erbjudanden cachade</div><div class="stat">${d.offers.rows}</div><div class="small text-muted">${d.offers.stores_cached} butiker</div></div></div>
          <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted small">Nästa erbjudande-sweep</div><div class="fw-bold mt-1">${esc(sw.next_run || "-")}</div><div class="small mono text-muted">${esc(sw.cron || "")}</div></div></div>
        </div>
        <div class="card p-3">
          <div class="text-muted small mb-2">Förhämtar erbjudanden för alla butiker (hoppar färska om inte "tvinga"). Rate-limitat - första körningen tar några minuter. "Med erbjudanden" = nuvarande täckning i cachen; resten är senaste sweep-körningen.</div>
          <table class="table table-sm align-middle mb-0">
            <thead><tr><th>Kedja</th><th>Butiker</th><th>Med erbjudanden</th><th>Erbjudanden</th><th>Hämtade</th><th>Hoppade</th><th>Fel</th><th>Status</th></tr></thead>
            <tbody>${swRows}</tbody></table>
          ${swErrLines.length ? `<div class="small text-muted mt-2"><div class="fw-bold">Fel (senaste sweep):</div>${swErrLines.join("")}</div>` : ""}</div>`;
      document.getElementById("sweepNow").addEventListener("click", triggerSweep);
      clearTimeout(sweepTimer);
      if (sw.running && active === "sweep") sweepTimer = setTimeout(loadSweep, 2500);
    }

    async function triggerSync() {
      await api("/v1/sync", { method: "POST" });
      loadKedjor();
    }

    async function triggerSweep() {
      const force = document.getElementById("sweepForce").checked;
      await api(`/v1/offers/sweep?force=${force}`, { method: "POST" });
      loadSweep();
    }

    async function loadCalls() {
      const opts = ["", "egen", "ica", "coop", "willys", "hemkop", "lidl", "citygross", "other"]
        .map(c => `<option value="${c}">${c ? (CHAIN_LABELS[c] || c) : "Alla källor"}</option>`).join("");
      document.getElementById("calls").innerHTML = `
        <div class="d-flex align-items-center gap-2 mb-2">
          <h6 class="mb-0">API-anrop</h6>
          <select id="callsSource" class="form-select form-select-sm ms-auto" style="width:auto">${opts}</select>
          <select id="callsStatus" class="form-select form-select-sm" style="width:auto">
            <option value="">Alla status</option><option value="ok">OK (&lt;400)</option><option value="err">Fel (&ge;400)</option>
          </select>
          <span class="small text-muted">var 5:e s</span>
        </div>
        <div id="callsStats" class="mb-3"></div>
        <h6>Senaste anrop</h6>
        <div id="callsRecent"></div>`;
      const cs = document.getElementById("callsSource"), cst = document.getElementById("callsStatus");
      cs.value = callsFilter.source; cst.value = callsFilter.status;
      cs.addEventListener("change", () => { callsFilter.source = cs.value; renderCallsData(); });
      cst.addEventListener("change", () => { callsFilter.status = cst.value; renderCallsData(); });
      await refreshCalls();
    }

    async function refreshCalls() {
      callsData = await (await api("/v1/admin/calls")).json();
      renderCallsData();
    }

    function renderCallsData() {
      if (!callsData || !document.getElementById("callsStats")) return;
      let stats = callsData.stats, recent = callsData.recent;
      if (callsFilter.source) {
        stats = stats.filter(s => s.chain === callsFilter.source);
        recent = recent.filter(c => c.chain === callsFilter.source);
      }
      if (callsFilter.status) {
        const err = callsFilter.status === "err";
        recent = recent.filter(c => (c.status >= 400) === err);
      }
      document.getElementById("callsStats").innerHTML = `
        <div class="card p-2"><table class="table table-sm mb-0">
          <thead><tr><th>Källa</th><th>Host</th><th>Anrop</th><th>Fel</th><th>Snitt</th></tr></thead>
          <tbody>${stats.map(s => `<tr><td>${chip(s.chain)}</td><td class="mono">${esc(s.host)}</td><td>${s.count}</td>
            <td class="${s.errors ? "call-err" : ""}">${s.errors}</td><td>${s.avg_ms ?? "-"} ms</td></tr>`).join("")
            || '<tr><td colspan="5" class="text-muted">Inga anrop.</td></tr>'}</tbody></table></div>`;
      document.getElementById("callsRecent").innerHTML = `
        <div class="card p-2"><table class="table table-sm table-hover mb-0">
          <thead><tr><th>Tid</th><th>Källa</th><th>Metod</th><th>Status</th><th>ms</th><th>URL</th></tr></thead>
          <tbody>${recent.map(c => `<tr><td class="text-muted">${ago(c.ts)}</td><td>${chip(c.chain)}</td>
            <td>${esc(c.method)}</td><td class="${c.status >= 400 ? "call-err" : ""}">${c.status}</td>
            <td>${c.ms ?? "-"}</td><td class="mono text-truncate" style="max-width:340px">${esc(c.chain === "egen" ? "" : c.host)}${esc(c.path)}</td></tr>`).join("")
            || '<tr><td colspan="6" class="text-muted">Inga anrop.</td></tr>'}</tbody></table></div>`;
    }

    // Foldbar JSON-trädvy: objekt/arrayer blir <details> som kan fällas in/ut.
    function jtVal(v, key) {
      const k = key !== undefined ? `<span class="j-key">${esc(key)}</span><span class="j-punc">: </span>` : "";
      if (v === null) return `<div class="jt-leaf">${k}<span class="j-null">null</span></div>`;
      if (Array.isArray(v)) {
        if (!v.length) return `<div class="jt-leaf">${k}<span class="j-punc">[ ]</span></div>`;
        return `<details class="jt" open><summary>${k}<span class="j-punc">[</span><span class="jt-n">${v.length}</span><span class="j-punc">]</span></summary>`
          + `<div class="jt-kids">${v.map(it => jtVal(it)).join("")}</div></details>`;
      }
      if (typeof v === "object") {
        const ks = Object.keys(v);
        if (!ks.length) return `<div class="jt-leaf">${k}<span class="j-punc">{ }</span></div>`;
        return `<details class="jt" open><summary>${k}<span class="j-punc">{</span><span class="jt-n">${ks.length}</span><span class="j-punc">}</span></summary>`
          + `<div class="jt-kids">${ks.map(kk => jtVal(v[kk], kk)).join("")}</div></details>`;
      }
      let c = "j-num", disp = String(v);
      if (typeof v === "string") { c = "j-str"; disp = `"${v}"`; }
      else if (typeof v === "boolean") c = "j-bool";
      return `<div class="jt-leaf">${k}<span class="${c}">${esc(disp)}</span></div>`;
    }

    function showResult(header, data, isJson) {
      const out = document.getElementById("apiOut");
      const meta = `<div class="j-meta">${esc(header)}</div>`;
      if (isJson && data !== null && typeof data === "object") {
        out.innerHTML = meta + `<div class="jt-root">${jtVal(data)}</div>`;
      } else {
        out.innerHTML = meta + `<div class="jt-text">${esc(typeof data === "string" ? data : JSON.stringify(data, null, 2))}</div>`;
      }
    }

    async function runApiTest() {
      const path = document.getElementById("apiPath").value.trim();
      const out = document.getElementById("apiOut");
      if (!path) return;
      out.textContent = "Kör…";
      try {
        const t0 = performance.now();
        const r = await fetch(path);
        const ms = Math.round(performance.now() - t0);
        const ct = r.headers.get("content-type") || "";
        if (ct.startsWith("image/")) {
          const blob = await r.blob();
          out.innerHTML = `<span class="j-meta">${r.status} ${r.statusText}  ·  ${ms} ms  ·  ${esc(ct)}  ·  ${Math.round(blob.size / 1024)} KB</span>\n<img src="${URL.createObjectURL(blob)}" style="max-height:240px;margin-top:8px;background:#fff;border-radius:6px">`;
          return;
        }
        const isJson = ct.includes("application/json");
        const data = isJson ? await r.json() : await r.text();
        showResult(`${r.status} ${r.statusText}  ·  ${ms} ms`, data, isJson);
      } catch (e) { out.textContent = "Fel: " + e; }
    }

    async function runProxyTest(url, authKind, method, reqBody) {
      const out = document.getElementById("apiOut");
      out.textContent = "Kör upstream…";
      try {
        const t0 = performance.now();
        const r = await fetch("/v1/admin/proxy", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url, auth_kind: authKind || "none", method: method || "GET", body: reqBody || null }),
        });
        const ms = Math.round(performance.now() - t0);
        const d = await r.json();
        let data = d.body, isJson = true;
        if (typeof d.body === "string") {
          try { data = JSON.parse(d.body); } catch { data = d.body; isJson = false; }
        } else if (d.body == null) { data = d; }
        showResult(`${method || "GET"} ${url}${reqBody ? "\nbody: " + reqBody : ""}\n[auth: ${authKind || "none"}]  ·  upstream ${d.status ?? r.status}  ·  ${ms} ms`, data, isJson);
      } catch (e) { out.textContent = "Fel: " + e; }
    }

    async function loadSources() {
      const d = await (await api("/v1/admin/sources")).json();
      const srcRows = d.sources.map(s => `<tr>
        <td>${chip(s.chain)}</td><td>${esc(s.what)}</td>
        <td class="mono">${esc(s.url)}</td><td class="small">${esc(s.auth)}</td>
        <td>${s.example ? `<button class="btn btn-sm btn-outline-dark src-test" data-url="${esc(s.example)}" data-auth="${esc(s.auth_kind || "none")}" data-method="${esc(s.method || "GET")}" data-body="${esc(s.body || "")}">Testa</button>` : '<span class="text-muted small">-</span>'}</td></tr>`).join("");
      const own = d.own_apis || [];
      const fieldRows = (arr) => (arr || []).map(x =>
        `<tr><td class="mono small" style="white-space:nowrap">${esc(x.name || x.field)}</td><td class="small">${esc(x.desc)}</td></tr>`).join("");
      const ownCards = own.map(a => `
        <details class="api-ep">
          <summary>
            <span class="badge bg-secondary">${esc(a.group)}</span>
            <span class="mono small">${esc(a.method)} ${esc(a.path)}</span>
            <button class="btn btn-sm btn-outline-dark own-test ms-auto" data-path="${esc(a.path)}">Testa</button>
          </summary>
          <div class="small text-muted mt-2">${esc(a.desc)}</div>
          ${(a.params && a.params.length) ? `<div class="ep-h">Parametrar</div><table class="table table-sm mb-1"><tbody>${fieldRows(a.params)}</tbody></table>` : ""}
          ${(a.returns && a.returns.length) ? `<div class="ep-h">Returnerar</div><table class="table table-sm mb-0"><tbody>${fieldRows(a.returns)}</tbody></table>` : ""}
        </details>`).join("");
      document.getElementById("sources").innerHTML = `
        <div class="card p-3 mb-3">
          <h6>Egna API:er</h6>
          <div class="small text-muted mb-2">Vårt eget <span class="mono">/v1</span>-API. Fäll ut en endpoint för parametrar och returnerade fält. "Testa" kör den (din konsol-session följer med) och visar svaret nedan. Du kan också skriva en egen sökväg.</div>
          ${ownCards}
          <div class="input-group input-group-sm mb-2 mt-3">
            <input id="apiPath" class="form-control mono" placeholder="/v1/products/{ean}" value="${esc((own[0] || {}).path || "/v1/chains")}">
            <button id="apiRun" class="btn btn-dark">Kör</button>
          </div>
          <div id="apiOut" class="mono p-2 mb-0" style="background:#0f1419;color:#d7dde3;border-radius:7px;max-height:46vh;overflow:auto;word-break:break-word">Resultat visas här.</div>
        </div>
        <div class="card p-3"><h6>Datakällor per kedja</h6>
        <div class="small text-muted mb-2">Kedjornas upstream-API:er som synken läser. "Testa" kör via vår proxy (nyckel/token läggs på server-side). Resultatet visas i rutan ovan.</div>
        <table class="table table-sm mb-0">
          <thead><tr><th>Kedja</th><th>Vad</th><th>Endpoint</th><th>Auth</th><th>Test</th></tr></thead>
          <tbody>${srcRows}</tbody></table></div>`;
      document.getElementById("apiRun").addEventListener("click", runApiTest);
      document.getElementById("apiPath").addEventListener("keydown", (e) => { if (e.key === "Enter") runApiTest(); });
      document.querySelectorAll(".own-test").forEach(b => b.addEventListener("click", (e) => {
        e.preventDefault();  // hindra att <summary>-klicket fäller ut/in
        document.getElementById("apiPath").value = b.dataset.path;
        document.getElementById("apiOut").scrollIntoView({ block: "nearest" });
        runApiTest();
      }));
      document.querySelectorAll(".src-test").forEach(b => b.addEventListener("click", () => {
        document.getElementById("apiOut").scrollIntoView({ block: "nearest" });
        runProxyTest(b.dataset.url, b.dataset.auth, b.dataset.method, b.dataset.body);
      }));
    }

    async function loadTypes() {
      const d = await (await api("/v1/tags/types")).json();
      const builtin = new Set(d.builtin);
      const chips = d.types.map(t => {
        const title = builtin.has(t) ? "ta bort (inbyggd: seedas till 'other' om den ändå produceras)" : "ta bort";
        const x = `<span class="type-del" data-type="${t}" title="${title}">&times;</span>`;
        return `<span class="typechip${builtin.has(t) ? " bi" : ""}">${esc(t)}${x}</span>`;
      }).join(" ");
      const provs = await (await api("/v1/providers")).json();
      const provChips = (provs.providers || []).map(p =>
        `<span class="typechip">${esc(p)}<span class="prov-vdel" data-name="${p}" title="ta bort">&times;</span></span>`).join(" ");
      document.getElementById("typesPanel").innerHTML = `
        <div class="d-flex align-items-center mb-1"><h6 class="mb-0">Kanoniska typer</h6>
          <span class="ms-2 small text-muted">inbyggda (grå) kan tas bort - en seedad typ utan vokabulär-post faller till 'other'</span></div>
        <div class="mb-2">${chips}</div>
        <div class="input-group input-group-sm mb-3" style="max-width:320px">
          <input id="newType" class="form-control" placeholder="ny typ (a-z, _)">
          <button id="addType" class="btn btn-dark">Lägg till</button>
        </div>
        <div class="d-flex align-items-center mb-1"><h6 class="mb-0">Speditörer</h6>
          <span class="ms-2 small text-muted">för paket-/post-taggar - sätt per råetikett i tabellen nedan (kolumnen "Kanoniska typer")</span></div>
        <div class="mb-2">${provChips || '<span class="text-muted small">Inga speditörer.</span>'}</div>
        <div class="input-group input-group-sm" style="max-width:320px">
          <input id="newProv" class="form-control" placeholder="ny speditör (t.ex. Airmee)">
          <button id="addProv" class="btn btn-dark">Lägg till</button>
        </div>`;
      document.querySelectorAll(".type-del").forEach(x => x.addEventListener("click", async (e) => {
        e.stopPropagation();
        const r = await api("/v1/tags/types/" + encodeURIComponent(x.dataset.type), { method: "DELETE" });
        if (!r.ok) alert((await r.json()).detail || "Kunde inte ta bort.");
        loadTags();
      }));
      document.getElementById("addType").addEventListener("click", async () => {
        const v = document.getElementById("newType").value.trim();
        if (!v) return;
        await api("/v1/tags/types", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: v }) });
        loadTags();
      });
      document.querySelectorAll(".prov-vdel").forEach(x => x.addEventListener("click", async (e) => {
        e.stopPropagation();
        const r = await api("/v1/providers/" + encodeURIComponent(x.dataset.name), { method: "DELETE" });
        if (!r.ok) alert((await r.json()).detail || "Kunde inte ta bort.");
        loadTags();
      }));
      document.getElementById("addProv").addEventListener("click", async () => {
        const v = document.getElementById("newProv").value.trim();
        if (!v) return;
        await api("/v1/providers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: v }) });
        loadTags();
      });
    }

    let tagState = { tags: [], types: [], providers: [] };

    function provControl(t) {
      const shipping = t.types.some(x => x === "parcel" || x === "postal") || t.provider;
      if (!shipping) return "";
      const autoTxt = (t.provider && !t.provider_overridden) ? `(auto: ${esc(t.provider)})` : "(auto)";
      const opts = `<option value="">${autoTxt}</option>` + tagState.providers.map(p =>
        `<option value="${esc(p)}"${t.provider_overridden && t.provider === p ? " selected" : ""}>${esc(p)}</option>`).join("");
      return `<select class="form-select form-select-sm prov-sel mt-1" style="width:auto;display:inline-block" title="Speditör">${opts}</select>`;
    }

    function tagRow(t) {
      const attn = (!t.overridden && t.types.length === 1 && t.types[0] === "other") ? "attn" : "";
      const chainsHtml = t.chains.map(chip).join(" ");
      const prov = t.provider ? `<span class="badge bg-secondary ms-1 prov-badge">${esc(t.provider)}</span>` : "";
      const chips = tagState.types.map(ty =>
        `<span class="tchip${t.types.includes(ty) ? " on" : ""}" data-type="${ty}">${ty}</span>`).join("");
      return `<tr class="${attn}" data-label="${esc(t.label)}">
        <td>${esc(t.label)}${prov}</td>
        <td>${chainsHtml}</td>
        <td>${t.count}</td>
        <td><div class="toggles">${chips}</div>
          <button class="btn btn-sm btn-link p-0 auto-btn">↺ auto</button>
          <span class="small text-muted ms-2 tag-mode">${t.overridden ? "manuell" : "auto"}</span>
          ${provControl(t)}</td></tr>`;
    }

    function refreshTagRow(tr, t) {  // uppdatera rad in-place utan omsortering
      tr.querySelectorAll(".tchip").forEach(ch => ch.classList.toggle("on", t.types.includes(ch.dataset.type)));
      tr.classList.toggle("attn", !t.overridden && t.types.length === 1 && t.types[0] === "other");
      const mode = tr.querySelector(".tag-mode");
      if (mode) mode.textContent = t.overridden ? "manuell" : "auto";
    }

    function bindTagRows() {
      document.querySelectorAll("#tagBody .tchip").forEach(c => {
        c.addEventListener("click", async () => {
          const tr = c.closest("tr"), label = tr.dataset.label;
          c.classList.toggle("on");
          const types = [...tr.querySelectorAll(".tchip.on")].map(x => x.dataset.type);
          if (!types.length) { c.classList.add("on"); return; }  // minst en typ
          const r = await api("/v1/tags/map", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label, types }) });
          const d = await r.json();
          const t = tagState.tags.find(x => x.label === label);
          if (t) { t.types = d.types || types; t.overridden = true; refreshTagRow(tr, t); }
        });
      });
      document.querySelectorAll("#tagBody .auto-btn").forEach(b => {
        b.addEventListener("click", async () => {
          const tr = b.closest("tr"), label = tr.dataset.label;
          const r = await api("/v1/tags/map/" + encodeURIComponent(label), { method: "DELETE" });
          const d = await r.json();
          const t = tagState.tags.find(x => x.label === label);
          if (t) { t.types = d.types || []; t.overridden = false; refreshTagRow(tr, t); }
        });
      });
      document.querySelectorAll("#tagBody .prov-sel").forEach(s => {
        s.addEventListener("change", async () => {
          const tr = s.closest("tr"), label = tr.dataset.label, val = s.value;
          const r = val
            ? await api("/v1/tags/provider", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label, provider: val }) })
            : await api("/v1/tags/provider/" + encodeURIComponent(label), { method: "DELETE" });
          const d = await r.json();
          const t = tagState.tags.find(x => x.label === label);
          if (t) {
            t.provider = d.provider || null; t.provider_overridden = !!val;
            let badge = tr.querySelector(".prov-badge");
            if (t.provider) {
              if (badge) badge.textContent = t.provider;
              else tr.querySelector("td:first-child").insertAdjacentHTML("beforeend", `<span class="badge bg-secondary ms-1 prov-badge">${esc(t.provider)}</span>`);
            } else if (badge) badge.remove();
          }
        });
      });
    }

    function renderTagRows() {
      const q = (document.getElementById("tagSearch").value || "").trim().toLowerCase();
      const rows = tagState.tags.filter(t =>
        !q || (t.label || "").toLowerCase().includes(q) || t.chains.some(c => c.includes(q)));
      document.getElementById("tagBody").innerHTML = rows.length
        ? rows.map(tagRow).join("")
        : `<tr><td colspan="4" class="text-muted small">Inga råetiketter matchar.</td></tr>`;
      bindTagRows();
    }

    async function loadTags() {
      document.getElementById("tags").innerHTML =
        `<div id="typesPanel" class="card p-3 mb-3"></div><div id="tagTable" class="card p-3"></div>`;
      await loadTypes();
      const d = await (await api("/v1/tags")).json();
      tagState.tags = d.tags;
      tagState.types = d.types;
      tagState.providers = d.providers || [];
      document.getElementById("tagTable").innerHTML = `
        <div class="d-flex align-items-center mb-2"><h6 class="mb-0">Tagg-normalisering</h6>
          <input id="tagSearch" class="form-control form-control-sm ms-auto" style="max-width:240px" placeholder="Sök råetikett…"></div>
        <div class="small text-muted mb-2">Klicka typ-chip för att (av)markera. En tagg kan ha flera typer
          (t.ex. "Posten Brev &amp; paket" = postal + parcel). Gulmarkerade behöver mappas. "↺ auto" återställer.
          Listan laddar inte om vid klick.</div>
        <table class="table table-sm align-middle mb-0">
          <thead><tr><th>Råetikett</th><th>Kedjor</th><th>Butiker</th><th>Kanoniska typer</th></tr></thead>
          <tbody id="tagBody"></tbody></table>`;
      document.getElementById("tagSearch").addEventListener("input", renderTagRows);
      renderTagRows();
    }

    // ---- Märkesvaror (cross-chain-paring av private label, EAN-centrerat) ----
    const marq = { products: [], catalog: [], selected: null, picks: new Map() };
    const mqUnit = (p) => p.comparison_value != null ? `${p.comparison_derived ? "≈ " : ""}${p.comparison_value} kr/${p.comparison_unit || "st"}` : "-";
    const mqThumb = (p, cls = "mq-thumb") => {
      const full = p.ean ? `/v1/products/${encodeURIComponent(p.ean)}/image?size=full` : (p.image || "");
      const src = p.image || full;
      return src ? `<img class="${cls} lb" src="${esc(src)}" data-full="${esc(full || src)}" alt="" loading="lazy">` : `<span class="${cls}"></span>`;
    };
    const chains = (p) => (p.chains || []).map(chip).join("");
    const detailChain = (p) => (p.chains || []).find(c => c === "willys" || c === "hemkop") || (p.chains || [])[0];
    const findProduct = (ean) => marq.products.find(p => p.ean === ean) || marq.catalog.find(p => p.ean === ean);

    async function loadMarques() {
      const d = await (await api("/v1/admin/private-products")).json();
      marq.products = d.products; marq.selected = null; marq.picks.clear();
      const cs = [...new Set(marq.products.flatMap(p => p.chains))];
      const opts = `<option value="">Alla kedjor</option>` + cs.map(c => `<option value="${c}">${c}</option>`).join("");
      document.getElementById("marques").innerHTML = `
        <details class="alert alert-light border small mb-3" open>
          <summary class="fw-semibold" style="cursor:pointer">Vad gör jag här? (märkesvaru-paring)</summary>
          <p class="mb-1 mt-2">Egna märkesvaror (ICA, Garant, Coop, X-tra, City Gross…) har kedjeinterna
            EAN och matchar därför <em>aldrig</em> automatiskt mellan kedjor. Här parar du ihop
            motsvarigheter manuellt så att de får cross-chain-prisjämförelse i appen.</p>
          <ul class="mb-0">
            <li>Välj en produkt till vänster → granska förslagen (semantiskt rankade) eller sök
              manuellt → markera motsvarigheterna → <em>Para ihop valda</em>.</li>
            <li>Para bara <strong>samma vara</strong> (samma innehåll, storlek och variant –
              naturell med naturell, 500 g med 500 g). Jämförelsen bygger på din paring, så en
              felaktig paring ger fel prisjämförelse för slutanvändaren.</li>
            <li>Förslagen är <strong>hjälp, inte facit</strong> – verifiera med <em>info</em>
              (ingredienser/förpackning) och klicka på bilden för att granska den i full storlek.</li>
            <li>Samma EAN i flera kedjor (t.ex. Willys + Hemköp) matchar redan automatiskt och
              behöver inte paras – de visas hopslagna med flera kedje-chips.</li>
            <li>Ett förslag som redan ligger i en paring visar <em>→ grupp N</em>; klick lägger din
              valda produkt i den befintliga gruppen i stället för att skapa en ny.</li>
            <li>Under <em>Befintliga paringar</em> kan du söka, lägga till fler produkter (+ Lägg
              till) och ta bort enskilda medlemmar (✕). En grupp med &lt; 2 medlemmar upplöses.</li>
            <li>När du söker tas även varor ur kedjornas <strong>fulla katalog</strong> med (utöver
              de med erbjudande) - de får chippet <em>inget erbjudande</em>. Du kan förhandsmatcha
              dem; paringen hamnar under <em>Väntar på erbjudande</em> och tänds automatiskt när ett
              erbjudande dyker upp.</li>
          </ul>
        </details>
        <div class="row g-3">
          <div class="col-lg-6"><div class="card p-3">
            <div class="d-flex gap-2 mb-2 align-items-center">
              <select id="mqChain" class="form-select form-select-sm" style="width:auto">${opts}</select>
              <input id="mqSearch" class="form-control form-control-sm" placeholder="Sök produkt…">
              <div class="form-check form-switch small ms-auto" style="white-space:nowrap">
                <input class="form-check-input" type="checkbox" id="mqUnmatched" checked>
                <label class="form-check-label" for="mqUnmatched">Omappade</label></div>
            </div>
            <div id="mqCount" class="small text-muted mb-1"></div>
            <div id="mqList" class="marq-list"></div>
          </div></div>
          <div class="col-lg-6">
            <div id="mqPair" class="card p-3"><div class="text-muted small">Välj en produkt till vänster för att para ihop.</div></div>
            <div id="mqGroups" class="card p-3 mt-3"></div>
          </div>
        </div>`;
      document.getElementById("mqChain").addEventListener("change", renderMqList);
      document.getElementById("mqUnmatched").addEventListener("change", renderMqList);
      let mqt; document.getElementById("mqSearch").addEventListener("input", (e) => {
        renderMqList();  // direkt lokal filtrering
        clearTimeout(mqt); mqt = setTimeout(() => mqCatalogSearch(e.target.value.trim()), 350);  // + katalog
      });
      renderMqList();
      loadMqGroups();
    }

    // Sök även i kedjornas katalog (private-label-varor utan aktuellt erbjudande) och merga in
    // dem i listan, flaggade catalogOnly. Förhandsmatchade par tänds när ett erbjudande dyker upp.
    async function mqCatalogSearch(q) {
      if (q.length < 2) { if (marq.catalog.length) { marq.catalog = []; renderMqList(); } return; }
      const offEans = new Set(marq.products.map(p => p.ean));
      const groupOf = (e) => (findProduct(e) || {}).group_id;
      try {
        const d = await (await api(`/v1/admin/catalog-private?q=${encodeURIComponent(q)}`)).json();
        marq.catalog = (d.products || []).filter(p => !offEans.has(p.ean))
          .map(p => ({ ...p, catalogOnly: true, group_id: p.group_id ?? groupOf(p.ean) ?? null }));
      } catch (e) { marq.catalog = []; }
      renderMqList();
    }

    function renderMqList() {
      const ch = document.getElementById("mqChain").value;
      const q = document.getElementById("mqSearch").value.trim().toLowerCase();
      const onlyUn = document.getElementById("mqUnmatched").checked;
      const items = [...marq.products, ...marq.catalog].filter(p =>
        (!ch || p.chains.includes(ch)) && (!onlyUn || p.group_id == null) &&
        (!q || (p.name || "").toLowerCase().includes(q)));
      const nCat = items.filter(p => p.catalogOnly).length;
      document.getElementById("mqCount").textContent = `${items.length} produkter` + (nCat ? ` (${nCat} ur katalogen)` : "");
      document.getElementById("mqList").innerHTML = items.map(p => `
        <div class="marq-row${marq.selected && marq.selected.ean === p.ean ? " sel" : ""}" data-key="${esc(p.ean)}">
          <div class="d-flex align-items-center gap-2">
            ${mqThumb(p)}
            <div style="min-width:0;flex:1">
              <div class="mq-rowhead d-flex align-items-center gap-2">
                ${chains(p)}<span class="fw-semibold small text-truncate">${esc(p.name || "")}</span>
                <span class="ms-auto d-flex gap-1">
                  ${p.catalogOnly ? `<span class="badge bg-light text-muted border" title="Finns i sortimentet men inget aktuellt erbjudande">inget erbjudande</span>` : ""}
                  ${p.group_id != null ? `<span class="badge bg-success grp-badge">grupp ${p.group_id}</span>` : ""}
                </span>
              </div>
              <div class="small text-muted text-truncate">${esc(p.brand || "")} · ${esc(p.package || "")} · ${mqUnit(p)}</div>
            </div>
          </div>
        </div>`).join("") || '<div class="text-muted small">Inga produkter.</div>';
      document.querySelectorAll("#mqList .marq-row").forEach(r => r.addEventListener("click", () => selectMq(r.dataset.key)));
    }

    async function selectMq(ean) {
      const s = marq.selected = findProduct(ean); marq.picks.clear();
      renderMqList();
      let suggestions = [];
      if (!s.catalogOnly) {  // semantiska förslag (offers); källan måste finnas i offers-cachen
        try { suggestions = (await (await api(`/v1/admin/match/suggestions?ean=${encodeURIComponent(ean)}`)).json()).suggestions || []; } catch (e) {}
      }
      // Merga in katalog-only-kandidater ur aktuella sökningen (andra kedjor, ej redan med).
      const seen = new Set([s.ean, ...suggestions.map(p => p.ean)]);
      const catCands = marq.catalog.filter(p => !seen.has(p.ean) && !p.chains.includes(s.chains[0]));
      renderPair([...suggestions, ...catCands]);
    }

    function candRow(p) {
      return `<div class="dt-wrap">
        <div class="d-flex align-items-center gap-2">
          <label class="mq-cand d-flex align-items-center gap-2 flex-grow-1 mb-0">
            <input type="checkbox" class="mq-pick" data-key="${esc(p.ean)}" ${marq.picks.has(p.ean) ? "checked" : ""} ${p.group_id != null ? "disabled" : ""}>
            ${mqThumb(p)}${chains(p)}<span class="small fw-semibold text-truncate">${esc(p.name || "")}</span>
            <span class="small text-muted text-nowrap">${esc(p.package || "")} · ${mqUnit(p)}</span>
            ${p.score != null ? `<span class="badge bg-light text-dark ms-auto">${p.score}</span>` : ""}
            ${p.catalogOnly ? `<span class="badge bg-light text-muted border${p.score == null ? " ms-auto" : ""}" title="Inget aktuellt erbjudande">inget erbj.</span>` : ""}
          </label>
          ${p.group_id != null ? `<button class="btn btn-sm btn-link p-0 text-nowrap mq-addsrc" data-gid="${p.group_id}" title="Lägg källan i denna paring">→ grupp ${p.group_id}</button>` : ""}
          <button class="btn btn-sm btn-link p-0 dt-btn" data-chain="${detailChain(p)}" data-ean="${esc(p.ean)}">info</button>
        </div>
        <div class="dt-box d-none small ps-4 pt-1"></div></div>`;
    }

    function renderPair(suggestions) {
      const s = marq.selected;
      const sug = suggestions.map(candRow).join("") || '<div class="text-muted small">Inga förslag. Sök manuellt nedan.</div>';
      document.getElementById("mqPair").innerHTML = `
        <div class="d-flex align-items-center mb-2"><h6 class="mb-0">Para ihop</h6>
          <button id="mqClear" class="btn btn-sm btn-link ms-auto p-0">Avbryt</button></div>
        <div class="d-flex gap-2 mb-2">
          ${mqThumb(s, "mq-thumb-lg")}
          <div style="min-width:0">
            <div>${chains(s)} <span class="fw-semibold">${esc(s.name || "")}</span></div>
            <div class="small text-muted">${esc(s.brand || "")} · ${esc(s.package || "")} · ${mqUnit(s)}</div>
            <div class="small text-muted mono">EAN ${esc(s.ean)}</div>
          </div>
        </div>
        <div id="mqBaseDetail" class="small mb-2"></div>
        <div class="small text-uppercase text-muted mb-1">Förslag (andra kedjor)</div>
        <div id="mqSug">${sug}</div>
        <input id="mqManual" class="form-control form-control-sm mt-2" placeholder="Sök manuellt i annan kedja…">
        <div id="mqManualRes" class="mt-1"></div>
        <button id="mqSave" class="btn btn-dark btn-sm w-100 mt-3">Para ihop valda</button>`;
      document.getElementById("mqClear").onclick = clearPair;
      bindPicks(document.getElementById("mqSug"));
      let t; document.getElementById("mqManual").addEventListener("input", (e) => {
        clearTimeout(t); t = setTimeout(() => manualSearch(e.target.value.trim()), 220);
      });
      document.getElementById("mqSave").onclick = saveMatch;
      showDetail(detailChain(s), s.ean, document.getElementById("mqBaseDetail"));  // auto för basprodukten
    }

    function bindPicks(root) {
      root.querySelectorAll(".mq-pick").forEach(cb => cb.addEventListener("change", () => {
        const p = findProduct(cb.dataset.key);
        if (cb.checked) marq.picks.set(cb.dataset.key, p); else marq.picks.delete(cb.dataset.key);
      }));
      root.querySelectorAll(".mq-addsrc").forEach(b => b.addEventListener("click", () => addSourceToGroup(+b.dataset.gid)));
    }

    async function addSourceToGroup(gid) {
      const s = marq.selected;
      if (!s) return;
      const body = { chain: detailChain(s), ean: s.ean, name: s.name, brand: s.brand, package: s.package };
      const r = await api(`/v1/admin/matches/${gid}/members`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) { alert((await r.json()).detail || "Kunde inte lägga till."); return; }
      markRowMatched(s.ean, gid);
      marq.selected = null; marq.picks.clear();
      document.querySelectorAll("#mqList .marq-row.sel").forEach(rr => rr.classList.remove("sel"));
      document.getElementById("mqPair").innerHTML = `<div class="text-success small mb-2">Lade till källan i grupp ${gid}.</div>
        <div class="text-muted small">Välj nästa produkt till vänster.</div>`;
      loadMqGroups();
    }

    function manualSearch(q) {
      const box = document.getElementById("mqManualRes");
      if (!q) { box.innerHTML = ""; return; }
      const ql = q.toLowerCase(), selChains = new Set(marq.selected.chains);
      const items = [...marq.products, ...marq.catalog].filter(p => !p.chains.some(c => selChains.has(c)) && (p.name || "").toLowerCase().includes(ql)).slice(0, 12);
      box.innerHTML = items.map(candRow).join("") || '<div class="text-muted small">Inga träffar.</div>';
      bindPicks(box);
    }

    function clearPair() {
      marq.selected = null; marq.picks.clear();
      document.querySelectorAll("#mqList .marq-row.sel").forEach(r => r.classList.remove("sel"));
      document.getElementById("mqPair").innerHTML = '<div class="text-muted small">Välj en produkt till vänster för att para ihop.</div>';
    }

    function markRowMatched(ean, gid) {
      const p = findProduct(ean); if (p) p.group_id = gid;
      const row = document.querySelector(`#mqList .marq-row[data-key="${ean}"]`);
      if (row && !row.querySelector(".grp-badge")) {
        const b = document.createElement("span");
        b.className = "badge bg-success ms-auto grp-badge"; b.textContent = "grupp " + gid;
        row.querySelector(".mq-rowhead").appendChild(b);
      }
    }

    async function saveMatch() {
      const s = marq.selected;
      const pick = (p) => ({ chain: detailChain(p), ean: p.ean, name: p.name, brand: p.brand, package: p.package });
      const members = [pick(s), ...[...marq.picks.values()].map(pick)];
      if (members.length < 2) { alert("Välj minst en motsvarighet i en annan kedja."); return; }
      const r = await api("/v1/admin/matches", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ members }) });
      if (!r.ok) { alert((await r.json()).detail || "Kunde inte para ihop."); return; }
      const gid = (await r.json()).group_id;
      members.forEach(m => markRowMatched(m.ean, gid));  // in-place, ingen omsortering
      marq.selected = null; marq.picks.clear();
      document.querySelectorAll("#mqList .marq-row.sel").forEach(rr => rr.classList.remove("sel"));
      document.getElementById("mqPair").innerHTML = `<div class="text-success small mb-2">Parad till grupp ${gid}.</div>
        <div class="text-muted small">Välj nästa produkt till vänster.</div>`;
      loadMqGroups();
    }

    async function loadMqGroups() {
      const d = await (await api("/v1/admin/matches")).json();
      marq.groups = d.groups;
      if (!document.getElementById("mqGroupsList")) {
        document.getElementById("mqGroups").innerHTML = `
          <h6 class="mb-2">Befintliga paringar (<span id="mqGroupCount">0</span>)</h6>
          <input id="mqGroupSearch" class="form-control form-control-sm mb-2" placeholder="Sök i paringar (namn/märke/grupp-id)…">
          <div id="mqGroupsList"></div>`;
        document.getElementById("mqGroupSearch").addEventListener("input", (e) => {
          marq.groupQ = e.target.value.trim().toLowerCase(); renderMqGroups();
        });
      }
      renderMqGroups();
    }

    function groupCardHtml(g) {
      return `
        <div class="mq-group" data-gid="${g.group_id}">
          <div class="d-flex align-items-center"><span class="small fw-semibold">Grupp ${g.group_id}</span>
            ${g.active ? "" : `<span class="badge bg-warning text-dark ms-2" title="Ingen medlem har ett aktuellt erbjudande än">väntar</span>`}
            <button class="btn btn-sm btn-link p-0 ms-auto grp-add" data-gid="${g.group_id}">+ Lägg till</button>
            <button class="btn btn-sm btn-link text-danger p-0 ms-2 grp-del" data-gid="${g.group_id}">Ta bort</button></div>
          ${g.members.map(m => { const fp = findProduct(m.ean) || {}; return `<div class="dt-wrap">
            <div class="d-flex align-items-center gap-2 small py-1">
              ${mqThumb(fp)}${chip(m.chain)}
              <span class="text-truncate" style="min-width:0;flex:1">
                <span class="fw-semibold">${esc(m.name || m.ean)}</span>
                <span class="text-muted"> ${esc(fp.brand || m.brand || "")} · ${esc(fp.package || m.package || "")}${fp.comparison_value != null ? " · " + mqUnit(fp) : ""}</span>
              </span>
              <button class="btn btn-sm btn-link p-0 dt-btn" data-chain="${m.chain}" data-ean="${esc(m.ean)}">info</button>
              <button class="btn btn-sm btn-link text-danger p-0 grp-memdel" data-chain="${m.chain}" data-ean="${esc(m.ean)}" title="Ta bort ur paring">✕</button></div>
            <div class="dt-box d-none small ps-3"></div></div>`; }).join("")}
          <div class="grp-addbox d-none mt-1" data-gid="${g.group_id}"></div>
        </div>`;
    }

    function renderMqGroups() {
      const q = marq.groupQ || "", all = marq.groups || [];
      const groups = q
        ? all.filter(g => String(g.group_id) === q ||
            g.members.some(m => `${m.name || ""} ${m.brand || ""}`.toLowerCase().includes(q)))
        : all;
      document.getElementById("mqGroupCount").textContent = q ? `${groups.length}/${all.length}` : all.length;
      const active = groups.filter(g => g.active), waiting = groups.filter(g => !g.active);
      const hdr = (t, n) => `<div class="small text-uppercase text-muted mt-2 mb-1" style="font-size:.7rem">${t} (${n})</div>`;
      let html;
      if (!groups.length) html = `<div class="text-muted small">${q ? "Inga paringar matchar." : "Inga paringar än."}</div>`;
      else if (!waiting.length) html = active.map(groupCardHtml).join("");
      else html = (active.length ? hdr("Aktiva", active.length) + active.map(groupCardHtml).join("") : "")
                + hdr("Väntar på erbjudande", waiting.length) + waiting.map(groupCardHtml).join("");
      document.getElementById("mqGroupsList").innerHTML = html;
      document.querySelectorAll("#mqGroupsList .grp-del").forEach(b => b.addEventListener("click", async () => {
        if (!confirm("Ta bort paringen?")) return;
        await api("/v1/admin/matches/" + b.dataset.gid, { method: "DELETE" });
        marq.products.forEach(p => { if (String(p.group_id) === b.dataset.gid) p.group_id = null; });
        renderMqList(); loadMqGroups();
      }));
      document.querySelectorAll("#mqGroupsList .grp-add").forEach(b => b.addEventListener("click", () => openAddToGroup(+b.dataset.gid)));
      document.querySelectorAll("#mqGroupsList .grp-memdel").forEach(b => b.addEventListener("click", async () => {
        if (!confirm("Ta bort produkten ur paringen?")) return;
        await api(`/v1/admin/matches/${b.dataset.chain}/${encodeURIComponent(b.dataset.ean)}`, { method: "DELETE" });
        const p = findProduct(b.dataset.ean); if (p) p.group_id = null;
        renderMqList(); loadMqGroups();
      }));
    }

    function groupCandRow(p, gid) {
      return `<div class="dt-wrap">
        <div class="d-flex align-items-center gap-2 small py-1">
          ${mqThumb(p)}${chains(p)}
          <span class="text-truncate" style="min-width:0;flex:1">
            <span class="fw-semibold">${esc(p.name || "")}</span>
            <span class="text-muted"> ${esc(p.brand || "")} · ${esc(p.package || "")} · ${mqUnit(p)}</span>
          </span>
          ${p.score != null ? `<span class="badge bg-light text-dark">${p.score}</span>` : ""}
          <button class="btn btn-sm btn-link p-0 dt-btn" data-chain="${detailChain(p)}" data-ean="${esc(p.ean)}">info</button>
          <button class="btn btn-sm btn-link p-0 grp-addbtn" data-gid="${gid}" data-ean="${esc(p.ean)}">lägg till</button>
        </div>
        <div class="dt-box d-none small ps-4 pt-1"></div>
      </div>`;
    }

    function bindGroupAdd(root, gid) {
      root.querySelectorAll(".grp-addbtn").forEach(b => b.addEventListener("click", async () => {
        const p = findProduct(b.dataset.ean);
        if (!p) return;
        const body = { chain: detailChain(p), ean: p.ean, name: p.name, brand: p.brand, package: p.package };
        const r = await api(`/v1/admin/matches/${gid}/members`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        if (!r.ok) { alert((await r.json()).detail || "Kunde inte lägga till."); return; }
        markRowMatched(p.ean, gid);
        loadMqGroups();
      }));
    }

    async function openAddToGroup(gid) {
      const box = document.querySelector(`.grp-addbox[data-gid="${gid}"]`);
      if (!box.classList.contains("d-none")) { box.classList.add("d-none"); box.innerHTML = ""; return; }
      box.classList.remove("d-none");
      box.innerHTML = '<div class="text-muted small">Hämtar förslag…</div>';
      const d = await (await api(`/v1/admin/match/group-suggestions?group_id=${gid}`)).json();
      const sug = (d.suggestions || []).map(p => groupCandRow(p, gid)).join("") || '<div class="text-muted small">Inga förslag. Sök manuellt nedan.</div>';
      box.innerHTML = `<div class="small text-uppercase text-muted mb-1">Lägg till i grupp ${gid}</div>${sug}
        <input class="form-control form-control-sm mt-1 grp-manual" placeholder="Sök manuellt…">
        <div class="grp-manualres mt-1"></div>`;
      bindGroupAdd(box, gid);
      let t; box.querySelector(".grp-manual").addEventListener("input", (e) => {
        clearTimeout(t); t = setTimeout(() => {
          const ql = e.target.value.trim().toLowerCase(), res = box.querySelector(".grp-manualres");
          if (!ql) { res.innerHTML = ""; return; }
          const items = marq.products.filter(p => p.group_id == null && (p.name || "").toLowerCase().includes(ql)).slice(0, 8);
          res.innerHTML = items.map(p => groupCandRow(p, gid)).join("") || '<div class="text-muted small">Inga träffar.</div>';
          bindGroupAdd(res, gid);
        }, 220);
      });
    }

    // ---- Produktdetalj (lazy, ingredienser/näring/ursprung) ----
    function renderDetail(d, chain) {
      if (!d.found || !d.info) return `<span class="text-muted">Detaljerad info inte tillgänglig än.</span>`;
      const x = d.info, P = [];
      if (x.description) P.push(`<div>${esc(x.description)}</div>`);
      if (x.ingredients) P.push(`<div><span class="fw-semibold">Innehåll:</span> ${esc(x.ingredients)}</div>`);
      if (x.allergens && x.allergens.length) P.push(`<div><span class="fw-semibold">Allergener:</span> ${x.allergens.map(a => `<span class="badge bg-warning text-dark">${esc(a)}</span>`).join(" ")}</div>`);
      const orig = [x.origin, x.province].filter(Boolean).join(" · ");
      if (orig) P.push(`<div><span class="fw-semibold">Ursprung:</span> ${esc(orig)}</div>`);
      if (x.storage) P.push(`<div><span class="fw-semibold">Förvaring:</span> ${esc(x.storage)}</div>`);
      if (x.nutrition && x.nutrition.length) {
        const b = x.nutrition_basis ? ` (per ${esc(x.nutrition_basis.value || "")} ${esc(x.nutrition_basis.unit || "")})` : "";
        P.push(`<div><span class="fw-semibold">Näring${b}:</span> ${x.nutrition.map(n => `${esc(n.label)} ${esc(n.value)}${esc(n.unit || "")}`).join(", ")}</div>`);
      }
      if (x.labels && x.labels.length) P.push(`<div class="mt-1">${x.labels.map(l => `<span class="badge bg-light text-dark">${esc(l)}</span>`).join(" ")}</div>`);
      if (x.sources && x.sources.length) P.push(`<div class="mt-1"><span class="text-muted small">Källa:</span> ${x.sources.map(chip).join(" ")}</div>`);
      return P.join("") || `<span class="text-muted">Ingen detaljdata.</span>`;
    }

    async function showDetail(chain, ean, box) {
      if (!box) return;
      box.classList.remove("d-none");
      if (box.dataset.loaded) return;
      box.innerHTML = '<span class="text-muted">Laddar detaljer…</span>';
      const d = await (await api(`/v1/products/${encodeURIComponent(ean)}?prefer_chain=${encodeURIComponent(chain)}`)).json();
      box.dataset.loaded = "1";
      box.innerHTML = renderDetail(d, chain);
    }

    document.addEventListener("click", (e) => {
      const btn = e.target.closest(".dt-btn"); if (!btn) return;
      e.preventDefault();
      const box = btn.closest(".dt-wrap").querySelector(".dt-box");
      if (box.dataset.loaded) box.classList.toggle("d-none");
      else showDetail(btn.dataset.chain, btn.dataset.ean, box);
    });

    // Lightbox: klick på en produktbild visar den i full storlek (granska förpackningen).
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
      const img = e.target.closest("img.lb"); if (!img) return;
      openLightbox(img.dataset.full || img.getAttribute("src"));
    });

    // ---- API-nycklar (externa integratörer) ----
    async function renderKeyTable() {
      const d = await (await api("/v1/admin/api-keys")).json();
      const rows = d.keys.map(k => `<tr class="${k.revoked ? "text-muted" : ""}">
        <td class="mono">${esc(k.prefix)}…</td><td>${esc(k.label || "")}</td>
        <td class="mono small">${esc((k.created_at || "").replace("T", " ").replace("Z", ""))}</td>
        <td class="mono small">${esc((k.last_used || "-").replace("T", " ").replace("Z", ""))}</td>
        <td>${k.revoked ? '<span class="badge bg-secondary">återkallad</span>'
          : `<button class="btn btn-sm btn-link text-danger p-0 key-del" data-id="${k.id}">Återkalla</button>`}</td></tr>`).join("");
      document.getElementById("keyTable").innerHTML = `
        <table class="table table-sm mb-0">
          <thead><tr><th>Prefix</th><th>Etikett</th><th>Skapad</th><th>Senast använd</th><th></th></tr></thead>
          <tbody>${rows || '<tr><td colspan="5" class="text-muted small">Inga nycklar än.</td></tr>'}</tbody></table>`;
      document.querySelectorAll(".key-del").forEach(b => b.addEventListener("click", async () => {
        if (!confirm("Återkalla nyckeln? Den slutar fungera direkt.")) return;
        await api("/v1/admin/api-keys/" + b.dataset.id, { method: "DELETE" });
        renderKeyTable();
      }));
    }

    async function issueKey() {
      const label = document.getElementById("keyLabel").value.trim();
      const r = await api("/v1/admin/api-keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label }) });
      const d = await r.json();
      document.getElementById("keyNew").innerHTML =
        `<div class="alert alert-success small mb-0 py-2"><strong>Ny nyckel (visas bara nu, kopiera den):</strong><br><code>${esc(d.key)}</code></div>`;
      document.getElementById("keyLabel").value = "";
      renderKeyTable();
    }

    async function loadKeys() {
      document.getElementById("keys").innerHTML = `
        <div class="card p-3 mb-3">
          <h6>Utfärda API-nyckel</h6>
          <div class="small text-muted mb-2">För externa integratörer. Nyckeln visas <strong>en gång</strong> - kopiera den direkt (lagras hashad). Skickas som <code>X-API-Key</code>-header. Gatar inte de öppna läs-endpoints; ogiltig/återkallad nyckel nekas (401).</div>
          <div class="input-group input-group-sm" style="max-width:420px">
            <input id="keyLabel" class="form-control" placeholder="etikett (t.ex. partner-x)">
            <button id="keyIssue" class="btn btn-dark">Utfärda</button>
          </div>
          <div id="keyNew" class="mt-2"></div>
        </div>
        <div class="card p-3"><h6>Nycklar</h6><div id="keyTable"></div></div>`;
      document.getElementById("keyIssue").addEventListener("click", issueKey);
      await renderKeyTable();
    }

    // ---- Kategori-mappning ----
    let catData = { items: [], canonical: [] };
    const catState = { sort: { col: "chain_key", dir: 1 } };
    // chain_key (axfood/coop_nav/ica_nav...) -> baskedja för färg.
    const CAT_BASE = { axfood: "willys", coop: "coop", coop_nav: "coop", ica: "ica", ica_nav: "ica", citygross: "citygross" };
    const catChip = (ck) => `<span class="badge badge-chain" style="background:${CHAIN_COLOR[CAT_BASE[ck] || ck] || '#777'}">${esc(ck)}</span>`;

    function catRow(it) {
      const opts = catData.canonical.map(c =>
        `<option value="${c.key}" ${it.canonical === c.key ? "selected" : ""}>${esc(c.label)}</option>`).join("");
      return `<tr class="${it.canonical == null ? "attn" : ""}" data-ck="${esc(it.chain_key)}" data-rk="${esc(it.raw_key)}">
        <td>${catChip(it.chain_key)}</td><td class="mono">${esc(it.raw_key)}</td><td>${it.count}</td>
        <td><select class="form-select form-select-sm cat-sel" style="max-width:210px">
          <option value="" ${it.canonical == null ? "selected" : ""}>— omappad (övrigt) —</option>${opts}</select></td></tr>`;
    }

    function bindCatRows() {
      document.querySelectorAll("#catBody .cat-sel").forEach(sel => sel.addEventListener("change", async () => {
        const tr = sel.closest("tr"), ck = tr.dataset.ck, rk = tr.dataset.rk, canon = sel.value;
        if (!canon) return;
        const r = await api("/v1/admin/categories/map", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ chain_key: ck, raw_key: rk, canonical: canon }) });
        if (r.ok) {
          const it = catData.items.find(x => x.chain_key === ck && x.raw_key === rk);
          if (it) { it.canonical = canon; tr.classList.remove("attn"); }  // in-place, ingen omladdning
        }
      }));
    }

    function renderCatRows() {
      const q = (document.getElementById("catSearch").value || "").trim().toLowerCase();
      const cf = document.getElementById("catChainFilter").value;
      const un = document.getElementById("catUnmapped").checked;
      let rows = catData.items.filter(it =>
        (!q || (it.raw_key || "").toLowerCase().includes(q)) &&
        (!cf || it.chain_key === cf) && (!un || it.canonical == null));
      const { col, dir } = catState.sort;
      rows.sort((a, b) => {
        if (col === "count") return ((a.count || 0) - (b.count || 0)) * dir;
        const va = (a[col] || "").toString().toLowerCase(), vb = (b[col] || "").toString().toLowerCase();
        return va < vb ? -dir : va > vb ? dir : 0;
      });
      document.getElementById("catBody").innerHTML = rows.map(catRow).join("")
        || '<tr><td colspan="4" class="text-muted small">Inga kategorier.</td></tr>';
      document.getElementById("catCount").textContent = `${rows.length} av ${catData.items.length}`;
      bindCatRows();
    }

    async function loadCategoriesTab() {
      catData = await (await api("/v1/admin/categories")).json();
      const keys = [...new Set(catData.items.map(it => it.chain_key))].sort();
      const sortArrow = (c) => catState.sort.col === c ? (catState.sort.dir > 0 ? " ▲" : " ▼") : "";
      document.getElementById("cats").innerHTML = `
        <div class="card p-3">
          <div class="d-flex align-items-center gap-2 mb-2 flex-wrap"><h6 class="mb-0 me-auto">Kategori-mappning</h6>
            <select id="catChainFilter" class="form-select form-select-sm" style="width:auto">
              <option value="">Alla nycklar</option>${keys.map(k => `<option value="${esc(k)}">${esc(k)}</option>`).join("")}</select>
            <div class="form-check form-switch small" style="white-space:nowrap"><input class="form-check-input" type="checkbox" id="catUnmapped">
              <label class="form-check-label" for="catUnmapped">Bara omappade</label></div>
            <input id="catSearch" class="form-control form-control-sm" style="max-width:200px" placeholder="Sök råkategori…"></div>
          <div class="small text-muted mb-2">Råkategori per kedja → kanonisk. Axfood = pipe-pathens första segment
            ("axfood"-nyckel), ICA/Coop = hela råsträngen, Coop navCategories-toppar under "coop_nav".
            Gulmarkerade är omappade (→ övrigt). Klicka kolumnrubrik för att sortera. Ändring slår igenom direkt.</div>
          <table class="table table-sm align-middle mb-0">
            <thead><tr>
              <th class="cat-sort" data-col="chain_key" role="button" style="cursor:pointer">Kedja${sortArrow("chain_key")}</th>
              <th class="cat-sort" data-col="raw_key" role="button" style="cursor:pointer">Råkategori${sortArrow("raw_key")}</th>
              <th class="cat-sort" data-col="count" role="button" style="cursor:pointer">Antal${sortArrow("count")}</th>
              <th class="cat-sort" data-col="canonical" role="button" style="cursor:pointer">Kanonisk${sortArrow("canonical")}</th>
            </tr></thead>
            <tbody id="catBody"></tbody></table>
          <div id="catCount" class="small text-muted mt-1"></div></div>`;
      document.getElementById("catSearch").addEventListener("input", renderCatRows);
      document.getElementById("catChainFilter").addEventListener("change", renderCatRows);
      document.getElementById("catUnmapped").addEventListener("change", renderCatRows);
      const HDR = { chain_key: "Kedja", raw_key: "Råkategori", count: "Antal", canonical: "Kanonisk" };
      document.querySelectorAll("#cats .cat-sort").forEach(th => th.addEventListener("click", () => {
        const c = th.dataset.col;
        catState.sort = { col: c, dir: catState.sort.col === c ? -catState.sort.dir : 1 };
        document.querySelectorAll("#cats .cat-sort").forEach(t =>
          t.textContent = HDR[t.dataset.col] + (catState.sort.col === t.dataset.col ? (catState.sort.dir > 0 ? " ▲" : " ▼") : ""));
        renderCatRows();  // behåller filter (rebygger inte kontrollerna)
      }));
      renderCatRows();
    }

    // ---- Sortiment (fulla katalogen, steg 5): crawl-status + live-visualisering ----
    const CATALOG_IMPLEMENTED = ["citygross", "ica", "coop", "willys", "hemkop"];
    const CRAWL_STATUS = { idle: "väntar", running: "crawlar", ok: "klar", ok_med_fel: "klar (med fel)" };
    function fmtDur(sec) {
      sec = Math.round(sec);
      if (sec < 60) return `${sec}s`;
      const m = Math.floor(sec / 60);
      return m < 60 ? `${m}m ${sec % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
    }
    function fmtBytes(n) {
      if (!n) return "0 B";
      const u = ["B", "kB", "MB", "GB", "TB"];
      let i = 0;
      while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
      return `${n < 10 && i ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
    }
    // Konsekvent ISO 8601 (lokal visning): "2026-06-04T09:33:46Z" / "2026-06-04 12:00" -> "2026-06-04 12:00".
    const fmtTs = (s) => s ? String(s).slice(0, 16).replace("T", " ") : "-";
    // Live-feed: pollen ger batchar (upp till 14/poll); en klient-kö matar ut produkterna EN
    // och en på jämn takt -> kontinuerligt nedåtflöde (ny överst trycker ner listan) + uttoning.
    let feedQueue = [], feedSeen = new Set(), feedPump = null, feedRunning = false, feedStartedAt = null;
    const FEED_RELEASE_MS = 240, FEED_MAX = 18, FEED_QUEUE_CAP = 90;  // > synliga rader: understa tas bort under masken

    function stopFeedPump() { clearInterval(feedPump); feedPump = null; }

    function pumpFeed() {
      const inner = document.getElementById("catalogFeedInner");
      if (!inner || active !== "catalog") { stopFeedPump(); return; }
      const p = feedQueue.shift();
      if (!p) { if (!feedRunning) stopFeedPump(); return; }
      const ph = inner.querySelector(".feed-ph"); if (ph) ph.remove();
      const el = document.createElement("div");
      el.className = "feed-item";
      el.innerHTML = `${chip(p.chain)}<span class="text-truncate flex-grow-1">${esc(p.name || "")}</span><span class="mono text-muted">${esc(p.ean || "")}</span>`;
      inner.prepend(el);
      // Ren transform-scroll: full höjd direkt, hoppa upp en rad (osynligt) och animera ner ->
      // hela listan glider nedåt mjukt utan max-height-reflow.
      const rowH = el.offsetHeight || 32;
      inner.style.transition = "none";
      inner.style.transform = `translateY(-${rowH}px)`;
      void inner.offsetHeight;  // tvinga reflow innan vi animerar tillbaka
      inner.style.transition = "transform .4s ease";
      inner.style.transform = "translateY(0)";
      // Understa raden (under fold/mask) tas bort tyst.
      const items = inner.querySelectorAll(".feed-item");
      if (items.length > FEED_MAX) items[items.length - 1].remove();
    }

    function enqueueFeed(recent) {
      // recent = nyast-först; köa kronologiskt (äldst-ny först) de vi inte redan sett.
      for (let i = recent.length - 1; i >= 0; i--) {
        const p = recent[i], key = `${p.ean}|${p.name}`;
        if (feedSeen.has(key)) continue;
        feedSeen.add(key);
        feedQueue.push(p);
      }
      if (feedQueue.length > FEED_QUEUE_CAP) feedQueue = feedQueue.slice(-FEED_QUEUE_CAP);
      if (!feedPump && (feedQueue.length || feedRunning)) feedPump = setInterval(pumpFeed, FEED_RELEASE_MS);
    }

    // Bygg den statiska layouten EN gång; pollen uppdaterar bara delar (så feeden inte byts ut
    // hårt utan kan animera in/ut).
    function ensureCatalogSkeleton() {
      if (document.getElementById("catalogFeed")) return;
      document.getElementById("catalog").innerHTML = `
        <div class="d-flex align-items-center mb-3">
          <h5 class="mb-0">Fulla sortiment</h5>
          <span id="catalogStatus" class="ms-3 small text-muted"></span>
          <button id="crawlTest" class="btn btn-sm btn-outline-dark ms-auto">Testa alla (2 steg/kedja)</button>
          <button id="crawlNow" class="btn btn-sm btn-dark ms-2">Crawla alla kedjor</button>
        </div>
        <div class="text-muted small mb-1">Walk:ar kedjornas sortiment och persistar hela katalogen med hyllpris (ej bara erbjudanden) - prisändringar fångas över tid. Rate-limitat; en hel kedja tar några minuter. Knapparna uppe till höger kör <strong>alla</strong> implementerade kedjor; varje kedja har egna <em>Testa</em>/<em>Crawla</em>-knappar. Implementerat: City Gross, ICA, Coop, Willys, Hemköp.</div>
        <div id="catalogSchedule" class="text-muted small mb-2"></div>
        <div class="card p-3 mb-3">
          <div class="d-flex align-items-center mb-1">
            <h6 class="mb-0">Axfood-EAN-resolvning</h6>
            <span id="eanWarmStatus" class="ms-2 small text-muted"></span>
          </div>
          <div class="text-muted small mb-2">Slår upp Willys/Hemköp-katalogkoder till EAN (<span class="mono">/p/{code}</span>) så de slås ihop cross-chain med kedjor som redan har EAN. Rate-limitat; full körning är ~tiotusentals anrop. Körs även capat automatiskt efter varje crawl. En kedja i taget.</div>
          <div class="d-flex align-items-center gap-2 flex-wrap mb-1">
            ${chip("willys")}
            <button class="btn btn-sm btn-outline-dark warm-ean" data-chain="willys" data-cap="300">Testa (300)</button>
            <button class="btn btn-sm btn-dark warm-ean" data-chain="willys">Resolva alla</button>
            <span class="ms-2">${chip("hemkop")}</span>
            <button class="btn btn-sm btn-outline-dark warm-ean" data-chain="hemkop" data-cap="300">Testa (300)</button>
            <button class="btn btn-sm btn-dark warm-ean" data-chain="hemkop">Resolva alla</button>
          </div>
          <div id="eanWarmProgress"></div>
        </div>
        <div class="row g-3">
          <div class="col-12 col-lg-7" id="catalogChains"></div>
          <div class="col-12 col-lg-5"><div class="card p-3">
            <h6 class="mb-2">Senast inlästa produkter <span id="catalogLive"></span></h6>
            <div id="catalogFeed"><div id="catalogFeedInner"><div class="feed-ph text-muted small">Starta en crawl för att se produkter strömma in.</div></div></div>
          </div></div>
        </div>`;
      document.getElementById("crawlNow").addEventListener("click", () => triggerCrawl(null, null));
      document.getElementById("crawlTest").addEventListener("click", () => triggerCrawl(2, null));
      document.getElementById("catalog").addEventListener("click", (e) => {
        const b = e.target.closest(".warm-ean");
        if (b && !b.disabled) triggerWarmEans(b.dataset.cap ? Number(b.dataset.cap) : null, b.dataset.chain);
      });
      // Per-kedja-knappar (korten re-renderas varje poll -> delegerad lyssnare på containern).
      document.getElementById("catalogChains").addEventListener("click", (e) => {
        const b = e.target.closest(".catalog-chain-btn");
        if (b && !b.disabled) triggerCrawl(b.dataset.limit ? Number(b.dataset.limit) : null, b.dataset.chain);
      });
    }

    async function loadCatalog() {
      const d = await (await api("/v1/admin/catalog/crawl/status")).json();
      ensureCatalogSkeleton();
      // Feeden delas av crawl OCH EAN-resolvning. Ny körning (started_at ändrad) -> nollställ.
      const warm = d.ean_warm || {};
      const feedKey = d.running ? "c:" + d.started_at : (warm.running ? "w:" + warm.started_at : feedStartedAt);
      if (feedKey !== feedStartedAt) {
        feedStartedAt = feedKey;
        feedSeen = new Set(); feedQueue = [];
        const inner = document.getElementById("catalogFeedInner");
        if (inner) {
          inner.style.transition = "none"; inner.style.transform = "translateY(0)";
          inner.innerHTML = '<div class="feed-ph text-muted small">Starta en crawl eller EAN-resolvning för att se produkter strömma in.</div>';
        }
      }
      feedRunning = !!(d.running || warm.running);
      enqueueFeed(d.running ? (d.recent || []) : (warm.running ? (warm.recent || []) : []));
      const stats = d.stats || {};
      document.getElementById("catalogStatus").innerHTML = d.running
        ? '<span class="st-running">● crawlar…</span>'
        : "";  // "senast" visas per kedja nedan -> ingen redundant topp-rad
      document.getElementById("catalogLive").innerHTML = (d.running || warm.running) ? '<span class="st-running small">● live</span>' : "";
      const sched = document.getElementById("catalogSchedule");
      if (sched) sched.innerHTML = (d.cron && d.cron.trim())
        ? `Schemalagd crawl: <strong>${esc(fmtTs(d.next_run))}</strong> <span class="mono">${esc(d.cron)}</span>${d.finished_at ? ` &middot; senast klar ${esc(fmtTs(d.finished_at))}` : ""}`
        : `Manuell (ingen schemalagd crawl)${d.finished_at ? ` &middot; senast klar ${esc(fmtTs(d.finished_at))}` : ""}`;
      document.getElementById("crawlNow").disabled = d.running;
      document.getElementById("crawlTest").disabled = d.running;
      renderEanWarm(d.ean_warm || {}, d.running);
      document.getElementById("catalogChains").innerHTML = CATALOG_IMPLEMENTED.map((c) => {
        const s = (d.chains || {})[c] || {};
        const st = stats[c] || {};
        const pct = s.categories_total ? Math.round((s.categories_done / s.categories_total) * 100) : 0;
        const bar = s.status === "running" || s.categories_total
          ? `<div class="progress" style="height:6px"><div class="progress-bar bg-success" style="width:${pct}%;transition:width .5s ease"></div></div>
             <div class="small text-muted mt-1">${s.categories_done}/${s.categories_total} steg${s.limited ? " (test)" : ""}${s.current_category ? ` &middot; <span class="fw-semibold">${esc(s.current_category)}</span>` : ""}</div>`
          : "";
        // timing/takt
        const startMs = s.started_at ? Date.parse(s.started_at) : 0;
        const running = s.status === "running";
        const elapsed = startMs ? Math.max(0, ((running ? Date.now() : Date.parse(s.finished_at || s.started_at)) - startMs) / 1000) : 0;
        const rate = elapsed > 0.5 ? s.products / elapsed : 0;
        let timing = "";
        if (running) {
          const remain = (s.total && rate > 0 && s.products < s.total) ? (s.total - s.products) / rate : 0;
          timing = `${rate ? rate.toFixed(0) + " prod/s &middot; " : ""}${fmtDur(elapsed)} förflutet${remain ? ` &middot; ~${fmtDur(remain)} kvar` : ""}`;
        } else if (startMs && s.finished_at) {
          timing = `klar på ${fmtDur(elapsed)}${rate ? ` (${rate.toFixed(0)} prod/s)` : ""}`;
        }
        const statusClass = running ? "running" : s.errors ? "error" : "ok";
        return `<div class="card p-3 mb-2">
          <div class="d-flex align-items-center mb-1">${chip(c)}
            <span class="ms-2 stat" style="font-size:1.2rem">${(s.products || 0).toLocaleString("sv-SE")}</span>
            <span class="ms-2 small text-muted">produkter denna körning (${s.new || 0} nya, ${s.known || 0} befintliga${s.changed ? `, <span class="fw-semibold" style="color:#b8860b">${s.changed} prisändringar</span>` : ""})</span>
            <span class="ms-auto st-${statusClass}">${running ? "● " : ""}${esc(CRAWL_STATUS[s.status] || s.status || "väntar")}${s.errors ? ` &middot; ${s.errors} fel` : ""}</span>
            <span class="ms-2">
              <button class="btn btn-sm btn-outline-secondary py-0 catalog-chain-btn" data-chain="${c}" data-limit="2" ${d.running ? "disabled" : ""}>Testa</button>
              <button class="btn btn-sm btn-outline-dark py-0 catalog-chain-btn" data-chain="${c}" ${d.running ? "disabled" : ""}>Crawla</button>
            </span>
          </div>
          ${bar}
          ${timing ? `<div class="small mt-1" style="color:#6b7480">${timing}</div>` : ""}
          <div class="small text-muted mt-2">Cachat totalt: <strong>${(st.total || 0).toLocaleString("sv-SE")}</strong> produkter
            (${st.available || 0} tillgängliga, ${st.eans || 0} EAN)${st.last_crawl ? ` &middot; senast ${esc(fmtTs(st.last_crawl))}` : ""}</div>
          ${(s.last_errors || []).length ? `<div class="small text-danger mt-1">${s.last_errors.map(esc).join("; ")}</div>` : ""}
        </div>`;
      }).join("");
      clearTimeout(catalogTimer);
      if ((d.running || (d.ean_warm && d.ean_warm.running)) && active === "catalog")
        catalogTimer = setTimeout(loadCatalog, 1500);
    }

    function renderEanWarm(w, crawlRunning) {
      const prog = document.getElementById("eanWarmProgress"), status = document.getElementById("eanWarmStatus");
      document.querySelectorAll(".warm-ean").forEach(b => { b.disabled = w.running || crawlRunning; });
      if (status) status.innerHTML = w.running
        ? (w.cooldown ? '<span class="st-running">● pausad (WAF-block, väntar…)</span>' : '<span class="st-running">● resolvar…</span>')
        : "";
      if (!prog) return;
      const blk = w.blocked ? ` &middot; <span class="text-danger">${(w.blocked).toLocaleString("sv-SE")} blockerade</span>` : "";
      if (w.running) {
        const pct = w.total ? Math.round((w.done / w.total) * 100) : 0;
        const cd = w.cooldown
          ? `<div class="alert alert-warning py-1 px-2 mb-1 small d-flex align-items-center gap-2"><span class="st-running">⏸</span> Pausad - ${w.current_chain ? chip(w.current_chain) + " " : ""}WAF-blockerade. Väntar (cooldown) och försöker igen automatiskt - inte hängd.</div>`
          : "";
        prog.innerHTML = cd + `<div class="progress" style="height:6px"><div class="progress-bar ${w.cooldown ? "bg-warning" : "bg-success"}" style="width:${pct}%;transition:width .5s ease"></div></div>
          <div class="small text-muted mt-1">${(w.done || 0).toLocaleString("sv-SE")}/${(w.total || 0).toLocaleString("sv-SE")} koder${w.current_chain ? ` &middot; ${chip(w.current_chain)}` : ""} &middot; ${w.resolved || 0} med EAN, ${w.empty || 0} utan${blk}</div>`;
      } else if (w.finished_at) {
        const skipped = (w.skipped_chains || []).length ? ` &middot; <span class="text-danger">hoppade: ${w.skipped_chains.map(esc).join(", ")}</span>` : "";
        prog.innerHTML = `<div class="small text-muted">Senast klar ${esc(fmtTs(w.finished_at))}: ${(w.resolved || 0).toLocaleString("sv-SE")} med EAN, ${w.empty || 0} utan${blk} &middot; <strong>${(w.updated || 0).toLocaleString("sv-SE")}</strong> katalograder sammanslagna cross-chain${skipped}${w.error ? ` &middot; <span class="text-danger">fel: ${esc(w.error)}</span>` : ""}</div>`;
      } else {
        prog.innerHTML = "";
      }
    }

    async function triggerWarmEans(cap, chain) {
      const p = new URLSearchParams();
      if (cap) p.set("cap", cap);
      if (chain) p.set("chain", chain);
      const qs = p.toString();
      await api(`/v1/admin/catalog/warm-eans${qs ? "?" + qs : ""}`, { method: "POST" });
      loadCatalog();
    }

    async function triggerCrawl(limit, chain) {
      const p = new URLSearchParams();
      if (limit) p.set("limit_categories", limit);
      if (chain) p.set("chains", chain);
      const qs = p.toString();
      await api(`/v1/admin/catalog/crawl${qs ? "?" + qs : ""}`, { method: "POST" });
      loadCatalog();
    }

    // ---- Inställningar (schemaläggning: cron + tidszon, DB-override > env > default) ----
    const CRON_PRESETS = [
      { label: "Av (ingen schemaläggning)", value: "off" },
      { label: "Varje timme", value: "0 * * * *" },
      { label: "Var 6:e timme", value: "0 */6 * * *" },
      { label: "Var 12:e timme", value: "0 */12 * * *" },
      { label: "Dagligen 03:00", value: "0 3 * * *" },
      { label: "Dagligen 04:00", value: "0 4 * * *" },
      { label: "Veckovis (mån 03:00)", value: "0 3 * * 1" },
    ];
    const TZ_PRESETS = ["Europe/Stockholm", "Europe/Helsinki", "Europe/London", "UTC", "America/New_York"];
    const CRON_FIELDS = [
      { key: "sync_cron", name: "Butikssynk" },
      { key: "offers_sweep_cron", name: "Erbjudande-sweep" },
      { key: "catalog_crawl_cron", name: "Sortiment-crawl" },
    ];
    let settingsPreviewTimer = null;

    function presetOptions(presets, value) {
      const matched = presets.some(p => (p.value ?? p) === value);
      return presets.map(p => { const v = p.value ?? p, l = p.label ?? p;
        return `<option value="${esc(v)}"${v === value ? " selected" : ""}>${esc(l)}</option>`; }).join("")
        + `<option value="__custom"${matched ? "" : " selected"}>Anpassad…</option>`;
    }
    function overrideBadge(item) {
      return item.overridden
        ? `<span class="badge bg-warning text-dark ms-2">override</span>`
        : `<span class="badge bg-light text-muted ms-2">env-default</span>`;
    }
    function settingRow(key, name, item, presets, isCron) {
      return `<div class="set-row mb-3" data-key="${esc(key)}" data-cron="${isCron ? 1 : 0}">
        <div class="d-flex align-items-center mb-1"><strong>${esc(name)}</strong>${overrideBadge(item)}
          <span class="set-preview small text-muted ms-auto"></span></div>
        <div class="d-flex gap-2 align-items-center flex-wrap">
          <select class="form-select form-select-sm set-preset" style="width:auto">${presetOptions(presets, item.value)}</select>
          <input class="form-control form-control-sm set-val mono" style="max-width:220px" value="${esc(item.value || "")}" placeholder="${isCron ? "min tim dag mån vecka" : "Region/Stad"}">
          <button class="btn btn-sm btn-dark set-save">Spara</button>
          <button class="btn btn-sm btn-outline-secondary set-reset"${item.overridden ? "" : " disabled"}>Återställ env</button>
        </div></div>`;
    }

    async function loadSettings() {
      const d = await (await api("/v1/admin/settings")).json();
      const s = d.settings || {};
      document.getElementById("settings").innerHTML = `
        <h5 class="mb-3">Inställningar</h5>
        <div class="text-muted small mb-3">Schemaläggning. Effektivt värde = DB-override &gt; env &gt; kod-default. Ändringar slår igenom <strong>utan omstart</strong> (inom ~30 s). Tomt/"Av" pausar schemat; "Återställ env" tar bort overriden. Cron: <span class="mono">min tim dag månad veckodag</span>.</div>
        <div class="card p-3 mb-3"><h6 class="mb-3">Tidszon</h6>${settingRow("sync_tz", "Tidszon (alla scheman)", s.sync_tz || {}, TZ_PRESETS, false)}</div>
        <div class="card p-3"><h6 class="mb-3">Scheman</h6>${CRON_FIELDS.map(f => settingRow(f.key, f.name, s[f.key] || {}, CRON_PRESETS, true)).join("")}</div>`;
      document.querySelectorAll("#settings .set-row").forEach(wireSettingRow);
    }

    function wireSettingRow(row) {
      const key = row.dataset.key, isCron = row.dataset.cron === "1";
      const sel = row.querySelector(".set-preset"), val = row.querySelector(".set-val");
      const prev = row.querySelector(".set-preview");
      const syncPresetToVal = () => {
        const opts = [...sel.options].map(o => o.value);
        sel.value = opts.includes(val.value) ? val.value : "__custom";
      };
      const preview = () => {
        if (!isCron) { prev.textContent = ""; return; }
        clearTimeout(settingsPreviewTimer);
        settingsPreviewTimer = setTimeout(async () => {
          try {
            const r = await (await api(`/v1/admin/settings/cron-preview?cron=${encodeURIComponent(val.value)}`)).json();
            prev.className = "set-preview small ms-auto " + (r.valid ? "text-muted" : "text-danger");
            prev.textContent = !r.valid ? "Ogiltigt cron-uttryck" : r.disabled ? "Pausad (ingen körning)" : `Nästa: ${r.next_run || "-"}`;
          } catch (e) { prev.textContent = ""; }
        }, 250);
      };
      sel.addEventListener("change", () => { if (sel.value !== "__custom") { val.value = sel.value; preview(); } else val.focus(); });
      val.addEventListener("input", () => { syncPresetToVal(); preview(); });
      row.querySelector(".set-save").addEventListener("click", async () => {
        const r = await api("/v1/admin/settings", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key, value: val.value.trim() }) });
        if (r.ok) { loadSettings(); return; }
        const j = await r.json().catch(() => ({}));
        prev.className = "set-preview small text-danger ms-auto";
        prev.textContent = j.detail || "Kunde inte spara.";
      });
      row.querySelector(".set-reset").addEventListener("click", async () => {
        await api("/v1/admin/settings", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key, reset: true }) });
        loadSettings();
      });
      preview();
    }

    const LOADERS = { overview: loadOverview, kedjor: loadKedjor, sweep: loadSweep, calls: loadCalls, sources: loadSources, tags: loadTags, cats: loadCategoriesTab, catalog: loadCatalog, marques: loadMarques, keys: loadKeys, settings: loadSettings };

    function show(tab) {
      active = tab;
      if (location.hash.slice(1) !== tab) location.hash = tab;
      document.querySelectorAll("#tabs .nav-link").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
      document.querySelectorAll(".tab").forEach(s => s.classList.toggle("d-none", s.id !== tab));
      clearInterval(callsTimer);
      clearTimeout(syncTimer);
      clearTimeout(sweepTimer);
      clearTimeout(catalogTimer);
      if (tab !== "catalog") stopFeedPump();
      LOADERS[tab]().catch(() => {});
      if (tab === "calls") callsTimer = setInterval(refreshCalls, 5000);
    }

    const tabFromHash = () => {
      const t = location.hash.slice(1);
      return LOADERS[t] ? t : "overview";
    };

    // ---- Auth (konsol) ----
    function showGate() {
      clearInterval(callsTimer); clearTimeout(syncTimer); clearTimeout(sweepTimer); clearTimeout(catalogTimer); stopFeedPump();
      consoleEl.classList.add("d-none");
      document.getElementById("consoleAuth").innerHTML = "";
      gate.classList.remove("d-none");
      document.getElementById("cEmail").focus();
    }

    let currentAdmin = null;
    function renderConsoleAuth(me) {
      currentAdmin = me;
      document.getElementById("consoleAuth").innerHTML = `
        <div class="acct">
          <button id="acctBtn" class="acct-btn"><span class="acct-email">${esc(me.email)}</span><span class="acct-caret">&#9662;</span></button>
          <div id="acctMenu" class="acct-menu d-none">
            <button id="acctSettings" class="acct-item">Kontoinställningar</button>
            <button id="acctLogout" class="acct-item">Logga ut</button>
          </div>
        </div>`;
      document.getElementById("acctBtn").onclick = (e) => { e.stopPropagation(); document.getElementById("acctMenu").classList.toggle("d-none"); };
      document.getElementById("acctSettings").onclick = () => { closeAcctMenu(); openSettings(); };
      document.getElementById("acctLogout").onclick = () => { closeAcctMenu(); doLogout(); };
    }
    function closeAcctMenu() { const m = document.getElementById("acctMenu"); if (m) m.classList.add("d-none"); }
    document.addEventListener("click", closeAcctMenu);

    function showConsole(me) {
      gate.classList.add("d-none");
      consoleEl.classList.remove("d-none");
      renderConsoleAuth(me);
      show(tabFromHash());
    }

    async function doLogin() {
      const err = document.getElementById("cErr");
      err.classList.add("d-none");
      const r = await fetch("/v1/console/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: document.getElementById("cEmail").value.trim(), password: document.getElementById("cPass").value }),
      });
      if (!r.ok) { err.textContent = (await r.json()).detail || "Något gick fel."; err.classList.remove("d-none"); return; }
      document.getElementById("cPass").value = "";
      showConsole(await r.json());
    }

    async function doLogout() {
      await fetch("/v1/console/auth/logout", { method: "POST" });
      showGate();
    }

    // ---- Kontoinställningar (utbyggbar; rymmer just nu lösenordsbyte) ----
    const settingsModal = document.getElementById("settingsModal");
    function openSettings() {
      document.getElementById("setEmail").textContent = currentAdmin ? currentAdmin.email : "";
      document.getElementById("setCur").value = "";
      document.getElementById("setNew").value = "";
      document.getElementById("setMsg").classList.add("d-none");
      settingsModal.classList.remove("d-none");
      document.getElementById("setCur").focus();
    }
    async function saveSettingsPassword() {
      const msg = document.getElementById("setMsg");
      const show = (txt, ok) => { msg.textContent = txt; msg.className = "small mb-2 " + (ok ? "text-success" : "text-danger"); };
      const r = await fetch("/v1/console/auth/password", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: document.getElementById("setCur").value, new_password: document.getElementById("setNew").value }),
      });
      if (!r.ok) { show((await r.json()).detail || "Något gick fel.", false); return; }
      document.getElementById("setCur").value = "";
      document.getElementById("setNew").value = "";
      show("Lösenordet är bytt.", true);
    }

    document.getElementById("cLogin").addEventListener("click", doLogin);
    document.getElementById("cPass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
    document.getElementById("setClose").addEventListener("click", () => settingsModal.classList.add("d-none"));
    document.getElementById("settingsModal").addEventListener("click", (e) => { if (e.target.id === "settingsModal") settingsModal.classList.add("d-none"); });
    document.getElementById("setSave").addEventListener("click", saveSettingsPassword);
    document.getElementById("setNew").addEventListener("keydown", (e) => { if (e.key === "Enter") saveSettingsPassword(); });

    document.querySelectorAll("#tabs .nav-link").forEach(b =>
      b.addEventListener("click", () => show(b.dataset.tab)));
    window.addEventListener("hashchange", () => {
      const t = tabFromHash();
      if (t !== active && !consoleEl.classList.contains("d-none")) show(t);
    });

    (async function initConsole() {
      try {
        const me = await (await fetch("/v1/console/auth/me")).json();
        if (me) showConsole(me); else showGate();
      } catch (e) { showGate(); }
    })();
