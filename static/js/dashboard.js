// Dashboard: the hero band, and "all rides at a time" — a live board that can
// also be scrubbed back to any moment of any day. Rides come from /api/rides?at=;
// this file only draws them (table, day timeline, map markers). Loads before
// admin.js and reads its globals (map, trips, parcels, selectedId …) at call time.

const dash = {
  at: null,                       // null = live, else epoch seconds
  data: null, seq: 0, timer: null, soon: null,
  show: new Set(["en_route", "deviating", "scheduled", "finished"]),
  open: new Set(),                // ride ids whose parcel list is expanded
  sort: { key: "state", dir: 1 }, // sortable table, like Onfleet's one-click sort by status
};
let viewAt = null;                // what the rest of the console should show: null = live
let rideMarkers = {}, ridePolys = {}, trailLine = null;

const STATE_LABEL = { en_route: "En route", deviating: "Off route", scheduled: "Scheduled", finished: "Finished" };
const PARCEL_STATE_LABEL = {
  pending: "Pending", assigned: "Assigned", in_transit: "Out for delivery", picked_up: "Collected",
  delivered: "Delivered", failed: "Failed attempt", awaiting_redelivery: "Back at the hub", returned: "Returned",
};
const EVENT_STATE = {
  created: "pending", unassigned: "pending", assigned: "assigned", out_for_delivery: "in_transit",
  collected: "picked_up", delivered: "delivered", failed: "failed",
  returned_to_hub: "awaiting_redelivery", returned: "returned",
};

const dashPage = () => document.body.dataset.page === "dashboard";
const pad2 = n => String(n).padStart(2, "0");
const clock = ts => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const dayLabel = ts => new Date(ts * 1000).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
const dateValue = ts => { const d = new Date(ts * 1000); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };

// a parcel's state at a moment, from its history (null = it did not exist yet)
function parcelStateAt(p, at) {
  if (at == null) return p.status;
  let state = null;
  for (const e of [...(p.events || [])].sort((a, b) => a.ts - b.ts)) {
    if (e.ts > at) break;
    if (EVENT_STATE[e.type]) state = EVENT_STATE[e.type];
  }
  return state;
}

// ---------------------------------------------------------------- hero

function renderHero() {
  const now = new Date();
  const h = now.getHours();
  const hello = h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  const name = currentUser && currentUser.id !== "local" ? currentUser.name.split(/\s+/)[0] : "";
  $("greeting").textContent = "Operations overview";
  const pill = $("stream-pill"), live = $("hero-live");
  if (pill && live) { live.dataset.state = pill.dataset.state || ""; $("hero-live-text").textContent = pill.dataset.state === "live" ? "Live operations" : $("stream-text").textContent; }
  $("hero-date").textContent = now.toLocaleDateString([], { day: "numeric", month: "long", weekday: "long" });
  $("hero-clock").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (typeof depot !== "undefined" && depot) $("profile-hub").textContent = depot.label;
}

// ---------------------------------------------------------------- time bar

function dayStartOf(ts) { const d = new Date(ts * 1000); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; }

function syncControls() {
  const live = dash.at == null;
  const ref = live ? Date.now() / 1000 : dash.at;
  $("dash-live").setAttribute("aria-pressed", String(live));
  $("dash-date").value = dateValue(ref);
  $("dash-date").max = dateValue(Date.now() / 1000);
  const d = new Date(ref * 1000);
  const mins = d.getHours() * 60 + d.getMinutes();
  const isToday = dateValue(ref) === dateValue(Date.now() / 1000);
  const nowMins = new Date().getHours() * 60 + new Date().getMinutes();
  $("dash-slider").max = isToday ? nowMins : 1439;
  $("dash-slider").value = mins;
  $("dash-time").textContent = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  $("dash-hint").textContent = live ? "Drag back to replay the day" : "Viewing the past — press Live to return";
  $("dash-hint").classList.toggle("past", !live);
}

function setAtFromControls() {
  const day = $("dash-date").value;
  if (!day) return;
  const [y, m, dd] = day.split("-").map(Number);
  const base = new Date(y, m - 1, dd, 0, 0, 0).getTime() / 1000;
  let at = base + Number($("dash-slider").value) * 60;
  at = Math.min(at, Date.now() / 1000);
  goAt(at);
}

function goAt(at) {
  dash.at = at;
  viewAt = at;
  syncControls();
  loadRides();
}

function goLive() {
  dash.at = null;
  viewAt = null;
  syncControls();
  clearTrail();
  loadRides();
  if (detail && typeof renderManifest === "function") { renderManifest(); }
}

function stepMinutes(delta) {
  const base = dash.at == null ? Date.now() / 1000 : dash.at;
  goAt(Math.min(base + delta * 60, Date.now() / 1000));
}

// ---------------------------------------------------------------- data

async function loadRides() {
  if (!dashPage()) return;
  const seq = ++dash.seq;
  let d;
  try { d = await api(`/api/rides?at=${dash.at == null ? "now" : dash.at}`); }
  catch (err) { $("rides-list").innerHTML = `<div class="rides-empty">Couldn’t load the rides: ${pdEsc(err.message)}</div>`; return; }
  if (seq !== dash.seq) return;                       // a newer request overtook this one
  dash.data = d;
  renderRides();
  renderDock();
  drawRideMap();
  updateViewBanner();
  if (dash.at != null && selectedId) drawTrail(selectedId);
}

function dashRefreshSoon() {
  if (!dashPage() || dash.at != null) return;
  clearTimeout(dash.soon);
  dash.soon = setTimeout(loadRides, 700);
}

// SSE position tick: nudge the marker without a round trip
function dashOnPosition(p) {
  if (!dashPage() || dash.at != null || !dash.data) return;
  const ride = dash.data.rides.find(r => r.id === p.tripId);
  if (!ride) return;
  ride.position = { lat: p.lat, lng: p.lng, ts: p.ts };
  const m = rideMarkers[ride.id];
  if (m) m.setPosition({ lat: p.lat, lng: p.lng });
}

// ---------------------------------------------------------------- rides table

function visibleRides() {
  return dash.data ? dash.data.rides.filter(r => dash.show.has(r.state)) : [];
}

const STATE_ORDER = { deviating: 0, en_route: 1, scheduled: 2, finished: 3 };

function sortedRides(rides) {
  const { key, dir } = dash.sort;
  const val = r => ({
    driver: (r.driverName || "").toLowerCase(),
    state: STATE_ORDER[r.state] * 10 - Math.min(9, r.overdue + r.atRisk),   // trouble first within a state
    progress: r.parcelCount ? r.delivered / r.parcelCount : 0,
    next: (r.nextStop && r.nextStop.label || "zzz").toLowerCase(),
  })[key];
  return [...rides].sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * dir; });
}

function riskChip(r) {
  if (r.state === "finished") return "";
  if (r.overdue) return `<span class="risk-chip bad" title="Parcels already past their deadline">${r.overdue} overdue</span>`;
  if (r.atRisk) return `<span class="risk-chip" title="The planned arrival is after the deadline">${r.atRisk} at risk</span>`;
  return "";
}

function renderRides() {
  const d = dash.data;
  if (!d) return;
  $("rides-title").textContent = d.live ? "Rides now" : `Rides on ${dayLabel(d.at)} at ${clock(d.at)}`;

  $("rides-filter").innerHTML = ["en_route", "deviating", "scheduled", "finished"].map(s => `
    <button type="button" class="fchip" data-s="${s}" aria-pressed="${dash.show.has(s)}">${STATE_LABEL[s]}<span class="n">${d.counts[s] || 0}</span></button>`).join("");
  $("rides-filter").querySelectorAll(".fchip").forEach(b => {
    b.onclick = () => {
      dash.show.has(b.dataset.s) ? dash.show.delete(b.dataset.s) : dash.show.add(b.dataset.s);
      renderRides(); drawRideMap();
    };
  });
  $("hour-ticks").innerHTML = ["00", "06", "12", "18", "24"].map(h => `<span>${h}</span>`).join("");
  $("ride-cols").querySelectorAll("[data-sort]").forEach(b => {
    b.setAttribute("aria-sort", dash.sort.key === b.dataset.sort ? (dash.sort.dir === 1 ? "ascending" : "descending") : "none");
  });
  // the day's exceptions at a glance (manage by exception)
  const ex = d.exceptions || { failed: 0, delayed: 0 };
  $("exc-chips").innerHTML =
    (ex.delayed ? `<span class="exc bad">${ex.delayed} delayed</span>` : "") +
    (ex.failed ? `<span class="exc warn">${ex.failed} failed</span>` : "") +
    (!ex.delayed && !ex.failed && d.rides.length ? `<span class="exc ok">No exceptions</span>` : "");

  const rides = sortedRides(visibleRides());
  const span = d.dayEnd - d.dayStart;
  const pct = ts => Math.max(0, Math.min(100, (ts - d.dayStart) / span * 100));
  const list = $("rides-list");
  if (!rides.length) {
    list.innerHTML = `<div class="rides-empty">${d.rides.length
      ? "No rides match the filters above."
      : (d.live ? "No rides yet today. Plan a run from the Parcels page." : "No rides were running at this time.")}</div>`;
    return;
  }
  list.innerHTML = rides.map(r => {
    const start = r.startedAt || r.createdAt;
    const end = r.endedAt || Math.max(d.now, d.at);
    const left = pct(start), width = Math.max(1.2, pct(end) - left);
    const total = r.parcelCount || 0;
    const initials = (r.driverName || "?").split(/\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase();
    const open = dash.open.has(r.id);
    return `
      <div class="ride-row s-${r.state}${r.id === selectedId ? " selected" : ""}" role="button" tabindex="0" data-id="${r.id}" aria-label="Open ride ${pdEsc(r.driverName || r.id)}">
        <div class="ride-who"><span class="avatar">${pdEsc(initials)}</span>
          <div style="min-width:0"><b>${pdEsc(r.driverName || "No driver")}</b><span>${pdEsc(r.vehicleName || r.vehicleType)}${r.vehiclePlate ? " · " + pdEsc(r.vehiclePlate) : ""}</span></div></div>
        <div class="state-cell"><span class="state-chip ${r.state}">${STATE_LABEL[r.state]}</span>${riskChip(r)}</div>
        <div class="prog"><span>${r.delivered} of ${total} delivered${r.failed ? ` · ${r.failed} failed` : ""}</span>${r.state !== "finished" && r.onBoard ? `<span class="load${r.loadPct > 90 ? " full" : ""}" title="How much of the vehicle's capacity (count, weight or volume) is in use">${r.loadPct}% full</span>` : ""}
          <div class="prog-bar" aria-hidden="true"><i style="width:${total ? r.delivered / total * 100 : 0}%"></i><i class="bad" style="width:${total ? r.failed / total * 100 : 0}%"></i></div></div>
        <div class="next-stop">${r.nextStop && r.state !== "finished"
          ? `<b>${pdEsc(r.nextStop.label)}</b><span>${r.nextEta ? "due " + clock(r.nextEta) : pdEsc(r.nextStop.parcel || "")}</span>`
          : `<span>${r.state === "finished" ? "Route complete" : "–"}</span>`}</div>
        <div class="track" title="${clock(start)} – ${r.endedAt ? clock(r.endedAt) : "now"}" aria-hidden="true">
          <span class="seg ${r.state}" style="left:${left}%;width:${width}%"></span>
          <span class="cursor" style="left:${pct(d.at)}%"></span></div>
        <div class="ride-toggle"><button type="button" class="link-action" data-more="${r.id}" aria-expanded="${open}">${open ? "Hide" : "Show"} parcels (${total})</button></div>
      </div>
      <div class="ride-more${open ? " open" : ""}" id="more-${r.id}">${open ? parcelList(r) : ""}</div>`;
  }).join("");

  list.querySelectorAll(".ride-row").forEach(row => {
    const ride = rides.find(r => r.id === row.dataset.id);
    if (ride && ride.state !== "finished" && typeof makeDropTarget === "function") makeDropTarget(row, ride.id);
    row.onclick = () => dashSelect(row.dataset.id);
    row.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); dashSelect(row.dataset.id); } };
  });
  list.querySelectorAll("[data-more]").forEach(b => {
    b.onclick = e => {
      e.stopPropagation();
      const id = b.dataset.more;
      dash.open.has(id) ? dash.open.delete(id) : dash.open.add(id);
      renderRides();
    };
  });
}

// the parcels on a ride, as they stood at the chosen moment
function parcelList(r) {
  const rows = (r.parcelIds || []).map(id => parcels[id]).filter(Boolean).map(p => {
    const st = parcelStateAt(p, dash.at);
    return st ? { p, st } : null;
  }).filter(Boolean);
  if (!rows.length) return `<div class="hint" style="padding:8px 0">No parcel history for this moment.</div>`;
  return rows.map(({ p, st }) => `
    <div class="ride-parcel"><div><b>${pdEsc(p.trackingNumber || p.name)}</b> ${pdEsc(p.name)}
      <span class="sub">${pdEsc(p.receiverName || "")}${p.receiverName ? " · " : ""}${pdEsc(p.destination.label)}</span></div>
      <span class="state-chip ${st === "delivered" ? "finished" : st === "failed" || st === "returned" ? "deviating" : st === "in_transit" ? "en_route" : "scheduled"}">${PARCEL_STATE_LABEL[st] || st}</span></div>`).join("");
}

function dashHighlight() {
  document.querySelectorAll("#rides-list .ride-row").forEach(r => r.classList.toggle("selected", r.dataset.id === selectedId));
  if (!selectedId) clearTrail();
}

async function dashSelect(id) {
  if (dash.at != null) viewAt = dash.at;
  await selectTrip(id);
  const m = rideMarkers[id];
  if (m && window.google && map) map.panTo(m.getPosition());
  if (dash.at != null) drawTrail(id);
}

function updateViewBanner() {
  const b = $("view-banner");
  if (!b) return;
  if (dash.at == null || !selectedId) { b.hidden = true; return; }
  const ride = dash.data && dash.data.rides.find(r => r.id === selectedId);
  b.hidden = false;
  if (ride) {
    $("view-banner-text").textContent = `Showing ${dayLabel(dash.at)}, ${clock(dash.at)} — ${STATE_LABEL[ride.state].toLowerCase()}, ${ride.delivered} of ${ride.parcelCount} delivered`;
    $("kpi-delivered").textContent = ride.delivered;
  } else {
    $("view-banner-text").textContent = `This run hadn’t been planned yet at ${clock(dash.at)}.`;
    $("kpi-delivered").textContent = "–";
  }
  if (typeof renderManifest === "function" && detail) renderManifest();
}

// ---------------------------------------------------------------- map

function clearTrail() { if (trailLine) { trailLine.setMap(null); trailLine = null; } }

async function drawTrail(id) {
  if (!window.google || !map) return;
  try {
    const r = await api(`/api/trips/${id}/trail?until=${dash.at}`);
    if (id !== selectedId || dash.at == null) return;
    clearTrail();
    if (r.points.length > 1) {
      trailLine = new google.maps.Polyline({ map, path: r.points, strokeColor: "#667085", strokeOpacity: .9, strokeWeight: 4, zIndex: 3 });
    }
  } catch (_) { /* the trail is a nice-to-have */ }
}

const ROUTE_TONE = { en_route: "#6d35b3", deviating: "#dd1311", scheduled: "#a79bc0" };

function drawRideMap() {
  if (!window.google || !map || !dashPage()) return;
  const rides = visibleRides();
  const keepM = new Set(), keepP = new Set();
  rides.forEach(r => {
    if (r.position) {
      keepM.add(r.id);
      let m = rideMarkers[r.id];
      if (!m || m._veh !== r.vehicleType) {
        if (m) m.setMap(null);
        m = fleetVehicleMarker(map, r.position, r.vehicleType);
        m._veh = r.vehicleType;
        m.addListener("click", () => dashSelect(r.id));
        rideMarkers[r.id] = m;
      } else {
        m.setPosition(r.position);
      }
      m.setOpacity(r.state === "finished" ? .55 : 1);
      m.setTitle(`${r.driverName || r.id} — ${STATE_LABEL[r.state]}`);
    }
    // faint dashed route for rides that are still to do, except the selected one (drawn by selectTrip)
    if (r.polyline && ROUTE_TONE[r.state] && r.id !== selectedId) {
      keepP.add(r.id);
      const key = r.polyline + r.state;
      if (!ridePolys[r.id] || ridePolys[r.id]._key !== key) {
        if (ridePolys[r.id]) ridePolys[r.id].setMap(null);
        const line = new google.maps.Polyline({
          map, path: google.maps.geometry.encoding.decodePath(r.polyline), strokeOpacity: 0, zIndex: 1,
          icons: [{ icon: { path: "M 0,-1 0,1", strokeOpacity: .8, strokeColor: ROUTE_TONE[r.state], scale: 2.4 }, offset: "0", repeat: "12px" }],
        });
        line._key = key;
        ridePolys[r.id] = line;
      }
    }
  });
  Object.keys(rideMarkers).forEach(id => { if (!keepM.has(id)) { rideMarkers[id].setMap(null); delete rideMarkers[id]; } });
  Object.keys(ridePolys).forEach(id => { if (!keepP.has(id)) { ridePolys[id].setMap(null); delete ridePolys[id]; } });
  fitRidesOnce();
}

let _fitted = false;
function fitRidesOnce() {
  if (_fitted || !window.google || selectedId) return;
  const pts = Object.values(rideMarkers).map(m => m.getPosition());
  if (!pts.length) return;
  _fitted = true;
  const b = new google.maps.LatLngBounds();
  pts.forEach(p => b.extend(p));
  map.fitBounds(b, 60);
  if (pts.length === 1) map.setZoom(Math.min(map.getZoom(), 14));
}

function clearRideLayer() {
  Object.values(rideMarkers).forEach(m => m.setMap(null)); rideMarkers = {};
  Object.values(ridePolys).forEach(l => l.setMap(null)); ridePolys = {};
  clearTrail();
}

// ---------------------------------------------------------------- page changes

function onPageChange(page) {
  if (window.google && map) setTimeout(() => google.maps.event.trigger(map, "resize"), 60);
  if (page === "fleet" && typeof loadRouteTemplates === "function") loadRouteTemplates();
  if (page === "warehouse" && typeof startWarehouse === "function") startWarehouse();
  if (page === "impact" && typeof startImpact === "function") startImpact();
  if (page === "reports" && typeof startReports === "function") startReports();
  if (page === "dashboard") {
    Object.keys(vehicles || {}).forEach(id => { vehicles[id].setMap(null); delete vehicles[id]; });
    _fitted = false;
    syncControls();
    loadRides();
  } else {
    clearRideLayer();
    if (typeof updateVehicle === "function") Object.keys(trips).forEach(updateVehicle);
  }
}

function setupDashboard() {
  $("dash-live").onclick = goLive;
  $("view-live").onclick = goLive;
  $("dash-date").onchange = setAtFromControls;
  $("dash-slider").oninput = () => {
    const base = dateValue(dash.at == null ? Date.now() / 1000 : dash.at);
    const [y, m, dd] = ($("dash-date").value || base).split("-").map(Number);
    const mins = Number($("dash-slider").value);
    $("dash-time").textContent = `${pad2(Math.floor(mins / 60))}:${pad2(mins % 60)}`;
    clearTimeout(dash.slide);
    dash.slide = setTimeout(setAtFromControls, 120);         // fetch once the slider settles
  };
  $("ride-cols").querySelectorAll("[data-sort]").forEach(b => {
    b.onclick = () => {
      dash.sort = dash.sort.key === b.dataset.sort ? { key: b.dataset.sort, dir: -dash.sort.dir } : { key: b.dataset.sort, dir: 1 };
      renderRides();
    };
  });
  $("btn-change-hub").onclick = () => { switchTab("parcels"); if (!depotEditing) startDepotEdit(); };
  syncControls();
  renderHero();
  setInterval(renderHero, 30000);
  // the map lives in a grid cell whose size changes between pages: always follow it
  if (window.ResizeObserver) {
    new ResizeObserver(() => { if (window.google && map) google.maps.event.trigger(map, "resize"); }).observe($("map-wrap"));
  }
  setInterval(() => {                                        // keep "now" fresh
    if (dashPage() && dash.at == null) { loadRides(); syncControls(); }
  }, 15000);
  // counts in the accordion headers follow their content
  [["manifest", "manifest-count", el => el.querySelectorAll("tr[data-parcel], tr").length],
   ["alerts", "alerts-count", el => el.children.length]].forEach(([id, countId, count]) => {
    new MutationObserver(() => { $(countId).textContent = count($(id)); }).observe($(id), { childList: true });
  });
}


// ---------------------------------------------------------------- dock: what needs doing

const PRIORITY_RANK_UI = { express: 0, priority: 1, standard: 2 };

function unassignedParcels() {
  return Object.values(parcels)
    .filter(p => PLANNABLE.includes(p.status))
    .sort((a, b) => (PRIORITY_RANK_UI[a.priority || "standard"] - PRIORITY_RANK_UI[b.priority || "standard"]) || a.deadline - b.deadline);
}

function dueText(p) {
  const mins = Math.round((p.deadline - Date.now() / 1000) / 60);
  if (mins < 0) return { text: `${fmtMins(mins)} overdue`, tone: "bad" };
  if (mins < 60) return { text: `due in ${mins} min`, tone: "warn" };
  return { text: `due ${clock(p.deadline)}`, tone: "" };
}

function renderDock() {
  const box = $("dock-body");
  if (!box || !dashPage()) return;
  const queue = unassignedParcels();
  const attn = typeof attentionItems === "function" ? attentionItems() : [];
  const fresh = !vehiclesReg.length && !driversReg.length && !Object.keys(parcels).length;

  if (fresh) {
    box.innerHTML = startChecklist();
    box.querySelectorAll("[data-go]").forEach(b => { b.onclick = () => goStep(b.dataset.go); });
    return;
  }

  const attnHtml = attn.length ? attn.slice(0, 6).map((i, n) => `
    <button type="button" class="dock-item level-${i.level}" data-attn="${n}">
      <span class="di-tag"><i aria-hidden="true"></i>${pdEsc(i.tag)}<span class="di-kind"> · ${i.kind === "run" ? "Run" : "Parcel"}</span></span>
      <span class="di-main"><span class="di-title">${pdEsc(i.title)}</span><span class="di-sub">${pdEsc(i.detail)}</span></span>
      <svg viewBox="0 0 24 24" class="i di-go" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg></button>`).join("") +
    `<div class="dock-foot"><button type="button" class="link-action" data-all-attn>View all${attn.length > 6 ? " " + attn.length : ""}</button></div>`
    : `<div class="empty-state"><b>All clear</b>Nothing needs attention right now.</div>`;

  const pickups = queue.filter(p => p.requestType === "pickup");
  const dockParcel = p => {
    const due = dueText(p);
    return `<div class="dock-parcel" draggable="true" data-pid="${p.id}" tabindex="0" role="button" aria-label="Open ${pdEsc(p.trackingNumber || p.name)}">
      <span class="grip" aria-hidden="true"></span>
      <span class="dp-main"><b>${pdEsc(p.trackingNumber || p.name)}</b> ${pdEsc(p.name)}
        <span class="di-sub">${p.requestType === "pickup" && p.pickup ? "Collect · " + pdEsc(p.pickup.label) : pdEsc(p.destination.label)}</span></span>
      <span class="dp-side">${p.priority && p.priority !== "standard" ? `<span class="p-chip${p.priority === "express" ? " fragile" : ""}">${p.priority}</span>` : ""}
        <span class="due ${due.tone}">${due.text}</span></span></div>`;
  };
  const pickupHtml = pickups.map(dockParcel).join("");
  const others = queue.filter(p => p.requestType !== "pickup");
  const queueHtml = others.length ? others.slice(0, 8).map(p => {
    const due = dueText(p);
    return `<div class="dock-parcel" draggable="true" data-pid="${p.id}" tabindex="0" role="button" aria-label="Open ${pdEsc(p.trackingNumber || p.name)}">
      <span class="grip" aria-hidden="true"></span>
      <span class="dp-main"><b>${pdEsc(p.trackingNumber || p.name)}</b> ${pdEsc(p.name)}
        <span class="di-sub">${pdEsc(p.destination.label)}</span></span>
      <span class="dp-side">${p.priority && p.priority !== "standard" ? `<span class="p-chip${p.priority === "express" ? " fragile" : ""}">${p.priority}</span>` : ""}
        <span class="due ${due.tone}">${due.text}</span></span></div>`;
  }).join("") + (others.length > 8 ? `<button type="button" class="link-action" data-all-queue>See all ${others.length} on the Parcels page</button>` : "")
    : `<div class="empty-state"><b>Queue is empty</b>Every parcel is on a run.</div>`;

  box.innerHTML =
    accHtml("Needs attention", attnHtml, { open: true, count: attn.length, warn: attn.some(i => i.level === 0) }) +
    (pickups.length ? accHtml("Pickup requests", `<div class="hint" style="margin-bottom:6px">Customers waiting for a collection. Open one to find the best run, or drag it onto a ride.</div>${pickupHtml}`, { open: true, count: pickups.length }) : "") +
    accHtml("Unassigned parcels", `<div class="hint" style="margin-bottom:6px">Drag a parcel onto a ride in the table to add it to that run.</div>${queueHtml}
      <div class="row" style="margin-top:10px"><button type="button" class="btn btn-ghost" id="dock-add">Add parcel</button>
        <button type="button" class="btn btn-primary" id="dock-suggest"${queue.length ? "" : " disabled"}>Suggest runs</button></div>`,
      { open: true, count: queue.length });

  box.querySelectorAll("[data-attn]").forEach(b => { b.onclick = () => openAttentionItem(attn[Number(b.dataset.attn)]); });
  const all = box.querySelector("[data-all-attn]"); if (all) all.onclick = () => switchTab("attention");
  const allq = box.querySelector("[data-all-queue]"); if (allq) allq.onclick = () => { setStatusFilter("pending"); };
  box.querySelectorAll(".dock-parcel").forEach(el => {
    makeDraggable(el, el.dataset.pid);
    el.onclick = () => openParcelDetail(el.dataset.pid);
    el.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openParcelDetail(el.dataset.pid); } };
  });
  $("dock-add") && ($("dock-add").onclick = () => { switchTab("parcels"); toggleParcelForm(true); $("pf-name").focus(); });
  $("dock-suggest") && ($("dock-suggest").onclick = () => { switchTab("parcels"); autoAssign("rules"); });
}

// first-run guidance: what to do, in order, with the finished steps ticked
function startChecklist() {
  const steps = [
    ["Add a vehicle", "Set its type, cargo size and payload.", vehiclesReg.length > 0, "vehicle"],
    ["Add a driver", "Link them to a vehicle and set their shift.", driversReg.length > 0, "driver"],
    ["Set your hub", "The start point for every run.", false, "hub"],
    ["Add or import parcels", "One by one, or import a CSV or Excel manifest.", Object.keys(parcels).length > 0, "parcel"],
    ["Suggest runs", "Fit parcels onto the fewest vehicles, then approve.", Object.keys(trips).length > 0, "suggest"],
  ];
  return `<h2 style="margin-bottom:4px">Get started</h2><div class="hint" style="margin-bottom:10px">Five steps from an empty system to the first run.</div>` +
    steps.map(([t, d, done, key], i) => `
      <button type="button" class="step${done ? " done" : ""}" data-go="${key}"><span class="n">${done ? "✓" : i + 1}</span>
        <span><b>${t}</b><span class="di-sub">${d}</span></span></button>`).join("");
}

function goStep(key) {
  if (key === "vehicle") { switchTab("registry"); $("btn-add-vehicle").click(); }
  else if (key === "driver") { switchTab("registry"); $("btn-add-driver").click(); }
  else if (key === "hub") { switchTab("parcels"); if (!depotEditing) startDepotEdit(); }
  else if (key === "parcel") { switchTab("parcels"); toggleParcelForm(true); }
  else if (key === "suggest") { switchTab("parcels"); autoAssign("rules"); }
}
