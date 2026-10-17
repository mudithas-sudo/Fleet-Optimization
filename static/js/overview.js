// Dispatcher overview: today's numbers, parcel search/filters and the
// "needs attention" queue. Reads the globals owned by admin.js (parcels, trips,
// driversReg, vehiclesReg …) at call time, so this file loads before it.

const FILTERS = { status: null, q: "", driverId: "", when: "" };

const HOUR = 3600;
const SIZE_RANK = { small: 0, medium: 1, large: 2 };

function startOfToday() { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; }
function endOfToday() { return startOfToday() + 24 * HOUR; }
const nowSec = () => Date.now() / 1000;

const isOpen = p => p.status !== "delivered";
const isPending = p => PLANNABLE.includes(p.status);
const isOut = p => ["assigned", "in_transit", "picked_up"].includes(p.status);
const isLate = p => isOpen(p) && p.deadlineMissed;
const driverOf = p => (p.tripId && trips[p.tripId] && trips[p.tripId].driverId) || "";

// ---------------------------------------------------------------- filters

function parcelMatchesFilters(p) {
  const f = FILTERS;
  if (f.status === "pending" && !isPending(p)) return false;
  if (f.status === "out" && !isOut(p)) return false;
  if (f.status === "delivered" && p.status !== "delivered") return false;
  if (f.status === "late" && !isLate(p)) return false;
  if (f.driverId && driverOf(p) !== f.driverId) return false;
  if (f.when === "dueToday" && !(isOpen(p) && p.deadline < endOfToday())) return false;
  if (f.when === "due2h" && !(isOpen(p) && p.deadline < nowSec() + 2 * HOUR)) return false;
  if (f.when === "deliveredToday" && !(p.status === "delivered" && (p.deliveredAt || 0) >= startOfToday())) return false;
  if (f.q) {
    const hay = [p.name, p.trackingNumber, p.receiverName, p.recipientPhone, p.type,
      p.destination && p.destination.label, p.pickup && p.pickup.label]
      .filter(Boolean).join(" ").toLowerCase();
    if (!f.q.toLowerCase().split(/\s+/).every(w => hay.includes(w))) return false;
  }
  return true;
}

function filtersActive() { return !!(FILTERS.status || FILTERS.q || FILTERS.driverId || FILTERS.when); }

function clearFilters() {
  FILTERS.status = null; FILTERS.q = ""; FILTERS.driverId = ""; FILTERS.when = "";
  $("flt-q").value = ""; $("flt-driver").value = ""; $("flt-when").value = "";
  renderParcels();
}

function setStatusFilter(status) {
  FILTERS.status = FILTERS.status === status ? null : status;
  FILTERS.when = FILTERS.status === "delivered" && FILTERS.when === "" ? "deliveredToday" : "";
  $("flt-when").value = FILTERS.when;
  switchTab("parcels");
  renderParcels();
}

// ------------------------------------------------------------- today strip

function renderStats() {
  const all = Object.values(parcels);
  const today = startOfToday();
  const deliveredToday = all.filter(p => p.status === "delivered" && (p.deliveredAt || 0) >= today);
  const onTime = deliveredToday.filter(p => !p.deadlineMissed).length;
  const liveRuns = Object.values(trips).filter(t => LIVE.includes(t.status)).length;
  const late = all.filter(isLate).length;

  $("st-pending").textContent = all.filter(isPending).length;
  $("st-out").textContent = all.filter(isOut).length;
  $("st-out-sub").textContent = liveRuns ? `${liveRuns} run${liveRuns === 1 ? "" : "s"} live` : "none live";
  $("st-delivered").textContent = deliveredToday.length;
  $("st-late").textContent = late;
  $("st-ontime").textContent = deliveredToday.length ? Math.round(onTime / deliveredToday.length * 100) + "%" : "–";
  document.querySelector('[data-f="late"]').classList.toggle("alert", late > 0);

  document.querySelectorAll("#today [data-f]").forEach(b => {
    b.setAttribute("aria-pressed", String(FILTERS.status === b.dataset.f));
  });
}

// ------------------------------------------------------- driver filter list

let _driverKey = "";
function syncDriverFilter() {
  const key = driversReg.map(d => d.id + d.name).join("|");
  if (key === _driverKey) return;
  _driverKey = key;
  const sel = $("flt-driver");
  const keep = FILTERS.driverId;
  sel.innerHTML = `<option value="">All drivers</option>` +
    driversReg.map(d => `<option value="${d.id}">${d.name}</option>`).join("");
  sel.value = keep;
}

function renderFilterNote(shown, total) {
  const el = $("filter-note");
  if (!filtersActive()) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `Showing ${shown} of ${total} parcels · <button type="button" class="link-action" id="flt-clear">Clear filters</button>`;
  $("flt-clear").onclick = clearFilters;
}

// ---------------------------------------------------------- attention queue

function fitsAnyVehicle(p) {
  if (!vehiclesReg.length) return true;
  return vehiclesReg.some(v => {
    if (SIZE_RANK[p.size] > SIZE_RANK[v.maxParcelSize || "large"]) return false;
    const c = v.cargoCm || (CARGO_DEFAULTS[v.type] ? { l: CARGO_DEFAULTS[v.type][0], w: CARGO_DEFAULTS[v.type][1], h: CARGO_DEFAULTS[v.type][2] } : null);
    const kg = v.maxPayloadKg || (CARGO_DEFAULTS[v.type] ? CARGO_DEFAULTS[v.type][3] : Infinity);
    if (p.dimensionsCm && c) {
      const a = [p.dimensionsCm.l, p.dimensionsCm.w, p.dimensionsCm.h].sort((x, y) => x - y);
      const b = [c.l, c.w, c.h].sort((x, y) => x - y);
      if (a.some((x, i) => x > b[i])) return false;
    }
    return !(p.weightKg && p.weightKg > kg);
  });
}

function fmtMins(m) {
  m = Math.abs(Math.round(m));
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}

function attentionItems() {
  const items = [];
  const atRisk = new Set();
  Object.values(trips).forEach(t => {
    if (t.status !== "completed") (t.atRiskParcelIds || []).forEach(id => atRisk.add(id));
  });

  Object.values(parcels).filter(isOpen).forEach(p => {
    const mins = (p.deadline - nowSec()) / 60;
    const who = [p.trackingNumber, p.name].filter(Boolean).join(" · ");
    const where = [p.receiverName, p.destination && p.destination.label].filter(Boolean).join(", ");
    const base = { kind: "parcel", id: p.id, title: who, where };
    if (isLate(p)) {
      items.push({ ...base, level: 0, tag: "Late", detail: `${fmtMins(mins)} past the deadline · ${p.status.replace(/_/g, " ")}`, sort: p.deadline });
    } else if (isPending(p) && !fitsAnyVehicle(p)) {
      items.push({ ...base, level: 1, tag: "Can’t be carried", detail: "No vehicle in the fleet can take this parcel — split it, or add a larger vehicle.", sort: p.deadline });
    } else if (atRisk.has(p.id)) {
      items.push({ ...base, level: 1, tag: "At risk", detail: `Planned arrival is after the deadline (due in ${fmtMins(mins)}).`, sort: p.deadline });
    } else if (isPending(p) && mins < 60) {
      items.push({ ...base, level: 1, tag: "Due soon, unassigned", detail: `Due in ${fmtMins(mins)} and not on a run yet.`, sort: p.deadline });
    }
  });

  Object.values(trips).forEach(t => {
    const base = { kind: "run", id: t.id, title: t.label || t.id, where: [t.driverName, t.vehicleType].filter(Boolean).join(" · "), sort: -1 };
    if (t.status === "deviating") {
      items.push({ ...base, level: 0, tag: "Off route", detail: "The driver has left the planned route — the system is re-routing." });
    } else if (LIVE.includes(t.status) && t.risk) {
      items.push({ ...base, level: 1, tag: t.risk === "stopped" ? "Vehicle stopped" : "Pickup at risk", detail: t.risk === "stopped" ? "No movement for a while — check on the driver." : "The collection window may be missed." });
    }
  });

  return items.sort((a, b) => a.level - b.level || a.sort - b.sort);
}

function renderAttention() {
  const items = attentionItems();
  const critical = items.filter(i => i.level === 0).length;
  const badge = $("attention-badge");
  badge.hidden = !items.length;
  badge.textContent = items.length;
  badge.classList.toggle("warn", critical > 0);

  const wrap = $("attention-list");
  wrap.innerHTML = "";
  if (!items.length) {
    wrap.innerHTML = `<div class="empty">${iconSvg("check", "i-lg")}<b>Nothing needs attention</b>Late, at-risk, off-route and unassigned items will show up here.</div>`;
    return;
  }
  items.forEach(i => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `attn-item level-${i.level}`;
    b.innerHTML = `
      <div class="attn-top"><span class="attn-tag">${i.tag}</span><span class="attn-kind">${i.kind === "run" ? "Run" : "Parcel"}</span></div>
      <div class="attn-title">${i.title}</div>
      ${i.where ? `<div class="attn-where">${i.where}</div>` : ""}
      <div class="attn-detail">${i.detail}</div>`;
    b.onclick = () => openAttentionItem(i);
    wrap.append(b);
  });
}

function openAttentionItem(i) {
  if (i.kind === "run") {
    switchTab("fleet");
    selectTrip(i.id);
    return;
  }
  const p = parcels[i.id];
  FILTERS.status = null; FILTERS.driverId = ""; FILTERS.when = "";
  FILTERS.q = (p && (p.trackingNumber || p.name)) || "";
  $("flt-q").value = FILTERS.q; $("flt-driver").value = ""; $("flt-when").value = "";
  switchTab("parcels");
  renderParcels();
}

// ------------------------------------------------------------------- glue

function refreshOverview() {
  try {
    syncDriverFilter();
    renderStats();
    renderAttention();
  } catch (e) { console.warn("overview refresh failed", e); }
}

function setupOverview() {
  document.querySelectorAll("#today [data-f]").forEach(b => {
    b.onclick = () => setStatusFilter(b.dataset.f);
  });
  $("flt-q").addEventListener("input", e => { FILTERS.q = e.target.value.trim(); renderParcels(); });
  $("flt-driver").addEventListener("change", e => { FILTERS.driverId = e.target.value; renderParcels(); });
  $("flt-when").addEventListener("change", e => { FILTERS.when = e.target.value; renderParcels(); });
  setInterval(refreshOverview, 30000);   // "past the deadline" minutes drift
}
