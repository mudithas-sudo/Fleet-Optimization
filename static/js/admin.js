// FleetOps Delivery Console.
//
// Parcels tab: manage deliverables (manual add, CSV/Excel import), select
// pending parcels → assign an eligible driver → a run is planned by the ADK
// agent from the depot. Runs tab: live fleet monitoring (map, manifest,
// alerts). Registry tab: vehicles and drivers.
// A global SSE stream (/api/stream) carries trip AND parcel events.

let map;
let activeTab = "dashboard";
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
let pfKind = "delivery";    // delivery | pickup (a customer's pickup request)
let pfSize = "small";
let pfPrio = "standard";
let editingDriverId = null;
let pfDest = null;         // {lat, lng, label}
let pfMarker = null;
let pfPickup = null;       // {lat, lng, label}
let pfPickupMarker = null;
let pfMapTarget = "dest";  // which field a map click fills: "dest" | "pickup"

// datetime-local string -> epoch seconds (or null)
const toEpoch = v => (v ? new Date(v).getTime() / 1000 : null);
let depotEditing = false;
// cargo space L, W, H (cm) + payload (kg) per vehicle type — mirrors CARGO_DEFAULTS in delivery.py
const CARGO_DEFAULTS = {
  bike: [40, 30, 30, 10], car: [100, 50, 45, 200],
  van: [300, 170, 130, 1000], truck: [500, 200, 200, 5000],
};

// picker state
let pickerDriverId = null;

const $ = id => document.getElementById(id);

init();

async function init() {
  initTheme();
  await loadMe();            // signed in? (redirects to the sign-in page if not)
  await loadMaps();
  map = new google.maps.Map($("map"), {
    center: { lat: 6.9271, lng: 79.8612 }, zoom: 12,
    styles: mapThemeStyles(),
    disableDefaultUI: true, zoomControl: true, clickableIcons: false,
  });
  themeAwareMap(map, () => routeLine);

  map.addListener("click", e => onMapClick(e.latLng.lat(), e.latLng.lng()));

  ["dashboard", "parcels", "fleet", "attention", "registry"].forEach(t => {
    $(`tab-btn-${t}`).onclick = () => switchTab(t);
  });

  // fill the vehicle-type picker glyphs with SVG icons
  document.querySelectorAll("#vf-type [data-veh]").forEach(el => {
    el.innerHTML = vehIconSvg(el.dataset.veh);
  });

  // depot chip is a role=button toggle — Enter/Space activate, Esc cancels
  $("depot-chip").addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleDepotEdit(); }
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && depotEditing) cancelDepotEdit();
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
  document.querySelectorAll("#pf-prio button").forEach(b => {
    b.onclick = () => {
      pfPrio = b.dataset.p;
      document.querySelectorAll("#pf-prio button").forEach(x => {
        const on = x === b;
        x.classList.toggle("active", on);
        x.setAttribute("aria-pressed", String(on));
      });
    };
  });
  setupDestSearch();
  setupPickupSearch();
  $("pf-has-pickup").onchange = onPickupToggle;
  document.querySelectorAll("#pf-kind button").forEach(b => { b.onclick = () => setPfKind(b.dataset.k); });
  $("pf-dest").addEventListener("focus", () => armMapTarget("dest"));
  $("pf-pickup").addEventListener("focus", () => armMapTarget("pickup"));
  $("pf-dest-pin").onclick = () => armMapTarget("dest", true);
  $("pf-pickup-pin").onclick = () => armMapTarget("pickup", true);

  // progressive disclosure for the optional details
  $("pf-more-btn").onclick = () => {
    const open = $("pf-more").hidden;
    $("pf-more").hidden = !open;
    $("pf-more-btn").setAttribute("aria-expanded", String(open));
  };

  // blur validation — only surface an error once the user has left a field
  $("pf-name").addEventListener("blur", () => {
    if (!$("pf-name").value.trim()) pfErr("pf-name-err", "Give the parcel a name.");
  });
  $("pf-weight").addEventListener("blur", validateWeightField);
  $("pf-phone").addEventListener("blur", validatePhoneField);
  $("depot-chip").onclick = toggleDepotEdit;
  $("btn-plan-run").onclick = openPicker;
  $("btn-add-to-run").onclick = addSelectedToRun;
  setupDragAndDrop();
  setupReassign();
  $("btn-auto").onclick = () => autoAssign("rules");
  $("btn-auto-ai").onclick = () => autoAssign("ai");
  $("btn-close-dispatch").onclick = closeDispatch;
  $("btn-dispatch-discard").onclick = closeDispatch;
  $("btn-dispatch-approve").onclick = approveDispatch;
  $("btn-close-picker").onclick = () => { $("run-picker").style.display = "none"; };
  $("btn-create-run").onclick = createRun;
  setupImport();
  setupDepotForm();
  setupOverview();
  setupDashboard();
  setupPalette();
  setupParcelDetail();
  setupReports();
  setupPassword();
  setupUsers();

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
      const c = CARGO_DEFAULTS[b.dataset.t];
      $("vf-cargo-l").value = c[0]; $("vf-cargo-w").value = c[1]; $("vf-cargo-h").value = c[2];
      $("vf-payload").value = c[3];
    };
  });
  $("btn-add-driver").onclick = () => openDriverForm(null);
  $("df-cancel").onclick = () => { $("driver-form").style.display = "none"; };
  $("df-save").onclick = saveDriver;

  await Promise.all([loadDepot(), loadParcels(), loadRegistry(), loadTrips(), loadUsers()]);
  openStream();
  switchTab("dashboard");
  if (typeof renderHero === "function") renderHero();
  setInterval(renderParcels, 30000);   // deadline countdowns
}

const PAGES = ["dashboard", "parcels", "fleet", "attention", "registry"];

function switchTab(tab) {
  if (!PAGES.includes(tab)) tab = "dashboard";
  activeTab = tab;
  document.body.dataset.page = tab;
  PAGES.forEach(t => {
    const btn = $(`tab-btn-${t}`);
    if (btn) { if (t === tab) btn.setAttribute("aria-current", "page"); else btn.removeAttribute("aria-current"); }
    const section = $(`tab-${t}`);
    if (section) section.hidden = t !== tab;
  });
  updateRunFooter();
  if (typeof onPageChange === "function") onPageChange(tab);
}

function onMapClick(lat, lng) {
  if (depotEditing) {
    setDepotDraft({ lat, lng, label: $("df-depot-name").value.trim() || "Dropped pin" });
    api("/api/revgeocode?lat=" + lat + "&lng=" + lng).then(r => {
      if (r.label) setDepotDraft({ lat, lng, label: r.label });
    }).catch(() => {});
    return;
  }
  if (activeTab === "parcels" && $("parcel-form").style.display !== "none") {
    const set = (pfMapTarget === "pickup" && $("pf-has-pickup").checked)
      ? setPfPickup : setPfDest;
    set({ lat, lng, label: "Dropped pin" });
    disarmMapTargets();
    api(`/api/revgeocode?lat=${lat}&lng=${lng}`)
      .then(r => { if (r.label) set({ lat, lng, label: r.label }); })
      .catch(() => {});
  }
}

// ---------------- depot ----------------
// The admin-defined start point every delivery run begins from. Edits are a
// draft (search / map click / name) until "Save start point".

let depotMarker = null;
let depotDraft = null;

async function loadDepot() {
  depot = await api("/api/settings/depot");
  $("depot-label").textContent = depot.label;
  drawDepotMarker(depot);
  if (typeof renderHero === "function") renderHero();
}

function drawDepotMarker(d) {
  try {
    if (depotMarker) depotMarker.setMap(null);
    depotMarker = d ? stopMarker(map, d, 0, 1, "depot") : null;
  } catch (e) { console.warn("depot marker failed", e); }
}

function setDepotDraft(d) {
  depotDraft = d;
  $("df-depot-name").value = d.label;
  $("df-depot-coords").textContent = `${d.lat.toFixed(5)}, ${d.lng.toFixed(5)}`;
  drawDepotMarker(d);
}

function depotErr(msg) {
  const el = $("df-depot-err");
  el.textContent = msg;
  el.hidden = !msg;
}

function toggleDepotEdit() {
  depotEditing ? cancelDepotEdit() : startDepotEdit();
}

function startDepotEdit() {
  depotEditing = true;
  $("depot-chip").setAttribute("aria-pressed", "true");
  $("depot-action").textContent = " · cancel";
  $("depot-form").style.display = "flex";
  depotErr("");
  setDepotDraft({ ...depot });
  $("df-depot-search").focus();
}

function closeDepotForm() {
  depotEditing = false;
  depotDraft = null;
  $("depot-chip").setAttribute("aria-pressed", "false");
  $("depot-action").textContent = " · edit";
  $("depot-form").style.display = "none";
  $("depot-label").textContent = depot.label;
}

function cancelDepotEdit() {
  closeDepotForm();
  drawDepotMarker(depot);
}

async function saveDepot() {
  const name = $("df-depot-name").value.trim();
  if (!depotDraft) return;
  if (!name) { depotErr("Give the start point a name."); return; }
  const next = { lat: depotDraft.lat, lng: depotDraft.lng, label: name };
  try {
    await api("/api/settings/depot", { method: "PUT", body: JSON.stringify(next) });
  } catch (err) {
    depotErr("Couldn’t save the start point: " + err.message);
    return;
  }
  depot = next;
  closeDepotForm();
  drawDepotMarker(depot);
  if (typeof renderHero === "function") renderHero();
}

function setupDepotForm() {
  $("df-depot-save").onclick = saveDepot;
  $("df-depot-cancel").onclick = cancelDepotEdit;
  $("df-depot-search").addEventListener("keydown", async e => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const input = $("df-depot-search");
    const q = input.value.trim();
    if (!q) return;
    input.classList.add("busy");
    depotErr("");
    try {
      const r = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
      setDepotDraft(r);
      try { map.panTo({ lat: r.lat, lng: r.lng }); } catch (_) {}
      input.value = "";
    } catch (_) {
      depotErr(`Couldn’t find “${q}”. Try a more specific name, or click the map.`);
    } finally {
      input.classList.remove("busy");
    }
  });
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

// Parcels are shown in status groups so only plannable ones can be selected for
// a run — assigned/in-transit/picked-up parcels are already committed to a
// driver and delivered ones are history. awaiting_redelivery (collected then
// the run was aborted) is plannable again.
const PLANNABLE = ["pending", "awaiting_redelivery"];
const PARCEL_GROUPS = [
  { key: "review",   label: "Failed — needs review", dot: "g-active",
    match: s => s === "failed" },
  { key: "pending",  label: "Pending",          dot: "g-planned",
    match: s => PLANNABLE.includes(s) },
  { key: "transit",  label: "Out for delivery", dot: "g-active",
    match: s => ["assigned", "in_transit", "picked_up"].includes(s) },
  { key: "done",     label: "Delivered",        dot: "",
    match: s => s === "delivered" },
  { key: "returned", label: "Returned to sender", dot: "",
    match: s => s === "returned" },
];

function parcelGroupHead(label, count, dotMod) {
  const head = document.createElement("div");
  head.className = "trip-group " + dotMod;
  head.innerHTML = `<span class="g-dot"></span><span>${label}</span>` +
    `<span class="g-line"></span><span class="g-count">${count}</span>`;
  return head;
}

function parcelRow(p) {
  const row = document.createElement("div");
  row.className = "parcel-row";
  const dl = fmtDeadline(p);
  const plannable = PLANNABLE.includes(p.status);
  const statusText = p.status.replace(/_/g, " ");
  const chips = [
    p.status === "delivered" && p.deadlineMissed ? `<span class="late-tag">late</span>` : "",
    p.requestType === "pickup" ? `<span class="p-chip" title="Customer pickup request — collected, then brought to the hub">pickup</span>` : "",
    p.requestType === "pickup" && p.earlyOk ? `<span class="p-chip" title="Ready — the customer confirmed early collection">early ok</span>` : "",
    p.holdUntil && p.holdUntil > Date.now() / 1000 ? `<span class="p-chip" title="Rescheduled — held until ${new Date(p.holdUntil * 1000).toLocaleDateString()}">held</span>` : "",
    p.requestType !== "pickup" && p.pickup && p.status !== "awaiting_redelivery" ? `<span class="p-chip" title="Collect at ${p.pickup.label}">P</span>` : "",
    p.priority === "express" ? `<span class="p-chip fragile" title="Express — scheduled first">express</span>` : "",
    p.priority === "priority" ? `<span class="p-chip" title="Priority — scheduled ahead of standard parcels">priority</span>` : "",
    p.windowStart && p.windowStart > Date.now() / 1000 ? `<span class="p-chip" title="Not before ${new Date(p.windowStart * 1000).toLocaleString()}">window</span>` : "",
    p.fragile ? `<span class="p-chip fragile" title="Fragile — handle with care">fragile</span>` : "",
    p.status === "awaiting_redelivery" ? `<span class="redeliv-chip" title="Collected earlier, run aborted — back at the hub">redeliv</span>` : "",
  ].join(" ");
  const where = p.requestType === "pickup"
    ? `Collect · ${p.pickup.label}${p.pickup.earliest ? " · cut-off " + new Date(p.pickup.earliest * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}`
    : p.pickup && p.status !== "awaiting_redelivery"
      ? `Collect · ${p.pickup.label} → ${p.destination.label}`
      : p.destination.label;
  const dims = p.dimensionsCm ? `${p.dimensionsCm.l}×${p.dimensionsCm.w}×${p.dimensionsCm.h} cm` : "";
  const extra = [
    p.trackingNumber || "",
    p.receiverName || "",
    dims,
    p.weightKg ? `${(+p.weightKg).toLocaleString()} kg` : "",
    p.recipientPhone || "",
  ].filter(Boolean).join(" · ");
  row.innerHTML = `
    ${plannable
      ? `<input type="checkbox" aria-label="Select ${p.name}" ${selectedParcels.has(p.id) ? "checked" : ""}>`
      : `<span class="p-dot ${p.status}" title="${statusText}"></span>`}
    <div class="info">
      <div class="label">${p.name} <span class="size-chip">${p.size[0]}</span> ${chips}</div>
      <div class="meta">${where} · ${p.type} · ${statusText}${extra ? " · " + extra : ""}</div>
    </div>
    <div class="deadline ${dl.cls}">${dl.cls ? iconSvg("clock") : ""}${dl.text}</div>
    ${plannable ? `<button class="del" type="button" aria-label="Delete ${p.name}">${iconSvg("trash")}</button>` : ""}`;
  if (plannable || ["assigned", "in_transit", "picked_up"].includes(p.status)) makeDraggable(row, p.id);
  const info = row.querySelector(".info");
  info.setAttribute("role", "button");
  info.tabIndex = 0;
  info.setAttribute("aria-label", `Open details for ${p.name}`);
  info.onclick = () => openParcelDetail(p.id);
  info.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openParcelDetail(p.id); } };
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
  return row;
}

// list order: soonest deadline by default; or priority first / newest / receiver A-Z
function parcelComparator(groupKey) {
  const rank = { express: 0, priority: 1, standard: 2 };
  if (groupKey === "done") return (a, b) => (b.deliveredAt || 0) - (a.deliveredAt || 0);
  switch (FILTERS.sort) {
    case "priority": return (a, b) => (rank[a.priority || "standard"] - rank[b.priority || "standard"]) || a.deadline - b.deadline;
    case "newest": return (a, b) => (b.createdAt || 0) - (a.createdAt || 0);
    case "receiver": return (a, b) => (a.receiverName || a.name).localeCompare(b.receiverName || b.name);
    default: return (a, b) => a.deadline - b.deadline;
  }
}

function renderParcels() {
  refreshOverview();
  const everything = Object.values(parcels);
  const all = everything.filter(parcelMatchesFilters);
  renderFilterNote(all.length, everything.length);
  const overdue = everything.filter(p => p.deadlineMissed && p.status !== "delivered").length;
  $("parcel-badge").hidden = !overdue;
  $("parcel-badge").textContent = overdue;

  const wrap = $("parcel-list");
  wrap.innerHTML = "";
  if (!all.length) {
    wrap.innerHTML = everything.length
      ? `<div class="empty">${iconSvg("pin", "i-lg")}<b>No parcels match</b>Try a different search, or clear the filters.</div>`
      : `<div class="empty">${iconSvg("depot", "i-lg")}
      <b>No parcels yet</b>Add one manually or import a spreadsheet to get started.</div>`;
    updateRunFooter();
    return;
  }

  PARCEL_GROUPS.forEach(g => {
    const items = all.filter(p => g.match(p.status)).sort(parcelComparator(g.key));
    if (!items.length) return;
    wrap.append(parcelGroupHead(g.label, items.length, g.dot));
    items.forEach(p => wrap.append(parcelRow(p)));
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
    if (!parcels[id] || !PLANNABLE.includes(parcels[id].status)) selectedParcels.delete(id);
  });
  const n = selectedParcels.size;
  $("run-footer").style.display = activeTab === "parcels" && n ? "block" : "none";
  $("btn-plan-run").textContent = `Plan delivery run (${n})`;
  const open = selectedId && trips[selectedId] && trips[selectedId].status !== "completed" ? trips[selectedId] : null;
  $("btn-add-to-run").hidden = !open;
  if (open) $("btn-add-to-run").textContent = `Add ${n} to run ${open.id}`;
}

// ---- inline validation helpers ----

function pfErr(id, msg) {
  const el = $(id);
  if (!el) return;
  el.textContent = msg || "";
  el.hidden = !msg;
  const field = el.id.replace(/-err$|-win-err$/, "");
  const input = $(field);
  if (input && input.tagName !== "DIV") input.classList.toggle("invalid", !!msg);
}

const PF_ERR_IDS = ["pf-window-err", "pf-name-err", "pf-dest-err", "pf-weight-err", "pf-dim-err", "pf-phone-err",
  "pf-pickup-err", "pf-pickup-win-err", "pf-form-err"];

function pfClearErrors() { PF_ERR_IDS.forEach(id => pfErr(id, "")); }

function validateWeightField() {
  const raw = $("pf-weight").value.trim();
  if (!raw) { pfErr("pf-weight-err", ""); return true; }
  const w = Number(raw);
  if (!isFinite(w) || w <= 0) { pfErr("pf-weight-err", "Weight must be a positive number."); return false; }
  if (w > 2000) { pfErr("pf-weight-err", "That looks too heavy — double-check the value."); return false; }
  pfErr("pf-weight-err", "");
  return true;
}

function validatePhoneField() {
  const raw = $("pf-phone").value.trim();
  if (!raw) { pfErr("pf-phone-err", ""); return true; }
  if (raw.replace(/\D/g, "").length < 7) { pfErr("pf-phone-err", "Enter a valid phone number."); return false; }
  pfErr("pf-phone-err", "");
  return true;
}

// package size: blank, or all three edges positive
function readDimensions() {
  const vals = ["l", "w", "h"].map(k => $(`pf-dim-${k}`).value.trim());
  if (vals.every(v => !v)) return { dims: null, ok: true };
  const nums = vals.map(Number);
  if (vals.some(v => !v) || nums.some(n => !isFinite(n) || n <= 0 || n > 1000)) {
    pfErr("pf-dim-err", "Give length, width and height (cm), or leave all three blank.");
    return { dims: null, ok: false };
  }
  pfErr("pf-dim-err", "");
  return { dims: { l: nums[0], w: nums[1], h: nums[2] }, ok: true };
}

function validateParcelForm() {
  pfClearErrors();
  let ok = true;
  if (!readDimensions().ok) ok = false;
  if (!$("pf-name").value.trim()) { pfErr("pf-name-err", "Give the parcel a name."); ok = false; }
  if (pfKind === "delivery" && !pfDest) { pfErr("pf-dest-err", "Search a place or use “Pin on map”."); ok = false; }
  if (!validateWeightField()) ok = false;
  if (!validatePhoneField()) ok = false;
  if (pfKind === "pickup") {
    if (!pfPickup) { pfErr("pf-pickup-err", "Set the customer’s address."); ok = false; }
    const from = toEpoch($("pf-pickup-from").value), until = toEpoch($("pf-pickup-until").value);
    if (!from) { pfErr("pf-pickup-win-err", "Enter the customer’s cut-off time."); ok = false; }
    else if (until && from > until) { pfErr("pf-pickup-win-err", "The collect-by time is before the cut-off."); ok = false; }
    else if ((until || from) < Date.now() / 1000) { pfErr("pf-pickup-win-err", "That time is in the past."); ok = false; }
    return ok;
  }
  if ($("pf-has-pickup").checked) {
    if (!pfPickup) { pfErr("pf-pickup-err", "Set the pickup location, or turn this off."); ok = false; }
    const from = toEpoch($("pf-pickup-from").value), until = toEpoch($("pf-pickup-until").value);
    if (from && until && from > until) {
      pfErr("pf-pickup-win-err", "“Collect from” is later than “Collect until”."); ok = false;
    }
  }
  const dl = toEpoch($("pf-deadline").value);
  if (dl && dl < Date.now() / 1000) { pfErr("pf-form-err", "The delivery deadline is in the past."); ok = false; }
  const win = toEpoch($("pf-window").value);
  if (win && dl && win > dl) { pfErr("pf-window-err", "“Not before” is later than “Deliver by”."); ok = false; }
  return ok;
}

// ---- "Pin on map" arming ----

function armMapTarget(which, focusHint) {
  if (which === "pickup" && !$("pf-has-pickup").checked) which = "dest";
  pfMapTarget = which;
  $("pf-dest-pin").setAttribute("aria-pressed", String(which === "dest" && !!focusHint));
  $("pf-pickup-pin").setAttribute("aria-pressed", String(which === "pickup" && !!focusHint));
  if (focusHint) {
    const hint = which === "pickup" ? $("pf-pickup-label") : $("pf-dest-label");
    if (hint && !(which === "pickup" ? pfPickup : pfDest)) {
      hint.textContent = "Click the location on the map";
    }
  }
}

function disarmMapTargets() {
  $("pf-dest-pin").setAttribute("aria-pressed", "false");
  $("pf-pickup-pin").setAttribute("aria-pressed", "false");
}

// ---- form open/close ----

function toggleParcelForm(show) {
  $("parcel-form").style.display = show ? "flex" : "none";
  if (show) {
    pfClearErrors();
    return;
  }
  pfDest = pfPickup = null;
  pfMapTarget = "dest";
  pfSize = "small";
  pfPrio = "standard";
  document.querySelectorAll("#pf-prio button").forEach(b => {
    const on = b.dataset.p === "standard";
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", String(on));
  });
  if (pfMarker) { pfMarker.setMap(null); pfMarker = null; }
  if (pfPickupMarker) { pfPickupMarker.setMap(null); pfPickupMarker = null; }
  document.querySelectorAll("#pf-size button").forEach(b => {
    const on = b.dataset.s === "small";
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", String(on));
  });
  ["pf-name", "pf-tracking", "pf-receiver", "pf-dim-l", "pf-dim-w", "pf-dim-h",
    "pf-window", "pf-email", "pf-type", "pf-dest", "pf-weight", "pf-phone", "pf-notes",
    "pf-deadline", "pf-pickup", "pf-pickup-from", "pf-pickup-until"].forEach(id => { $(id).value = ""; });
  $("pf-fragile").checked = false;
  $("pf-early").checked = false;
  $("pf-dest-label").textContent = "No destination set";
  $("pf-pickup-label").textContent = "No pickup set";
  $("pf-has-pickup").checked = false;
  $("pf-pickup-fields").hidden = true;
  $("pf-more").hidden = true;
  $("pf-more-btn").setAttribute("aria-expanded", "false");
  setPfKind("delivery");
  disarmMapTargets();
  pfClearErrors();
  const save = $("pf-save");
  save.disabled = false;
  save.textContent = "Add parcel";
}

// A pickup request is collected at the customer's address and brought back to
// the hub, so the destination and delivery times go away and the pickup fields
// carry the customer's cut-off (collect from) and the collect-by time.
function setPfKind(kind) {
  pfKind = kind;
  const pickup = kind === "pickup";
  document.querySelectorAll("#pf-kind button").forEach(b => {
    const on = b.dataset.k === kind;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", String(on));
  });
  $("pf-kind-hint").hidden = !pickup;
  ["pf-dest-field", "pf-pickup-toggle", "pf-deliver-times", "pf-window-err"].forEach(id => { $(id).hidden = pickup; });
  $("pf-early-row").hidden = !pickup;
  $("pf-name-lbl").innerHTML = pickup ? `Shipment name <span class="req">(required)</span>` : `Parcel name <span class="req">(required)</span>`;
  $("pf-pickup-fields").hidden = !pickup && !$("pf-has-pickup").checked;
  $("pf-from-lbl").innerHTML = pickup ? `Customer cut-off <span class="req">(required)</span>` : `Collect from <span class="opt">(optional)</span>`;
  $("pf-until-lbl").innerHTML = pickup ? `Collect by <span class="opt">(default: 2 h after the cut-off)</span>` : `Collect until <span class="opt">(optional)</span>`;
  $("pf-save").textContent = pickup ? "Add pickup request" : "Add parcel";
  if (pickup) {
    $("pf-has-pickup").checked = true;
    armMapTarget("pickup", true);
  } else if ($("pf-has-pickup").checked) {
    $("pf-has-pickup").checked = false;
    onPickupToggle();
  }
}

function onPickupToggle() {
  const on = $("pf-has-pickup").checked;
  $("pf-pickup-fields").hidden = !on;
  if (on) {
    armMapTarget("pickup", true);
  } else {
    pfMapTarget = "dest";
    pfPickup = null;
    if (pfPickupMarker) { pfPickupMarker.setMap(null); pfPickupMarker = null; }
    $("pf-pickup").value = "";
    $("pf-pickup-label").textContent = "No pickup set";
    pfErr("pf-pickup-err", "");
    pfErr("pf-pickup-win-err", "");
  }
}

function _pinLabel(el, text) {
  el.innerHTML = "";
  el.append(
    Object.assign(document.createElement("span"), {
      innerHTML: iconSvg("pin"), style: "vertical-align:-2px;margin-right:4px",
    }),
    document.createTextNode(text));
}

// A broken Maps JS instance must never block location capture: set state and
// clear the error FIRST, then attempt the (throwable) marker + pan.
function setPfDest(dest) {
  pfDest = dest;
  _pinLabel($("pf-dest-label"), dest.label);
  pfErr("pf-dest-err", "");
  disarmMapTargets();
  try {
    if (pfMarker) pfMarker.setMap(null);
    pfMarker = stopMarker(map, dest, 1, 3, "delivery");
  } catch (e) { console.warn("dest marker failed", e); }
}

function setPfPickup(dest) {
  pfPickup = dest;
  _pinLabel($("pf-pickup-label"), dest.label);
  pfErr("pf-pickup-err", "");
  disarmMapTargets();
  try {
    if (pfPickupMarker) pfPickupMarker.setMap(null);
    pfPickupMarker = stopMarker(map, dest, 1, 3, "pickup");
  } catch (e) { console.warn("pickup marker failed", e); }
}

function _wireSearch(inputId, setter, errId) {
  $(inputId).addEventListener("keydown", async e => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const input = $(inputId);
    const q = input.value.trim();
    if (!q) return;
    input.classList.add("busy");
    pfErr(errId, "");
    try {
      const r = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
      setter(r);
      try { map.panTo({ lat: r.lat, lng: r.lng }); } catch (_) {}
      input.value = "";
    } catch (_) {
      pfErr(errId, `Couldn’t find “${q}”. Try a more specific name.`);
    } finally {
      input.classList.remove("busy");
    }
  });
}

function setupDestSearch() {
  $("pf-name").addEventListener("input", () => {
    if ($("pf-name").value.trim()) pfErr("pf-name-err", "");
  });
  _wireSearch("pf-dest", setPfDest, "pf-dest-err");
}

function setupPickupSearch() {
  _wireSearch("pf-pickup", setPfPickup, "pf-pickup-err");
}

async function saveParcel() {
  if (!validateParcelForm()) {
    const bad = $("parcel-form").querySelector(".field-err:not([hidden])");
    if (bad) bad.scrollIntoView({ block: "nearest", behavior: "smooth" });
    return;
  }
  const save = $("pf-save");
  const label = save.textContent;
  save.disabled = true;
  save.textContent = "Adding…";

  const request = pfKind === "pickup";
  const deadline = request ? null : (toEpoch($("pf-deadline").value) || Date.now() / 1000 + 4 * 3600);
  let pickup = null;
  if ($("pf-has-pickup").checked && pfPickup) {
    pickup = {
      lat: pfPickup.lat, lng: pfPickup.lng, label: pfPickup.label,
      earliest: toEpoch($("pf-pickup-from").value),
      latest: toEpoch($("pf-pickup-until").value),
    };
  }
  const weight = $("pf-weight").value.trim();
  const { dims } = readDimensions();

  try {
    const parcel = await api("/api/parcels", {
      method: "POST",
      body: JSON.stringify({
        name: $("pf-name").value.trim(),
        priority: pfPrio,
        windowStart: toEpoch($("pf-window").value),
        receiverEmail: $("pf-email").value.trim() || null,
        trackingNumber: $("pf-tracking").value.trim() || null,
        receiverName: $("pf-receiver").value.trim() || null,
        dimensionsCm: dims,
        type: $("pf-type").value.trim() || "general",
        size: pfSize,
        requestType: pfKind,
        earlyOk: request && $("pf-early").checked,
        destination: request ? null : pfDest,
        pickup,
        deadline,
        weightKg: weight ? Number(weight) : null,
        fragile: $("pf-fragile").checked,
        recipientPhone: $("pf-phone").value.trim() || null,
        notes: $("pf-notes").value.trim() || null,
      }),
    });
    parcels[parcel.id] = parcel;   // reflect immediately; don't wait on SSE
    renderParcels();
    toggleParcelForm(false);
  } catch (err) {
    pfErr("pf-form-err", err.message || "Could not add the parcel. Please try again.");
    save.disabled = false;
    save.textContent = label;
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
  const seenTracking = new Set();
  for (let i = 0; i < raw.length; i++) {
    const r = raw[i];
    $("import-progress").textContent = `Geocoding ${i + 1}/${raw.length}…`;
    const row = { ...r, error: null, destinationResolved: null, dims: null };
    const edges = [r.length, r.width, r.height];
    if (edges.some(Boolean)) {
      const n = edges.map(Number);
      if (n.every(x => x > 0 && x <= 1000)) row.dims = { l: n[0], w: n[1], h: n[2] };
      else row.error = "dimensions need length, width and height (cm)";
    }
    row.sizeResolved = (r.size || "").toLowerCase() ||
      (row.dims ? sizeFromDimensions(row.dims.l, row.dims.w, row.dims.h) : "");
    const dupe = r.trackingNumber && seenTracking.has(r.trackingNumber.toLowerCase());
    if (r.trackingNumber) seenTracking.add(r.trackingNumber.toLowerCase());
    if (row.error) { /* dimension error above */ }
    else if (!r.name && !r.trackingNumber) row.error = "needs a name or tracking number";
    else if (dupe) row.error = "duplicate tracking number in this file";
    else if (!r.destination) row.error = "missing destination address";
    else if (!SIZES_OK(row.sizeResolved)) row.error = "size must be small/medium/large (or give dimensions)";
    else if (r.deadline && parseDeadline(r.deadline) === null) row.error = "bad deadline";
    else if (r.windowStart && parseDeadline(r.windowStart) === null) row.error = "bad window start";
    else if (r.priority && !["standard", "priority", "express"].includes(r.priority.toLowerCase())) row.error = "priority must be standard, priority or express";
    else {
      try {
        row.destinationResolved = await api(`/api/geocode?q=${encodeURIComponent(r.destination)}`);
      } catch (_) {
        row.error = "address not found";
      }
    }
    out.push(row);
  }
  importRows = out;
  const ok = out.filter(r => !r.error).length;
  $("import-progress").textContent =
    `${ok} of ${out.length} rows ready` + (ok < out.length ? " — rows with errors are skipped" : "");
  $("import-preview").innerHTML = `<table>
    <tr><th>Tracking no.</th><th>Parcel</th><th>Receiver</th><th>Size</th><th>Address</th><th></th></tr>
    ${out.map(r => `<tr>
      <td>${r.trackingNumber || "auto"}</td><td>${r.name || "—"}</td><td>${r.receiverName || "—"}</td>
      <td>${r.sizeResolved || "—"}${r.dims ? ` · ${r.dims.l}×${r.dims.w}×${r.dims.h}` : ""}${r.weightKg ? ` · ${r.weightKg} kg` : ""}</td>
      <td>${r.destinationResolved ? r.destinationResolved.label : r.destination}</td>
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
      priority: (r.priority || "standard").toLowerCase(),
      windowStart: parseDeadline(r.windowStart),
      receiverEmail: r.receiverEmail || null,
      name: r.name || r.trackingNumber,
      trackingNumber: r.trackingNumber || null,
      receiverName: r.receiverName || null,
      recipientPhone: r.recipientPhone || null,
      type: r.type || "general",
      size: r.sizeResolved,
      dimensionsCm: r.dims,
      weightKg: r.weightKg && Number(r.weightKg) > 0 ? Number(r.weightKg) : null,
      fragile: parseFlag(r.fragile),
      notes: r.notes || null,
      destination: r.destinationResolved,
      deadline: parseDeadline(r.deadline) ?? Date.now() / 1000 + 4 * 3600,
    })),
  };
  const res = await api("/api/parcels/bulk", { method: "POST", body: JSON.stringify(body) });
  $("import-modal").style.display = "none";
  if (res.errors.length) alert(`${res.errors.length} rows were rejected by the server:\n` +
    res.errors.slice(0, 5).map(e => `row ${e.index + 1}: ${e.message}`).join("\n"));
});

// ---------------- editing runs: add, move, drag and drop, change driver ----------------

function makeDraggable(el, parcelId) {
  el.draggable = true;
  el.addEventListener("dragstart", e => {
    e.dataTransfer.setData("text/plain", parcelId);
    e.dataTransfer.effectAllowed = "move";
    el.classList.add("dragging");
  });
  el.addEventListener("dragend", () => el.classList.remove("dragging"));
}

function makeDropTarget(row, tripId) {
  row.addEventListener("dragover", e => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; row.classList.add("drop-target"); });
  row.addEventListener("dragleave", () => row.classList.remove("drop-target"));
  row.addEventListener("drop", e => {
    e.preventDefault();
    row.classList.remove("drop-target");
    const pid = e.dataTransfer.getData("text/plain");
    if (pid && parcels[pid]) dropParcelOnRun(pid, tripId);
  });
}

// hovering a dragged parcel over the Runs tab opens it, so a parcel can be
// dragged from the Parcels list straight onto a run
function setupDragAndDrop() {
  const tab = $("tab-btn-fleet");
  let timer = null;
  tab.addEventListener("dragover", e => {
    e.preventDefault();
    tab.classList.add("drop-hint");
    if (!timer && activeTab !== "fleet") timer = setTimeout(() => { switchTab("fleet"); timer = null; }, 500);
  });
  tab.addEventListener("dragleave", () => { tab.classList.remove("drop-hint"); clearTimeout(timer); timer = null; });
  tab.addEventListener("drop", () => { tab.classList.remove("drop-hint"); clearTimeout(timer); timer = null; });
}

async function dropParcelOnRun(parcelId, tripId) {
  const p = parcels[parcelId];
  if (!p || p.tripId === tripId) return;
  try {
    await putParcelOnRun(p, tripId);
  } catch (err) {
    alert(`Couldn’t move “${p.name}”: ${err.message}`);
  }
}

// pending parcel -> add to the run; parcel already on another run -> move it
async function putParcelOnRun(p, tripId) {
  if (p.tripId && ["assigned", "in_transit", "picked_up"].includes(p.status)) {
    await api(`/api/parcels/${p.id}/move`, { method: "POST", body: JSON.stringify({ toTripId: tripId }) });
  } else {
    await api(`/api/runs/${tripId}/parcels`, { method: "POST", body: JSON.stringify({ parcelIds: [p.id] }) });
  }
  await refreshAfterRunEdit();
}

// the edit itself already succeeded; a failed redraw must not read as a failed edit
async function refreshAfterRunEdit() {
  try {
    await Promise.all([loadParcels(), loadTrips()]);
    if (selectedId && trips[selectedId]) await selectTrip(selectedId);
  } catch (err) { console.warn("refresh after run edit failed", err); }
}

async function addSelectedToRun() {
  if (!selectedId || !selectedParcels.size) return;
  const btn = $("btn-add-to-run");
  btn.disabled = true;
  try {
    await api(`/api/runs/${selectedId}/parcels`, {
      method: "POST", body: JSON.stringify({ parcelIds: [...selectedParcels] }),
    });
    selectedParcels.clear();
    await refreshAfterRunEdit();
  } catch (err) {
    alert("Couldn’t add them: " + err.message);
  } finally {
    btn.disabled = false;
    renderParcels();
  }
}

function setupReassign() {
  $("btn-reassign").onclick = async () => {
    const box = $("reassign-form");
    if (!box.hidden) { box.hidden = true; return; }
    $("reassign-err").hidden = true;
    const sel = $("reassign-driver");
    sel.innerHTML = `<option>Loading…</option>`;
    box.hidden = false;
    try {
      const o = await api(`/api/runs/${selectedId}/options`);
      sel.innerHTML = o.drivers.map(d =>
        `<option value="${d.id}" ${d.reason ? "disabled" : ""}>${d.name}${d.vehicle ? " · " + d.vehicle : ""}${d.reason ? " — " + d.reason : ""}</option>`).join("")
        || `<option value="">No other drivers</option>`;
      const first = o.drivers.find(d => !d.reason);
      if (first) sel.value = first.id;
    } catch (err) { sel.innerHTML = `<option value="">${err.message}</option>`; }
  };
  $("reassign-cancel").onclick = () => { $("reassign-form").hidden = true; };
  $("reassign-save").onclick = async () => {
    const id = $("reassign-driver").value;
    if (!id) return;
    $("reassign-save").disabled = true;
    try {
      await api(`/api/runs/${selectedId}/reassign`, { method: "POST", body: JSON.stringify({ driverId: id }) });
      $("reassign-form").hidden = true;
      await Promise.all([loadTrips(), loadParcels()]);
      await selectTrip(selectedId);
    } catch (err) {
      $("reassign-err").textContent = err.message;
      $("reassign-err").hidden = false;
    } finally { $("reassign-save").disabled = false; }
  };
}

// ---------------- run creation ----------------

function openPicker() {
  pickerDriverId = null;
  $("picker-count").textContent = selectedParcels.size;
  pickerReasons = {};
  renderPickerDrivers();
  openPickerReasons();
  $("run-picker").style.display = "flex";
  $("trip-detail").style.display = "none";
}

let pickerReasons = {};   // driverId -> reason the vehicle can't take the selection (server-checked)

async function openPickerReasons() {
  try {
    pickerReasons = await api("/api/runs/eligibility", {
      method: "POST", body: JSON.stringify({ parcelIds: [...selectedParcels] }),
    });
  } catch (_) { pickerReasons = {}; }
  renderPickerDrivers();
}

function renderPickerDrivers() {
  const wrap = $("picker-drivers");
  wrap.innerHTML = "";
  if (!driversReg.length) {
    wrap.innerHTML = `<div class="empty">${iconSvg("user", "i-lg")}
      <b>No drivers yet</b>Add vehicles and drivers in the Registry tab first.</div>`;
  }
  driversReg.forEach(d => {
    const v = vehiclesReg.find(x => x.id === d.vehicleId);
    const reason = !v ? "no vehicle assigned" : (pickerReasons[d.id] || null);
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
        <div class="meta">${v ? `${v.name} · ${v.plate} · cap ${v.capacity} · max ${v.maxParcelSize}${
          v.maxPayloadKg ? ` · ${(+v.maxPayloadKg).toLocaleString()} kg` : ""}` : "no vehicle"}</div>
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

async function autoAssign(engine = "rules") {
  const pending = Object.values(parcels).filter(p => p.status === "pending");
  if (!pending.length) return alert("No pending parcels to assign.");
  if (!driversReg.length) return alert("Add drivers in the Registry tab first.");

  const scope = selectedParcels.size ? [...selectedParcels] : null;
  $("run-picker").style.display = "none";
  $("trip-detail").style.display = "none";
  $("dispatch-card").style.display = "flex";
  $("dispatch-actions").style.display = "none";
  $("dispatch-body").innerHTML = `<div class="working"><span class="spinner"></span>
    <span>Grouping ${scope ? scope.length + " selected" : "your pending"} parcels into runs —
    ${engine === "ai" ? "checking fleet eligibility, routes and deadlines. This can take up to a minute…"
                      : "fitting them onto as few vehicles as possible…"}</span></div>`;
  try {
    dispatchPlan = await api("/api/dispatch/propose", {
      method: "POST", body: JSON.stringify({ parcelIds: scope, engine }),
    });
    renderDispatch();
  } catch (err) {
    $("dispatch-body").innerHTML = `<div class="warn-row">Couldn't suggest runs: ${err.message}</div>`;
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
  if ($("veh-count")) { $("veh-count").textContent = vehiclesReg.length; $("drv-count").textContent = driversReg.length; }
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
        <div class="meta">${v.type} · ${v.capacity} parcels · max ${v.maxParcelSize}${
          v.cargoCm ? ` · cargo ${v.cargoCm.l}×${v.cargoCm.w}×${v.cargoCm.h} cm` : ""}${
          v.maxPayloadKg ? ` · ${(+v.maxPayloadKg).toLocaleString()} kg` : ""}</div>
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
    const queueUrl = `${location.origin}/static/driver.html?driver=${d.id}${d.token ? "&k=" + encodeURIComponent(d.token) : ""}`;
    row.innerHTML = `
      <span class="veh-ico">${v ? vehIconSvg(v.type) : iconSvg("user")}</span>
      <div class="info">
        <div class="label">${d.name}</div>
        <div class="meta">${d.phone || "—"} · ${v ? v.name : "no vehicle"}${driverHoursText(d)} ·
          <a href="${queueUrl}" target="_blank" rel="noopener" style="color:var(--primary)">driver link</a></div>
      </div>
      <button class="link-action edit-driver" type="button" aria-label="Edit driver ${d.name}">Edit</button>
      <button class="del" type="button" aria-label="Delete driver ${d.name}">${iconSvg("trash")}</button>`;
    row.querySelector(".edit-driver").onclick = () => openDriverForm(d);
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
  const edges = ["l", "w", "h"].map(k => Number($(`vf-cargo-${k}`).value));
  const cargo = edges.every(n => n > 0) ? { l: edges[0], w: edges[1], h: edges[2] } : null;
  try {
    await api("/api/vehicles", {
      method: "POST",
      body: JSON.stringify({
        name: $("vf-name").value.trim() || "Vehicle",
        plate: $("vf-plate").value.trim(),
        type,
        capacity: Number($("vf-capacity").value) || null,
        maxParcelSize: $("vf-maxsize").value,
        cargoCm: cargo,
        maxPayloadKg: Number($("vf-payload").value) || null,
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

function driverHoursText(d) {
  const bits = [];
  if (d.onLeave) bits.push("on leave");
  else if (d.shiftStart && d.shiftEnd) bits.push(`${d.shiftStart}–${d.shiftEnd}`);
  if (d.maxStops) bits.push(`max ${d.maxStops} stops`);
  if (d.maxHours) bits.push(`max ${d.maxHours} h`);
  return bits.length ? " · " + bits.join(" · ") : "";
}

function openDriverForm(d) {
  editingDriverId = d ? d.id : null;
  renderDriverVehicleSelect();
  $("df-name").value = d ? d.name : "";
  $("df-phone").value = d ? d.phone || "" : "";
  if (d && d.vehicleId) $("df-vehicle").value = d.vehicleId;
  $("df-shift-start").value = d ? d.shiftStart || "" : "";
  $("df-shift-end").value = d ? d.shiftEnd || "" : "";
  $("df-max-stops").value = d && d.maxStops ? d.maxStops : "";
  $("df-max-hours").value = d && d.maxHours ? d.maxHours : "";
  $("df-leave").checked = !!(d && d.onLeave);
  $("df-save").textContent = d ? "Save changes" : "Add driver";
  $("df-err").hidden = true;
  $("driver-form").style.display = "flex";
  $("df-name").focus();
}

async function saveDriver() {
  $("df-err").hidden = true;
  try {
    await api(editingDriverId ? `/api/drivers/${editingDriverId}` : "/api/drivers", {
      method: editingDriverId ? "PUT" : "POST",
      body: JSON.stringify({
        name: $("df-name").value.trim() || "Driver",
        phone: $("df-phone").value.trim(),
        vehicleId: $("df-vehicle").value || null,
        shiftStart: $("df-shift-start").value || null,
        shiftEnd: $("df-shift-end").value || null,
        maxStops: Number($("df-max-stops").value) || null,
        maxHours: Number($("df-max-hours").value) || null,
        onLeave: $("df-leave").checked,
      }),
    });
    $("driver-form").style.display = "none";
    editingDriverId = null;
    await loadRegistry();
  } catch (err) {
    $("df-err").textContent = err.message;
    $("df-err").hidden = false;
  }
}

// ---------------- fleet (runs) ----------------

function runLabel(stops) {
  const first = stops[0], last = stops[stops.length - 1];
  const origin = (first.depot || first.kind === "depot") ? "Hub" : (first.label || "Start");
  return `${origin} → ${last.place || last.label || "destination"}`;
}

// last unresolved stall / pickup-risk in the alert log → a short badge label
function riskFromAlerts(alerts) {
  for (const a of [...(alerts || [])].reverse()) {
    if (a.type === "moving_again" || a.type === "collected") return null;
    if (a.type === "stalled") return "stopped";
    if (a.type === "pickup_risk") return "pickup at risk";
  }
  return null;
}

function summarize(trip) {
  const s = trip.route.orderedStops;
  return {
    id: trip.id,
    status: trip.status,
    label: runLabel(s),
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
    atRiskParcelIds: (trip.plannedEtaPerStop || [])
      .filter(e => e.kind === "delivery" && e.atRisk).map(e => e.parcelId),
    // live risk marker — kept if already set, else derived from the alert log
    risk: (trips[trip.id] || {}).risk ?? riskFromAlerts(trip.alerts),
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
    ${t.risk ? `<span class="dev-badge risk">${iconSvg("alert")} ${t.risk}</span>` : ""}
    ${t.deviations ? `<span class="dev-badge">${iconSvg("alert")} ${t.deviations}</span>` : ""}`;
  row.onclick = () => selectTrip(t.id);
  if (t.status !== "completed") makeDropTarget(row, t.id);
  return row;
}

function renderTripList() {
  refreshOverview();
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
  updateRunFooter();
  if (typeof dashHighlight === "function") dashHighlight();
}

function renderDetail() {
  closeParcelDetail();
  $("trip-detail").style.display = "flex";
  $("btn-reassign").hidden = detail.status !== "planned";
  $("reassign-form").hidden = true;
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

  const drv = driversReg.find(x => x.id === detail.driverId);
  const url = detail.driverId
    ? `${location.origin}/static/driver.html?driver=${detail.driverId}${drv && drv.token ? "&k=" + encodeURIComponent(drv.token) : ""}`
    : `${location.origin}/static/driver.html?trip=${detail.id}`;
  $("btn-runsheet").href = `/static/runsheet.html?trip=${encodeURIComponent(detail.id)}`;
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
  const etas = {};
  (detail.plannedEtaPerStop || []).forEach(e => { etas[e.stopIndex] = e; });
  const rows = detail.route.orderedStops.map((s, i) => {
    if (i === 0 || !s.parcelId) return "";
    const kind = s.kind || "delivery";
    const isPickup = kind === "pickup";
    const p = parcels[s.parcelId];
    const e = etas[i];
    const st = p ? parcelStateAt(p, typeof viewAt === "undefined" ? null : viewAt) : null;
    const done = p && (isPickup
      ? ["picked_up", "delivered"].includes(st)
      : st === "delivered");
    const status = done
      ? `<span class="done-tag">${iconSvg("check")} ${isPickup ? "collected" : (p.deadlineMissed ? "late" : "done")}</span>`
      : (e && e.atRisk ? `<span class="risk-tag">at risk</span>` : "");
    const eta = e ? new Date(e.eta * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "–";
    const sub = isPickup ? (p && p.pickup ? p.pickup.label : s.label)
                         : (p ? p.destination.label : "");
    const drag = p && !done && !isPickup && detail.status !== "completed" && (typeof viewAt === "undefined" || viewAt == null) ? ` draggable="true" data-parcel="${p.id}"` : "";
    return `<tr${drag}>
      <td class="leg-route">${isPickup ? "Collect" : "Deliver"} — ${p ? p.name : s.label}<small>${sub}</small></td>
      <td class="num">${eta}</td>
      <td class="num">${status}</td>
    </tr>`;
  }).filter(Boolean);
  $("manifest").innerHTML = rows.join("") ||
    `<tr><td class="leg-route">No parcels on this trip</td></tr>`;
  document.querySelectorAll("#manifest tr[data-parcel]").forEach(tr => makeDraggable(tr, tr.dataset.parcel));
}

function drawSelectedRoute() {
  if (routeLine) routeLine.setMap(null);
  selStopMarkers.forEach(m => m.setMap(null));
  trafficLines.forEach(l => l.setMap(null));
  const path = decodedPath(detail);
  routeLine = drawRoute(map, path);
  trafficLines = drawTraffic(map, path, detail.route.traffic);
  selStopMarkers = detail.route.orderedStops.map((s, i) =>
    stopMarker(map, s, i, detail.route.orderedStops.length, s.kind));
  const bounds = new google.maps.LatLngBounds();
  path.forEach(p => bounds.extend(p));
  const docked = document.body.dataset.page === "dashboard";
  map.fitBounds(bounds, docked ? { top: 40, bottom: 40, left: 40, right: 40 } : { top: 80, bottom: 60, left: 60, right: 410 });
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
  if (typeof dashHighlight === "function") dashHighlight();
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
  if (document.body.dataset.page === "dashboard") {        // the dashboard draws its own ride markers
    if (vehicles[id]) { vehicles[id].setMap(null); delete vehicles[id]; }
    return;
  }
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

let _es = null, _esRetry = 0;

function setStreamDot(state) {          // "live" | "down" | ""
  const d = $("stream-dot");
  if (!d) return;
  d.className = state;
  const text = state === "live" ? "Live" : state === "down" ? "Reconnecting…" : "Connecting";
  d.title = state === "live" ? "Live updates: connected"
    : state === "down" ? "Live updates: reconnecting…" : "Live updates: connecting…";
  const pill = $("stream-pill");
  if (pill) { pill.dataset.state = state; $("stream-text").textContent = text; }
}

// EventSource retries on its own, but a server restart (or a proxy dropping an
// idle stream) can leave it CLOSED for good — so watch it and rebuild.
function openStream() {
  if (_es) { _es.close(); _es = null; }
  const reconnecting = _esRetry > 0;
  const es = _es = new EventSource("/api/stream");
  es.onopen = () => {
    _esRetry = 0;
    setStreamDot("live");
    if (reconnecting) Promise.allSettled([loadParcels(), loadTrips()]);  // catch up
  };
  es.onerror = () => {
    setStreamDot("down");
    if (es.readyState === EventSource.CLOSED) {
      const wait = Math.min(1000 * 2 ** _esRetry++, 15000);
      setTimeout(() => { if (_es === es) openStream(); }, wait);
    }
  };

  es.addEventListener("parcel", e => {
    const { parcel, deleted } = JSON.parse(e.data);
    if (deleted) delete parcels[parcel.id];
    else parcels[parcel.id] = parcel;
    renderParcels();
    if (pdOpenId === parcel.id) { deleted ? closeParcelDetail() : refreshParcelDetail(); }
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
    if (typeof dashRefreshSoon === "function") dashRefreshSoon();
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
    if (typeof dashOnPosition === "function") dashOnPosition(p);
  });

  es.addEventListener("status", e => {
    const s = JSON.parse(e.data);
    const t = trips[s.tripId];
    if (!t) return;
    t.status = s.status;
    renderTripList();
    updateVehicle(s.tripId);
    if (typeof dashRefreshSoon === "function") dashRefreshSoon();
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
    if (t && a.type === "collected") { t.collectedCount = (t.collectedCount || 0) + 1; }
    // persistent per-trip risk marker (survives the banner fade)
    if (t && a.type === "stalled") { t.risk = "stopped"; renderTripList(); }
    if (t && a.type === "pickup_risk") { t.risk = "pickup at risk"; renderTripList(); }
    if (t && (a.type === "moving_again" || a.type === "collected")) { t.risk = null; renderTripList(); }
    if (a.tripId === selectedId && detail) {
      detail.alerts.push(a);
      if (a.type === "delivered") detail.deliveredCount = (detail.deliveredCount || 0) + 1;
      if (a.type === "collected") detail.collectedCount = (detail.collectedCount || 0) + 1;
      if (a.type === "delivered" || a.type === "collected") renderManifest();
      addAlertRow(a, true);
      $("kpi-devs").textContent = detail.alerts.filter(x => x.type === "deviation").length;
    }
    if (["deviation", "pickup_risk", "stalled"].includes(a.type)) {
      flashBanner(`${t ? t.label : a.tripId} — ${a.message}`, a.type !== "deviation", a.tripId);
    }
    if (a.type === "moving_again" && $("alert-banner").dataset.trip === a.tripId) {
      $("alert-banner").style.display = "none";
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
  back_on_route: "ok", completed: "ok", delivered: "ok", collected: "ok",
  moving_again: "ok",
  reroute: "info", started: "info", ended: "info",
  pickup_risk: "warn", stalled: "warn", failed: "warn",
};

const ALERT_ICON = { ok: "check", info: "info", warn: "alert" };

function addAlertRow(a, prepend = false) {
  const row = document.createElement("div");
  const style = ALERT_STYLE[a.type] || "";
  row.className = "alert-row " + style;
  row.innerHTML = `${iconSvg(ALERT_ICON[style] || "alert")}<div><time>${fmtClock(a.ts)}</time>${a.message}</div>`;
  prepend ? $("alerts").prepend(row) : $("alerts").append(row);
}

// sticky: stays until dismissed (click) or the caller hides it — used for
// pickup-risk / stalled, which don't clear on their own like a deviation does
function flashBanner(msg, sticky = false, tripId = "") {
  const b = $("alert-banner");
  b.innerHTML = iconSvg("alert", "i-lg") + "<span></span>" +
    (sticky ? `<button class="banner-x" type="button" aria-label="Dismiss">${iconSvg("x")}</button>` : "");
  b.querySelector("span").textContent = msg;
  b.dataset.trip = tripId;
  b.style.display = "flex";
  clearTimeout(b._t);
  if (sticky) {
    b.querySelector(".banner-x").onclick = () => { b.style.display = "none"; };
  } else {
    b._t = setTimeout(() => { b.style.display = "none"; }, 6000);
  }
}

function setHeaderStatus(status) {
  const el = $("trip-status");
  el.className = "status-" + status;
  const text = { planned: "Scheduled", active: "En route", deviating: "Off route", completed: "Finished" }[status] || status;
  $("trip-status-text").textContent = text;
  const chip = $("detail-state");
  if (chip) {
    chip.textContent = text;
    chip.className = "state-chip " + ({ planned: "scheduled", active: "en_route", deviating: "deviating", completed: "finished" }[status] || "");
  }
}
