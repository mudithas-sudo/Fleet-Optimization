// FleetOps Delivery Console.
//
// Parcels tab: manage deliverables (manual add, CSV/Excel import), select
// pending parcels → assign an eligible driver → a run is planned by the ADK
// agent from the depot. Runs tab: live fleet monitoring (map, manifest,
// alerts). Registry tab: vehicles and drivers.
// A global SSE stream (/api/stream) carries trip AND parcel events.

let map;
let activeTab = "parcels";
const LIVE = ["active", "deviating"];

// vehicle glyph as an icon-wrapped SVG (chrome uses SVG, never emoji)
function vehBadge(type) { return `<span class="veh-ico">${vehIconSvg(type)}</span>`; }

// domain state
let parcels = {};          // id -> parcel
let selectedParcels = new Set();
let vehiclesReg = [];
let driversReg = [];
let depot = null;

// fleet state
let trips = {};            // id -> summary
let detail = null;
let selectedId = null;
let vehicles = {};         // id -> map marker
let routeLine = null;
let selStopMarkers = [];
let trafficLines = [];

// parcel form state
let pfSize = "small";
let pfDest = null;         // {lat, lng, label}
let pfMarker = null;
let depotEditing = false;

// picker state
let pickerDriverId = null;

const $ = id => document.getElementById(id);

init();

async function init() {
  initTheme();
  await loadMaps();
  map = new google.maps.Map($("map"), {
    center: { lat: 6.9271, lng: 79.8612 }, zoom: 12,
    styles: mapThemeStyles(),
    disableDefaultUI: true, zoomControl: true, clickableIcons: false,
  });
  themeAwareMap(map, () => routeLine);

  map.addListener("click", e => onMapClick(e.latLng.lat(), e.latLng.lng()));

  ["parcels", "fleet", "registry"].forEach(t => {
    $(`tab-btn-${t}`).onclick = () => switchTab(t);
  });

  // fill the vehicle-type picker glyphs with SVG icons
  document.querySelectorAll("#vf-type [data-veh]").forEach(el => {
    el.innerHTML = vehIconSvg(el.dataset.veh);
  });

  // depot chip is a role=button — support keyboard activation
  $("depot-chip").addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); startDepotEdit(); }
  });

  // parcels tab
  $("btn-add-parcel").onclick = () => toggleParcelForm(true);
  $("pf-cancel").onclick = () => toggleParcelForm(false);
  $("pf-save").onclick = saveParcel;
  $("btn-clear-delivered").onclick = clearDelivered;
  document.querySelectorAll("#pf-size button").forEach(b => {
    b.onclick = () => {
      pfSize = b.dataset.s;
      document.querySelectorAll("#pf-size button").forEach(x => {
        const on = x === b;
        x.classList.toggle("active", on);
        x.setAttribute("aria-pressed", String(on));
      });
    };
  });
  setupDestSearch();
  $("depot-chip").onclick = startDepotEdit;
  $("btn-plan-run").onclick = openPicker;
  $("btn-auto").onclick = autoAssign;
  $("btn-close-dispatch").onclick = closeDispatch;
  $("btn-dispatch-discard").onclick = closeDispatch;
  $("btn-dispatch-approve").onclick = approveDispatch;
  $("btn-close-picker").onclick = () => { $("run-picker").style.display = "none"; };
  $("btn-create-run").onclick = createRun;
  setupImport();

  // fleet tab
  $("btn-clear-all").onclick = clearAllTrips;
  $("btn-close-detail").onclick = deselectTrip;
  $("btn-delete").onclick = deleteSelected;

  // registry
  $("btn-add-vehicle").onclick = () => { $("vehicle-form").style.display = "flex"; };
  $("vf-cancel").onclick = () => { $("vehicle-form").style.display = "none"; };
  $("vf-save").onclick = saveVehicle;
  document.querySelectorAll("#vf-type button").forEach(b => {
    b.onclick = () => {
      document.querySelectorAll("#vf-type button").forEach(x => {
        const on = x === b;
        x.classList.toggle("active", on);
        x.setAttribute("aria-pressed", String(on));
      });
      const d = { bike: ["small", 2], car: ["medium", 4], van: ["large", 8], truck: ["large", 20] }[b.dataset.t];
      $("vf-maxsize").value = d[0];
      $("vf-capacity").value = d[1];
    };
  });
  $("btn-add-driver").onclick = () => { renderDriverVehicleSelect(); $("driver-form").style.display = "flex"; };
  $("df-cancel").onclick = () => { $("driver-form").style.display = "none"; };
  $("df-save").onclick = saveDriver;

  await Promise.all([loadDepot(), loadParcels(), loadRegistry(), loadTrips()]);
  openStream();
  setInterval(renderParcels, 30000);   // deadline countdowns
}

function switchTab(tab) {
  activeTab = tab;
  ["parcels", "fleet", "registry"].forEach(t => {
    const on = t === tab;
    const btn = $(`tab-btn-${t}`);
    btn.setAttribute("aria-selected", String(on));
    btn.tabIndex = on ? 0 : -1;
    $(`tab-${t}`).hidden = !on;
  });
  updateRunFooter();
}

function onMapClick(lat, lng) {
  if (depotEditing) {
    depot = { lat, lng, label: depot?.label || "Depot" };
    api("/api/revgeocode?lat=" + lat + "&lng=" + lng).then(r => {
      if (r.label) depot.label = r.label;
      saveDepot();
    }).catch(saveDepot);
    return;
  }
  if (activeTab === "parcels" && $("parcel-form").style.display !== "none") {
    setPfDest({ lat, lng, label: "Dropped pin" });
    api(`/api/revgeocode?lat=${lat}&lng=${lng}`)
      .then(r => { if (r.label) setPfDest({ lat, lng, label: r.label }); })
      .catch(() => {});
  }
}

// ---------------- depot ----------------

async function loadDepot() {
  depot = await api("/api/settings/depot");
  $("depot-label").textContent = depot.label;
}

function startDepotEdit() {
  depotEditing = true;
  $("depot-label").textContent = "click the map to move the depot…";
}

async function saveDepot() {
  depotEditing = false;
  await api("/api/settings/depot", { method: "PUT", body: JSON.stringify(depot) });
  $("depot-label").textContent = depot.label;
}

// ---------------- parcels ----------------

async function loadParcels() {
  (await api("/api/parcels")).forEach(p => { parcels[p.id] = p; });
  renderParcels();
}

function fmtDeadline(p) {
  const mins = (p.deadline - Date.now() / 1000) / 60;
  if (p.status === "delivered") {
    return { text: "delivered", cls: "" };
  }
  if (mins < 0) return { text: `Overdue ${Math.round(-mins)}m`, cls: "overdue" };
  if (mins < 60) return { text: `${Math.round(mins)}m left`, cls: "urgent" };
  if (mins < 180) return { text: `${Math.round(mins / 60 * 10) / 10}h left`, cls: "warn" };
  const d = new Date(p.deadline * 1000);
  return { text: d.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }), cls: "" };
}

function renderParcels() {
  const list = Object.values(parcels).sort((a, b) => a.deadline - b.deadline);
  const overdue = list.filter(p => p.deadlineMissed && p.status !== "delivered").length;
  $("parcel-badge").hidden = !overdue;
  $("parcel-badge").textContent = overdue;

  const wrap = $("parcel-list");
  wrap.innerHTML = "";
  if (!list.length) {
    wrap.innerHTML = `<div class="empty">${iconSvg("depot", "i-lg")}
      <b>No parcels yet</b>Add one manually or import a spreadsheet to get started.</div>`;
    return;
  }
  list.forEach(p => {
    const row = document.createElement("div");
    row.className = "parcel-row";
    const dl = fmtDeadline(p);
    const lateTag = p.status === "delivered" && p.deadlineMissed
      ? `<span class="late-tag">late</span>` : "";
    row.innerHTML = `
      ${p.status === "pending"
        ? `<input type="checkbox" aria-label="Select ${p.name}" ${selectedParcels.has(p.id) ? "checked" : ""}>`
        : `<span class="p-dot ${p.status}" title="${p.status.replace("_", " ")}"></span>`}
      <div class="info">
        <div class="label">${p.name} <span class="size-chip">${p.size[0]}</span> ${lateTag}</div>
        <div class="meta">${p.destination.label} · ${p.type} · ${p.status.replace("_", " ")}</div>
      </div>
      <div class="deadline ${dl.cls}">${dl.cls ? iconSvg("clock") : ""}${dl.text}</div>
      ${p.status === "pending" ? `<button class="del" type="button" aria-label="Delete ${p.name}">${iconSvg("trash")}</button>` : ""}`;
    const cb = row.querySelector("input[type=checkbox]");
    if (cb) cb.onchange = () => {
      cb.checked ? selectedParcels.add(p.id) : selectedParcels.delete(p.id);
      updateRunFooter();
    };
    const del = row.querySelector(".del");
    if (del) del.onclick = async () => {
      if (!confirm(`Delete parcel "${p.name}"?`)) return;
      try { await api(`/api/parcels/${p.id}`, { method: "DELETE" }); }
      catch (err) { alert(err.message); }
    };
    wrap.append(row);
  });
  updateRunFooter();
}

async function clearDelivered() {
  const done = Object.values(parcels).filter(p => p.status === "delivered");
  if (!done.length || !confirm(`Remove ${done.length} delivered parcel(s) from the list?`)) return;
  // delivered parcels are not deletable server-side by design; hide locally
  done.forEach(p => delete parcels[p.id]);
  renderParcels();
}

function updateRunFooter() {
  selectedParcels.forEach(id => {
    if (!parcels[id] || parcels[id].status !== "pending") selectedParcels.delete(id);
  });
  const n = selectedParcels.size;
  $("run-footer").style.display = activeTab === "parcels" && n ? "block" : "none";
  $("btn-plan-run").textContent = `Plan delivery run (${n})`;
}

function toggleParcelForm(show) {
  $("parcel-form").style.display = show ? "flex" : "none";
  if (!show) {
    pfDest = null;
    if (pfMarker) { pfMarker.setMap(null); pfMarker = null; }
    $("pf-name").value = ""; $("pf-type").value = ""; $("pf-dest").value = "";
    $("pf-dest-label").textContent = "No destination set";
    $("pf-save").disabled = true;
  }
}

function setPfDest(dest) {
  pfDest = dest;
  $("pf-dest-label").innerHTML = "";
  $("pf-dest-label").append(
    Object.assign(document.createElement("span"), {
      innerHTML: iconSvg("pin"), style: "vertical-align:-2px;margin-right:4px",
    }),
    document.createTextNode(dest.label));
  if (pfMarker) pfMarker.setMap(null);
  pfMarker = stopMarker(map, dest, 1, 3);
  $("pf-save").disabled = !$("pf-name").value.trim();
}

function setupDestSearch() {
  $("pf-name").oninput = () => { $("pf-save").disabled = !(pfDest && $("pf-name").value.trim()); };
  $("pf-dest").addEventListener("keydown", async e => {
    if (e.key !== "Enter" || !$("pf-dest").value.trim()) return;
    const input = $("pf-dest");
    input.classList.add("busy");
    try {
      const r = await api(`/api/geocode?q=${encodeURIComponent(input.value.trim())}`);
      setPfDest(r);
      map.panTo({ lat: r.lat, lng: r.lng });
      input.value = "";
    } catch (_) {
      input.placeholder = "Not found — try again…";
      input.value = "";
    } finally {
      input.classList.remove("busy");
    }
  });
}

async function saveParcel() {
  const deadlineStr = $("pf-deadline").value;
  const deadline = deadlineStr ? new Date(deadlineStr).getTime() / 1000
                               : Date.now() / 1000 + 4 * 3600;
  try {
    await api("/api/parcels", {
      method: "POST",
      body: JSON.stringify({
        name: $("pf-name").value.trim(),
        type: $("pf-type").value.trim() || "general",
        size: pfSize,
        destination: pfDest,
        deadline,
      }),
    });
    toggleParcelForm(false);
  } catch (err) {
    alert(err.message);
  }
}

// ---------------- import ----------------

function setupImport() {
  $("btn-import").onclick = () => {
    $("import-modal").style.display = "flex";
    $("import-preview").innerHTML = "";
    $("import-progress").textContent = "";
    $("btn-do-import").style.display = "none";
    $("import-file").value = "";
  };
  $("btn-close-import").onclick = () => { $("import-modal").style.display = "none"; };
  $("import-file").onchange = previewImport;
}

let importRows = [];

async function previewImport() {
  const file = $("import-file").files[0];
  if (!file) return;
  $("import-progress").textContent = "Parsing…";
  let raw;
  try {
    raw = await parseImportFile(file);
  } catch (err) {
    $("import-progress").textContent = "Parse failed: " + err.message;
    return;
  }
  importRows = [];
  const out = [];
  for (let i = 0; i < raw.length; i++) {
    const r = raw[i];
    $("import-progress").textContent = `Geocoding ${i + 1}/${raw.length}…`;
    const row = { ...r, error: null, destinationResolved: null };
    if (!r.name) row.error = "missing name";
    else if (!SIZES_OK(r.size)) row.error = "size must be small/medium/large";
    else if (parseDeadline(r.deadline) === null) row.error = "bad deadline";
    else {
      try {
        row.destinationResolved = await api(`/api/geocode?q=${encodeURIComponent(r.destination)}`);
      } catch (_) {
        row.error = "destination not found";
      }
    }
    out.push(row);
  }
  importRows = out;
  const ok = out.filter(r => !r.error).length;
  $("import-progress").textContent =
    `${ok} of ${out.length} rows ready` + (ok < out.length ? " — rows with errors are skipped" : "");
  $("import-preview").innerHTML = `<table>
    <tr><th>Name</th><th>Size</th><th>Destination</th><th>Deadline</th><th></th></tr>
    ${out.map(r => `<tr>
      <td>${r.name || "—"}</td><td>${r.size}</td>
      <td>${r.destinationResolved ? r.destinationResolved.label : r.destination}</td>
      <td>${r.deadline}</td>
      <td class="${r.error ? "row-err" : "row-ok"}">${r.error || "ready"}</td>
    </tr>`).join("")}
  </table>`;
  $("btn-do-import").style.display = ok ? "block" : "none";
  $("btn-do-import").textContent = `Import ${ok} parcel${ok === 1 ? "" : "s"}`;
}

function SIZES_OK(s) { return ["small", "medium", "large"].includes((s || "").toLowerCase()); }

$("btn-do-import") && ($("btn-do-import").onclick = async () => {
  const good = importRows.filter(r => !r.error);
  const body = {
    parcels: good.map(r => ({
      name: r.name,
      type: r.type || "general",
      size: r.size.toLowerCase(),
      destination: r.destinationResolved,
      deadline: parseDeadline(r.deadline),
    })),
  };
  const res = await api("/api/parcels/bulk", { method: "POST", body: JSON.stringify(body) });
  $("import-modal").style.display = "none";
  if (res.errors.length) alert(`${res.errors.length} rows were rejected by the server.`);
});

// ---------------- run creation ----------------

function openPicker() {
  pickerDriverId = null;
  $("picker-count").textContent = selectedParcels.size;
  renderPickerDrivers();
  $("run-picker").style.display = "flex";
  $("trip-detail").style.display = "none";
}

function renderPickerDrivers() {
  const sel = [...selectedParcels].map(id => parcels[id]);
  const wrap = $("picker-drivers");
  wrap.innerHTML = "";
  if (!driversReg.length) {
    wrap.innerHTML = `<div class="empty">${iconSvg("user", "i-lg")}
      <b>No drivers yet</b>Add vehicles and drivers in the Registry tab first.</div>`;
  }
  driversReg.forEach(d => {
    const v = vehiclesReg.find(x => x.id === d.vehicleId);
    let reason = null;
    if (!v) reason = "no vehicle assigned";
    else {
      if (sel.length > v.capacity) reason = `capacity ${v.capacity} < ${sel.length} parcels`;
      const order = { small: 0, medium: 1, large: 2 };
      const over = sel.find(p => order[p.size] > order[v.maxParcelSize]);
      if (!reason && over) reason = `max size ${v.maxParcelSize} < ${over.size} (${over.name})`;
    }
    const busy = Object.values(trips).some(t => t.driverId === d.id && LIVE.includes(t.status));
    const card = document.createElement("button");
    card.type = "button";
    card.className = "driver-card" + (reason ? " disabled" : "") +
      (pickerDriverId === d.id ? " selected" : "");
    if (reason) card.disabled = true;
    card.innerHTML = `
      <span class="veh-ico">${v ? vehIconSvg(v.type) : iconSvg("help")}</span>
      <div class="info">
        <div class="label">${d.name}</div>
        <div class="meta">${v ? `${v.name} · ${v.plate} · cap ${v.capacity} · max ${v.maxParcelSize}` : "no vehicle"}</div>
        ${reason ? `<div class="reason">${iconSvg("alert")} ${reason}</div>` : ""}
      </div>
      ${busy ? `<span class="busy-chip">en route</span>` : ""}`;
    if (!reason) card.onclick = () => { pickerDriverId = d.id; renderPickerDrivers(); };
    wrap.append(card);
  });
  $("btn-create-run").disabled = !pickerDriverId;
}

async function createRun() {
  const btn = $("btn-create-run");
  btn.disabled = true;
  btn.textContent = "Planning…";
  try {
    const trip = await api("/api/runs", {
      method: "POST",
      body: JSON.stringify({
        parcelIds: [...selectedParcels],
        driverId: pickerDriverId,
        avoidTolls: $("picker-tolls").checked,
      }),
    });
    selectedParcels.clear();
    $("run-picker").style.display = "none";
    trips[trip.id] = summarize(trip);
    renderTripList();
    switchTab("fleet");
    selectTrip(trip.id, trip);
  } catch (err) {
    alert("Planning failed: " + err.message);
  } finally {
    btn.textContent = "Plan run";
    btn.disabled = false;
  }
}

// ---------------- dispatch agent ----------------

let dispatchPlan = null;

async function autoAssign() {
  const pending = Object.values(parcels).filter(p => p.status === "pending");
  if (!pending.length) return alert("No pending parcels to assign.");
  if (!driversReg.length) return alert("Add drivers in the Registry tab first.");

  const scope = selectedParcels.size ? [...selectedParcels] : null;
  $("run-picker").style.display = "none";
  $("trip-detail").style.display = "none";
  $("dispatch-card").style.display = "flex";
  $("dispatch-actions").style.display = "none";
  $("dispatch-body").innerHTML = `<div class="working"><span class="spinner"></span>
    <span>The dispatch agent is planning ${scope ? scope.length + " selected" : "your"} runs —
    checking fleet eligibility, routes and deadlines. This can take up to a minute…</span></div>`;
  try {
    dispatchPlan = await api("/api/dispatch/propose", {
      method: "POST", body: JSON.stringify({ parcelIds: scope }),
    });
    renderDispatch();
  } catch (err) {
    $("dispatch-body").innerHTML = `<div class="warn-row">Dispatch failed: ${err.message}</div>`;
  }
}

function renderDispatch() {
  const p = dispatchPlan;
  const body = $("dispatch-body");
  body.innerHTML = "";
  if (p.summary) {
    body.insertAdjacentHTML("beforeend", `<div class="dispatch-summary">${p.summary}</div>`);
  }
  p.batches.forEach((b, i) => {
    const card = document.createElement("div");
    card.className = "batch-card";
    card.innerHTML = `
      <div class="batch-head">
        <input type="checkbox" checked data-i="${i}" aria-label="Include ${b.driverName}'s batch">
        <div class="who">${vehIconSvg(b.vehicleType)} ${b.driverName}
          <small>${b.vehicleName} · ${b.vehiclePlate || b.vehicleType} · ${b.parcelIds.length} parcel${b.parcelIds.length === 1 ? "" : "s"}</small>
        </div>
      </div>
      <div class="parcel-chips">${b.parcelNames.map(n => `<span class="parcel-chip">${n}</span>`).join("")}</div>
      <div class="rationale">${b.rationale}</div>
      ${b.riskNotes ? `<div class="risk-note">${iconSvg("alert")} ${b.riskNotes}</div>` : ""}`;
    card.querySelector("input").onchange = updateDispatchButton;
    body.append(card);
  });
  if (p.unassigned.length) {
    body.insertAdjacentHTML("beforeend",
      p.unassigned.map(u => `<div class="unassigned-row"><b>${u.name}</b> — ${u.reason}</div>`).join(""));
  }
  if (p.warnings.length) {
    body.insertAdjacentHTML("beforeend",
      `<div class="warn-row">${p.warnings.join("<br>")}</div>`);
  }
  $("dispatch-actions").style.display = p.batches.length ? "flex" : "none";
  updateDispatchButton();
}

function checkedBatches() {
  return [...document.querySelectorAll("#dispatch-body .batch-head input:checked")]
    .map(cb => dispatchPlan.batches[Number(cb.dataset.i)]);
}

function updateDispatchButton() {
  const n = checkedBatches().length;
  $("btn-dispatch-approve").textContent = `Create ${n} run${n === 1 ? "" : "s"}`;
  $("btn-dispatch-approve").disabled = !n;
}

async function approveDispatch() {
  const batches = checkedBatches().map(b => ({ driverId: b.driverId, parcelIds: b.parcelIds }));
  const btn = $("btn-dispatch-approve");
  btn.disabled = true;
  btn.textContent = "Creating…";
  try {
    const res = await api("/api/dispatch/approve", {
      method: "POST", body: JSON.stringify({ batches }),
    });
    closeDispatch();
    selectedParcels.clear();
    if (res.errors.length) alert(res.errors.map(e => e.message).join("\n"));
    if (res.created.length) {
      switchTab("fleet");
      selectTrip(res.created[0]);
    }
  } catch (err) {
    alert("Approve failed: " + err.message);
    btn.disabled = false;
    updateDispatchButton();
  }
}

function closeDispatch() {
  $("dispatch-card").style.display = "none";
  dispatchPlan = null;
}

// ---------------- registry ----------------

async function loadRegistry() {
  [vehiclesReg, driversReg] = await Promise.all([api("/api/vehicles"), api("/api/drivers")]);
  renderRegistry();
}

function renderRegistry() {
  const vWrap = $("vehicle-list");
  vWrap.innerHTML = vehiclesReg.length ? "" :
    `<div class="empty">${iconSvg("truck", "i-lg")}<b>No vehicles yet</b>Add a vehicle to assign drivers to it.</div>`;
  vehiclesReg.forEach(v => {
    const row = document.createElement("div");
    row.className = "reg-row";
    row.innerHTML = `
      <span class="veh-ico">${vehIconSvg(v.type)}</span>
      <div class="info">
        <div class="label">${v.name} · ${v.plate || "—"}</div>
        <div class="meta">${v.type} · capacity ${v.capacity} · max size ${v.maxParcelSize}</div>
      </div>
      <button class="del" type="button" aria-label="Delete vehicle ${v.name}">${iconSvg("trash")}</button>`;
    row.querySelector(".del").onclick = async () => {
      if (!confirm(`Delete vehicle ${v.name}?`)) return;
      try { await api(`/api/vehicles/${v.id}`, { method: "DELETE" }); await loadRegistry(); }
      catch (err) { alert(err.message); }
    };
    vWrap.append(row);
  });

  const dWrap = $("driver-list");
  dWrap.innerHTML = driversReg.length ? "" :
    `<div class="empty">${iconSvg("user", "i-lg")}<b>No drivers yet</b>Add a driver and link them to a vehicle.</div>`;
  driversReg.forEach(d => {
    const v = vehiclesReg.find(x => x.id === d.vehicleId);
    const row = document.createElement("div");
    row.className = "reg-row";
    const queueUrl = `${location.origin}/static/driver.html?driver=${d.id}`;
    row.innerHTML = `
      <span class="veh-ico">${v ? vehIconSvg(v.type) : iconSvg("user")}</span>
      <div class="info">
        <div class="label">${d.name}</div>
        <div class="meta">${d.phone || "—"} · ${v ? v.name : "no vehicle"} ·
          <a href="${queueUrl}" target="_blank" rel="noopener" style="color:var(--primary)">driver link</a></div>
      </div>
      <button class="del" type="button" aria-label="Delete driver ${d.name}">${iconSvg("trash")}</button>`;
    row.querySelector(".del").onclick = async () => {
      if (!confirm(`Delete driver ${d.name}?`)) return;
      try { await api(`/api/drivers/${d.id}`, { method: "DELETE" }); await loadRegistry(); }
      catch (err) { alert(err.message); }
    };
    dWrap.append(row);
  });
}

async function saveVehicle() {
  const type = document.querySelector("#vf-type button.active").dataset.t;
  try {
    await api("/api/vehicles", {
      method: "POST",
      body: JSON.stringify({
        name: $("vf-name").value.trim() || "Vehicle",
        plate: $("vf-plate").value.trim(),
        type,
        capacity: Number($("vf-capacity").value) || null,
        maxParcelSize: $("vf-maxsize").value,
      }),
    });
    $("vehicle-form").style.display = "none";
    $("vf-name").value = ""; $("vf-plate").value = "";
    await loadRegistry();
  } catch (err) { alert(err.message); }
}

function renderDriverVehicleSelect() {
  $("df-vehicle").innerHTML = vehiclesReg.map(v =>
    `<option value="${v.id}">${v.name} · ${v.type} (${v.plate || v.type})</option>`).join("")
    || `<option value="">— add a vehicle first —</option>`;
}

async function saveDriver() {
  try {
    await api("/api/drivers", {
      method: "POST",
      body: JSON.stringify({
        name: $("df-name").value.trim() || "Driver",
        phone: $("df-phone").value.trim(),
        vehicleId: $("df-vehicle").value || null,
      }),
    });
    $("driver-form").style.display = "none";
    $("df-name").value = ""; $("df-phone").value = "";
    await loadRegistry();
  } catch (err) { alert(err.message); }
}

// ---------------- fleet (runs) ----------------

function summarize(trip) {
  const s = trip.route.orderedStops;
  return {
    id: trip.id,
    status: trip.status,
    label: `${s[0].label} → ${s[s.length - 1].label}`,
    stopCount: s.length,
    createdAt: trip.createdAt,
    totalDistanceMeters: trip.route.totalDistanceMeters,
    totalDurationSeconds: trip.route.totalDurationSeconds,
    deviations: trip.alerts.filter(a => a.type === "deviation").length,
    lastPosition: trip.lastPosition,
    routeVersion: trip.routeVersion,
    vehicleType: trip.vehicleType || "car",
    driverId: trip.driverId,
    vehicleId: trip.vehicleId,
    driverName: (driversReg.find(d => d.id === trip.driverId) || {}).name,
    parcelCount: (trip.parcelIds || []).length,
    deliveredCount: trip.deliveredCount || 0,
  };
}

async function loadTrips() {
  (await api("/api/trips")).forEach(t => { trips[t.id] = t; });
  renderTripList();
  Object.values(trips).forEach(t => updateVehicle(t.id));
}

const TRIP_GROUPS = [
  { key: "active", label: "En route", match: s => s === "active" || s === "deviating" },
  { key: "planned", label: "Scheduled", match: s => s === "planned" },
  { key: "completed", label: "Completed", match: s => s === "completed" },
];

function groupHeader(g, count) {
  const head = document.createElement("div");
  head.className = `trip-group g-${g.key}`;
  head.innerHTML = `<span class="g-dot"></span><span>${g.label}</span>` +
    `<span class="g-line"></span><span class="g-count">${count}</span>`;
  return head;
}

function tripRow(t) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = `trip-row status-${t.status}` + (t.id === selectedId ? " selected" : "");
  if (t.id === selectedId) row.setAttribute("aria-current", "true");
  const drv = t.driverName ? ` · ${t.driverName}` : "";
  const del = t.parcelCount ? ` · ${t.deliveredCount}/${t.parcelCount} delivered` : "";
  row.innerHTML = `
    <span class="veh-ico">${vehIconSvg(t.vehicleType)}</span>
    <div class="info">
      <div class="label">${t.label}</div>
      <div class="meta">${t.id}${drv} · ${fmtKm(t.totalDistanceMeters)}${del}</div>
    </div>
    ${t.deviations ? `<span class="dev-badge">${iconSvg("alert")} ${t.deviations}</span>` : ""}`;
  row.onclick = () => selectTrip(t.id);
  return row;
}

function renderTripList() {
  const list = Object.values(trips).sort((a, b) => b.createdAt - a.createdAt);
  $("fleet-badge").textContent = list.length;

  const wrap = $("trip-list");
  wrap.innerHTML = "";
  if (!list.length) {
    wrap.innerHTML = `<div class="empty">${iconSvg("truck", "i-lg")}
      <b>No delivery runs yet</b>Select pending parcels in the Parcels tab, then assign a driver.</div>`;
    return;
  }
  TRIP_GROUPS.forEach(g => {
    const items = list.filter(t => g.match(t.status));
    if (!items.length) return;
    wrap.append(groupHeader(g, items.length));
    items.forEach(t => wrap.append(tripRow(t)));
  });
  const rest = list.filter(t => !TRIP_GROUPS.some(g => g.match(t.status)));
  if (rest.length) {
    wrap.append(groupHeader({ key: "other", label: "Other" }, rest.length));
    rest.forEach(t => wrap.append(tripRow(t)));
  }
}

async function selectTrip(id, prefetched = null) {
  selectedId = id;
  detail = prefetched || await api(`/api/trips/${id}`);
  trips[id] = summarize(detail);
  $("run-picker").style.display = "none";
  renderTripList();
  renderDetail();
  drawSelectedRoute();
  setHeaderStatus(detail.status);
}

function renderDetail() {
  $("trip-detail").style.display = "flex";
  $("detail-id").textContent = detail.id;
  const r = detail.route;
  const v = vehiclesReg.find(x => x.id === detail.vehicleId);
  const d = driversReg.find(x => x.id === detail.driverId);
  $("run-meta").innerHTML = [
    d ? `<span class="tag">${iconSvg("user")} ${d.name}</span>` : "",
    v ? `<span class="tag">${vehIconSvg(v.type)} ${v.name} · ${v.plate || v.type}</span>` : "",
    detail.avoidTolls ? `<span class="tag">no tolls</span>` : "",
  ].join("");

  $("kpi-dist").textContent = fmtKm(r.totalDistanceMeters);
  const delayMin = Math.round(
    (r.totalDurationSeconds - (r.staticDurationSeconds || r.totalDurationSeconds)) / 60);
  $("kpi-time").innerHTML = fmtMin(r.totalDurationSeconds) +
    (delayMin >= 5 ? ` <small style="color:var(--danger);font-size:11px">+${delayMin} traffic</small>` : "");
  $("kpi-parcels").textContent = (detail.parcelIds || []).length || r.orderedStops.length;
  $("kpi-actual").textContent =
    detail.actualDistanceMeters ? fmtKm(detail.actualDistanceMeters) : "–";
  $("kpi-devs").textContent =
    detail.alerts.filter(a => a.type === "deviation").length;
  $("kpi-delivered").textContent = detail.deliveredCount || 0;

  renderManifest();
  $("briefing").textContent = r.briefing;

  const url = detail.driverId
    ? `${location.origin}/static/driver.html?driver=${detail.driverId}`
    : `${location.origin}/static/driver.html?trip=${detail.id}`;
  const link = $("driver-url");
  link.href = url;
  link.textContent = url;
  $("btn-copy").onclick = async () => {
    try {
      await navigator.clipboard.writeText(url);
      $("btn-copy").textContent = "Copied";
    } catch (_) {
      getSelection().selectAllChildren(link);
      $("btn-copy").textContent = "Press Ctrl+C";
    }
    setTimeout(() => { $("btn-copy").textContent = "Copy link"; }, 2000);
  };

  updateDetailButtons();
  $("alerts").innerHTML = "";
  [...detail.alerts].reverse().forEach(a => addAlertRow(a));
}

function renderManifest() {
  const etas = Object.fromEntries(
    (detail.plannedEtaPerStop || []).map(e => [e.parcelId, e]));
  const rows = detail.route.orderedStops
    .filter(s => s.parcelId)
    .map(s => {
      const p = parcels[s.parcelId];
      const e = etas[s.parcelId];
      const delivered = p && p.status === "delivered";
      const status = delivered
        ? `<span class="done-tag">${iconSvg("check")} ${p.deadlineMissed ? "late" : "done"}</span>`
        : (e && e.atRisk ? `<span class="risk-tag">at risk</span>` : "");
      const eta = e ? new Date(e.eta * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "–";
      return `<tr>
        <td class="leg-route">${s.label}<small>${p ? p.destination.label : ""}</small></td>
        <td class="num">${eta}</td>
        <td class="num">${status}</td>
      </tr>`;
    });
  $("manifest").innerHTML = rows.join("") ||
    `<tr><td class="leg-route">No parcels on this trip</td></tr>`;
}

function drawSelectedRoute() {
  if (routeLine) routeLine.setMap(null);
  selStopMarkers.forEach(m => m.setMap(null));
  trafficLines.forEach(l => l.setMap(null));
  const path = decodedPath(detail);
  routeLine = drawRoute(map, path);
  trafficLines = drawTraffic(map, path, detail.route.traffic);
  selStopMarkers = detail.route.orderedStops.map((s, i) =>
    stopMarker(map, s, i, detail.route.orderedStops.length));
  const bounds = new google.maps.LatLngBounds();
  path.forEach(p => bounds.extend(p));
  map.fitBounds(bounds, { top: 80, bottom: 60, left: 60, right: 410 });
}

function deselectTrip() {
  selectedId = null;
  detail = null;
  if (routeLine) { routeLine.setMap(null); routeLine = null; }
  trafficLines.forEach(l => l.setMap(null));
  trafficLines = [];
  selStopMarkers.forEach(m => m.setMap(null));
  selStopMarkers = [];
  $("trip-detail").style.display = "none";
  $("trip-status").className = "";
  $("trip-status-text").textContent = "No run";
  renderTripList();
}

async function deleteSelected() {
  if (!detail || !confirm(`Delete run ${detail.id}? Its parcels return to pending.`)) return;
  const id = detail.id;
  try {
    await api(`/api/trips/${id}`, { method: "DELETE" });
    removeTripLocal(id);
  } catch (err) {
    alert(err.message);
  }
}

async function clearAllTrips() {
  if (!confirm("Delete all runs? Live runs (driver en route) are kept. This cannot be undone.")) return;
  const res = await api("/api/trips", { method: "DELETE" });
  Object.keys(trips)
    .filter(id => !LIVE.includes(trips[id].status))
    .forEach(removeTripLocal);
  if (res.skippedLive) alert(`${res.skippedLive} live run(s) were kept.`);
}

function removeTripLocal(id) {
  delete trips[id];
  if (vehicles[id]) { vehicles[id].setMap(null); delete vehicles[id]; }
  if (id === selectedId) deselectTrip();
  renderTripList();
}

function updateVehicle(id) {
  const t = trips[id];
  const active = t && t.lastPosition && LIVE.includes(t.status);
  if (!active) {
    if (vehicles[id]) { vehicles[id].setMap(null); delete vehicles[id]; }
    return;
  }
  if (vehicles[id] && vehicles[id]._veh !== t.vehicleType) {
    vehicles[id].setMap(null);
    delete vehicles[id];
  }
  if (!vehicles[id]) {
    vehicles[id] = fleetVehicleMarker(map, t.lastPosition, t.vehicleType);
    vehicles[id]._veh = t.vehicleType;
    vehicles[id].addListener("click", () => { switchTab("fleet"); selectTrip(id); });
  } else {
    const prev = vehicles[id].getPosition();
    const next = new google.maps.LatLng(t.lastPosition);
    if (google.maps.geometry.spherical.computeDistanceBetween(prev, next) > 2) {
      setFleetVehicleBearing(vehicles[id], t.vehicleType,
        google.maps.geometry.spherical.computeHeading(prev, next));
    }
    vehicles[id].setPosition(t.lastPosition);
  }
}

function updateDetailButtons() {
  const live = detail && LIVE.includes(detail.status);
  $("btn-delete").disabled = live;
  $("btn-delete").title = live ? "Run is live — the driver must end it first" : "";
}

// ---------------- live stream ----------------

function openStream() {
  const es = new EventSource("/api/stream");

  es.addEventListener("parcel", e => {
    const { parcel, deleted } = JSON.parse(e.data);
    if (deleted) delete parcels[parcel.id];
    else parcels[parcel.id] = parcel;
    renderParcels();
    if (detail && (detail.parcelIds || []).includes(parcel.id)) {
      renderManifest();
      $("kpi-delivered").textContent =
        (detail.parcelIds || []).filter(id => parcels[id]?.status === "delivered").length;
    }
  });

  es.addEventListener("trip", e => {
    const s = JSON.parse(e.data);
    trips[s.tripId] = { ...trips[s.tripId], ...s, id: s.tripId };
    renderTripList();
  });

  es.addEventListener("removed", e => {
    removeTripLocal(JSON.parse(e.data).tripId);
  });

  es.addEventListener("position", e => {
    const p = JSON.parse(e.data);
    const t = trips[p.tripId];
    if (!t) return;
    t.lastPosition = p;
    updateVehicle(p.tripId);
  });

  es.addEventListener("status", e => {
    const s = JSON.parse(e.data);
    const t = trips[s.tripId];
    if (!t) return;
    t.status = s.status;
    renderTripList();
    updateVehicle(s.tripId);
    if (s.tripId === selectedId) {
      if (detail) detail.status = s.status;
      setHeaderStatus(s.status);
      updateDetailButtons();
    }
  });

  es.addEventListener("alert", e => {
    const a = JSON.parse(e.data);
    const t = trips[a.tripId];
    if (t && a.type === "deviation") { t.deviations = (t.deviations || 0) + 1; renderTripList(); }
    if (t && a.type === "delivered") { t.deliveredCount = (t.deliveredCount || 0) + 1; renderTripList(); }
    if (a.tripId === selectedId && detail) {
      detail.alerts.push(a);
      if (a.type === "delivered") detail.deliveredCount = (detail.deliveredCount || 0) + 1;
      addAlertRow(a, true);
      $("kpi-devs").textContent = detail.alerts.filter(x => x.type === "deviation").length;
    }
    if (a.type === "deviation") {
      flashBanner(`${t ? t.label : a.tripId} — ${a.message}`);
    }
  });

  es.addEventListener("reroute", async e => {
    const r = JSON.parse(e.data);
    if (trips[r.tripId]) trips[r.tripId].routeVersion = r.routeVersion;
    if (r.tripId === selectedId) {
      detail = await api(`/api/trips/${r.tripId}`);
      trips[r.tripId] = summarize(detail);
      renderTripList();
      renderDetail();
      drawSelectedRoute();
    }
  });
}

const ALERT_STYLE = {
  back_on_route: "ok", completed: "ok", delivered: "ok",
  reroute: "info", started: "info", ended: "info",
};

const ALERT_ICON = { ok: "check", info: "info" };

function addAlertRow(a, prepend = false) {
  const row = document.createElement("div");
  const style = ALERT_STYLE[a.type] || "";
  row.className = "alert-row " + style;
  row.innerHTML = `${iconSvg(ALERT_ICON[style] || "alert")}<div><time>${fmtClock(a.ts)}</time>${a.message}</div>`;
  prepend ? $("alerts").prepend(row) : $("alerts").append(row);
}

function flashBanner(msg) {
  const b = $("alert-banner");
  b.innerHTML = iconSvg("alert", "i-lg") + "<span></span>";
  b.querySelector("span").textContent = msg;
  b.style.display = "flex";
  clearTimeout(b._t);
  b._t = setTimeout(() => { b.style.display = "none"; }, 6000);
}

function setHeaderStatus(status) {
  const el = $("trip-status");
  el.className = "status-" + status;
  $("trip-status-text").textContent =
    { planned: "Scheduled", active: "En route", deviating: "Deviation", completed: "Completed" }[status] || status;
}
