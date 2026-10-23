// Warehouse / hub staff screens: the loading bay (what to hand each driver, in loading order, with a
// tick per parcel) and the receiving desk (collected pickups, parcels the driver could not deliver,
// returns). No map. Reads /api/warehouse/loading and /receiving; ticks go through
// /api/parcels/{id}/loaded, /api/runs/{id}/loaded and /api/parcels/{id}/received.

let whView = "load";
let whDriver = "";                   // "" = every driver
let whTimer = null;
let whData = { load: null, recv: null };

function setupWarehouse() {
  if (!$("tab-warehouse")) return;
  $("wh-driver").onchange = () => { whDriver = $("wh-driver").value; renderWarehouse(); };
  document.querySelectorAll("#tab-warehouse [data-wh]").forEach(b => {
    b.onclick = () => { whView = b.dataset.wh; renderWarehouse(); };
  });
  window.addEventListener("afterprint", () => {
    document.body.classList.remove("printing-run");
    document.querySelectorAll(".wh-run.printing").forEach(c => c.classList.remove("printing"));
  });
}

function startWarehouse() {
  loadWarehouse();
  clearInterval(whTimer);
  whTimer = setInterval(() => { if (activeTab === "warehouse") loadWarehouse(true); else clearInterval(whTimer); }, 10000);
}

async function loadWarehouse(quiet = false) {
  try {
    const [load, recv] = await Promise.all([api("/api/warehouse/loading"), api("/api/warehouse/receiving")]);
    whData = { load, recv };
  } catch (err) {
    if (!quiet) $("wh-load").innerHTML = `<div class="empty-state"><b>Couldn’t load the warehouse</b>${pdEsc(err.message)}</div>`;
    return;
  }
  renderWarehouse();
}

function whInitials(name) { return typeof initialsOf === "function" ? initialsOf(name) : String(name || "?").slice(0, 2).toUpperCase(); }

function renderWarehouse() {
  const { load: all, recv } = whData;
  if (!all || !recv) return;
  // the driver picker lists everyone who has parcels waiting to be handed over
  const drivers = [...new Set(all.runs.map(r => r.driverName).filter(Boolean))].sort();
  if (whDriver && !drivers.includes(whDriver)) whDriver = "";
  $("wh-driver").innerHTML = `<option value="">All drivers (${drivers.length})</option>` + drivers.map(d =>
    `<option value="${pdEsc(d)}" ${d === whDriver ? "selected" : ""}>${pdEsc(d)}</option>`).join("");
  $("wh-filter").hidden = whView !== "load";
  const runs = whDriver ? all.runs.filter(r => r.driverName === whDriver) : all.runs;
  const load = { runs, totals: { runs: runs.length, parcels: runs.reduce((n, r) => n + r.total, 0),
    loaded: runs.reduce((n, r) => n + r.loaded, 0), readyRuns: runs.filter(r => r.ready).length } };
  const t = load.totals, c = recv.counts, incoming = c.collected + c.not_delivered + c.returned;
  document.querySelectorAll("#tab-warehouse [data-wh]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.wh === whView)));
  $("wh-n-load").textContent = all.totals.runs;
  $("wh-n-recv").textContent = incoming;
  $("wh-n-recv").hidden = !incoming;
  $("wh-load").hidden = whView !== "load";
  $("wh-recv").hidden = whView !== "recv";
  $("wh-summary").innerHTML = whView === "load" ? `
      <div><b>${t.runs}</b><span>${whDriver ? "run for " + pdEsc(whDriver) : "drivers to serve"}</span></div>
      <div><b>${t.loaded}/${t.parcels}</b><span>parcels loaded</span></div>
      <div><b>${t.readyRuns}</b><span>ready to leave</span></div>` : `
      <div><b>${c.collected}</b><span>collected from customers</span></div>
      <div><b>${c.not_delivered}</b><span>not delivered</span></div>
      <div><b>${c.returned}</b><span>returned to sender</span></div>`;
  if (whView === "load") renderLoading(load); else renderReceiving(recv);
}

function renderLoading(load) {
  const box = $("wh-load");
  const canLoad = can("warehouse.load");
  if (!load.runs.length) {
    box.innerHTML = `<div class="empty-state"><b>Nothing to hand over</b>Each driver’s parcels appear here as soon as a dispatcher plans their run.</div>`;
    return;
  }
  box.innerHTML = "";
  load.runs.forEach(r => {
    const pct = r.total ? Math.round(r.loaded / r.total * 100) : 0;
    const card = document.createElement("article");
    card.className = "wh-run" + (r.ready ? " ready" : "");
    card.innerHTML = `
      <div class="wh-print-head"><b>Hand-over list</b> · ${pdEsc(r.driverName || "No driver")} · ${pdEsc(r.vehicleName || r.vehicleType)}${r.plate ? " · " + pdEsc(r.plate) : ""} · ${new Date().toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })}</div>
      <header class="wh-head">
        <span class="avatar sm">${whInitials(r.driverName)}</span>
        <div class="wh-who"><b>${pdEsc(r.driverName || "No driver")}</b>
          <small>${r.total} parcel${r.total === 1 ? "" : "s"} to hand over · ${pdEsc(r.vehicleName || r.vehicleType)}${r.plate ? " · " + pdEsc(r.plate) : ""}${r.driverPhone ? " · " + pdEsc(r.driverPhone) : ""}${r.templateCode ? " · " + pdEsc(r.templateCode) : ""}</small></div>
        <span class="pill ${r.ready ? "ok" : "warn"}">${r.ready ? "Ready to leave" : `${r.loaded}/${r.total} loaded`}</span>
        <div class="wh-actions">
          <button type="button" class="btn btn-ghost" data-print>Print hand-over list</button>
          ${canLoad ? `<button type="button" class="btn btn-primary" data-all ${r.ready ? "disabled" : ""}>Mark all loaded</button>` : ""}
        </div>
      </header>
      <div class="wh-bar" role="img" aria-label="${pct}% loaded"><i style="width:${pct}%"></i></div>
      <ol class="wh-list">${r.parcels.map(p => `
        <li class="wh-item${p.loaded ? " loaded" : ""}">
          <label><input type="checkbox" data-pid="${p.id}" ${p.loaded ? "checked" : ""} ${canLoad ? "" : "disabled"}
            aria-label="Loaded: ${pdEsc(p.trackingNumber || p.name)}">
            <span class="wh-n" aria-label="Load number">${p.loadOrder}</span>
            <span class="wh-main"><b>${pdEsc(p.trackingNumber || p.name)}</b> ${pdEsc(p.name)}
              <small>To ${pdEsc(p.to)}${p.receiver ? " · " + pdEsc(p.receiver) : ""}${p.notes ? " · " + pdEsc(p.notes) : ""}${p.loaded && p.loadedBy ? " · loaded by " + pdEsc(p.loadedBy) : ""}</small></span>
            <span class="wh-chips">
              ${p.fragile ? `<span class="p-chip fragile">fragile</span>` : ""}
              ${p.priority !== "standard" ? `<span class="p-chip${p.priority === "express" ? " fragile" : ""}">${pdEsc(p.priority)}</span>` : ""}
              <span class="size-chip">${pdEsc((p.size || "?")[0])}</span>${p.weightKg ? `<span class="wh-kg">${(+p.weightKg).toLocaleString()} kg</span>` : ""}
            </span>
          </label>
        </li>`).join("")}</ol>
      <div class="field-err form-err wh-err" role="alert" hidden></div>`;
    const err = card.querySelector(".wh-err");
    const fail = e => { err.textContent = e.message; err.hidden = false; };
    card.querySelectorAll("input[data-pid]").forEach(cb => {
      cb.onchange = async () => {
        err.hidden = true;
        try { await api(`/api/parcels/${cb.dataset.pid}/loaded`, { method: "POST", body: JSON.stringify({ loaded: cb.checked }) }); await loadWarehouse(true); }
        catch (e) { cb.checked = !cb.checked; fail(e); }
      };
    });
    const all = card.querySelector("[data-all]");
    if (all) all.onclick = async () => {
      all.disabled = true; all.setAttribute("aria-busy", "true");
      try { await api(`/api/runs/${r.tripId}/loaded`, { method: "POST", body: JSON.stringify({ loaded: true }) }); await loadWarehouse(true); }
      catch (e) { fail(e); all.disabled = false; all.removeAttribute("aria-busy"); }
    };
    card.querySelector("[data-print]").onclick = () => {
      card.classList.add("printing");
      document.body.classList.add("printing-run");
      window.print();
    };
    box.append(card);
  });
}

const WH_GROUPS = [
  ["collected", "Collected from customers", "Pickups the driver brought back. Check them in against the customer."],
  ["not_delivered", "Not delivered", "The driver could not deliver these. Check them in, then dispatch reviews them."],
  ["returned", "Returned to sender", "Waiting to be handed back to the sender."],
];

function renderReceiving(recv) {
  const box = $("wh-recv");
  const canIn = can("warehouse.receive");
  if (!recv.items.length) {
    box.innerHTML = `<div class="empty-state"><b>Nothing waiting</b>Everything that came back has been checked in.</div>`;
    return;
  }
  box.innerHTML = "";
  WH_GROUPS.forEach(([kind, title, hint]) => {
    const items = recv.items.filter(i => i.kind === kind);
    if (!items.length) return;
    const sec = document.createElement("section");
    sec.className = "wh-group";
    sec.innerHTML = `<h3>${title} <span class="acc-count">${items.length}</span></h3><p class="hint">${hint}</p>` + items.map(i => `
      <div class="wh-recv-row">
        <div class="wh-main"><b>${pdEsc(i.trackingNumber || i.name)}</b> ${pdEsc(i.name)}
          <small>${i.from ? "From " + pdEsc(i.from) + " · " : ""}${i.customer ? pdEsc(i.customer) + " · " : ""}${i.reason ? pdEsc(i.reason) + " · " : ""}${i.driverName ? "driver " + pdEsc(i.driverName) + " · " : ""}${i.since ? pdWhen(i.since) : ""}</small></div>
        <span class="wh-chips">${i.fragile ? `<span class="p-chip fragile">fragile</span>` : ""}<span class="size-chip">${pdEsc((i.size || "?")[0])}</span>${i.weightKg ? `<span class="wh-kg">${(+i.weightKg).toLocaleString()} kg</span>` : ""}</span>
        ${canIn ? `<button type="button" class="btn btn-primary" data-in="${i.id}">Check in</button>` : ""}
      </div>`).join("");
    sec.querySelectorAll("[data-in]").forEach(b => {
      b.onclick = async () => {
        b.disabled = true; b.setAttribute("aria-busy", "true");
        try { await api(`/api/parcels/${b.dataset.in}/received`, { method: "POST", body: JSON.stringify({}) }); await loadWarehouse(true); }
        catch (e) { b.disabled = false; b.removeAttribute("aria-busy"); alert(e.message); }
      };
    });
    box.append(sec);
  });
}
