// Predefined routes (route templates) on the Runs page: standing routes with a zone, a default
// driver, service days and recurring pickups. "Run today's route" builds the day's run from one.
// Talks to /api/route-templates; everything it needs from the console (api, $, pdEsc, driversReg,
// trips, selectTrip, loadTrips, loadParcels, roleAtLeast) is global.

let routeTemplates = [];
let rtEditing = null;          // template id being edited, or null for a new one
let rtZone = null;             // {lat, lng, label}
let rtPickups = [];            // recurring pickups in the form
let rtPendingPickup = null;    // place found by the pickup search, not yet added
let rtDays = new Set([0, 1, 2, 3, 4]);
const RT_DAYS = ["M", "T", "W", "T", "F", "S", "S"];
const RT_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function setupRoutes() {
  if (!$("route-list")) return;
  $("btn-new-route").onclick = () => openRouteForm(null);
  $("rt-cancel").onclick = closeRouteForm;
  $("rt-save").onclick = saveRouteTemplate;
  $("rt-pk-add").onclick = addRoutePickup;
  searchOnEnter("rt-zone", "rt-zone-err", place => {
    rtZone = place;
    $("rt-zone-label").textContent = place.label;
    try { map.panTo({ lat: place.lat, lng: place.lng }); } catch (_) {}
  });
  searchOnEnter("rt-pk-search", "rt-pk-err", place => {
    rtPendingPickup = place;
    $("rt-pk-label").textContent = place.label;
  });
  $("rt-days").innerHTML = RT_DAYS.map((d, i) =>
    `<button type="button" class="day-btn" data-d="${i}" aria-pressed="false" title="${RT_DAY_NAMES[i]}">${d}</button>`).join("");
  $("rt-days").querySelectorAll(".day-btn").forEach(b => {
    b.onclick = () => {
      const d = Number(b.dataset.d);
      rtDays.has(d) ? rtDays.delete(d) : rtDays.add(d);
      syncRouteDays();
    };
  });
}

function searchOnEnter(inputId, errId, onFound) {
  $(inputId).addEventListener("keydown", async e => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const q = $(inputId).value.trim();
    if (!q) return;
    $(errId).hidden = true;
    $(inputId).classList.add("busy");
    try {
      onFound(await api(`/api/geocode?q=${encodeURIComponent(q)}`));
      $(inputId).value = "";
    } catch (_) {
      $(errId).textContent = `Couldn’t find “${q}”. Try a more specific name.`;
      $(errId).hidden = false;
    } finally { $(inputId).classList.remove("busy"); }
  });
}

function syncRouteDays() {
  $("rt-days").querySelectorAll(".day-btn").forEach(b => b.setAttribute("aria-pressed", String(rtDays.has(Number(b.dataset.d)))));
}

async function loadRouteTemplates() {
  if (!$("route-list")) return;
  try { routeTemplates = await api("/api/route-templates"); }
  catch (err) { $("route-list").innerHTML = `<div class="hint">Couldn’t load the routes: ${pdEsc(err.message)}</div>`; return; }
  renderRouteTemplates();
}

function driverOptions(selected) {
  const busy = new Set(Object.values(trips).filter(t => t.status !== "completed").map(t => t.driverId));
  return `<option value="">Choose a driver</option>` + driversReg.map(d =>
    `<option value="${d.id}" ${d.id === selected ? "selected" : ""} ${busy.has(d.id) ? "disabled" : ""}>${pdEsc(d.name)}${busy.has(d.id) ? " — on a run" : ""}</option>`).join("");
}

function renderRouteTemplates() {
  const box = $("route-list");
  if (!routeTemplates.length) {
    box.innerHTML = `<div class="empty-state"><b>No predefined routes yet</b>A supervisor can define standing routes: a zone, a driver, service days and recurring pickups.</div>`;
    return;
  }
  box.innerHTML = "";
  routeTemplates.forEach(t => {
    const driver = driversReg.find(d => d.id === t.driverId);
    const card = document.createElement("div");
    card.className = "rcard";
    card.innerHTML = `
      <div class="rc-head"><span class="rc-code">${pdEsc(t.code)}</span><b>${pdEsc(t.name)}</b>
        <span class="pill ${t.runsToday ? "ok" : ""}">${t.runsToday ? "Runs today" : "Not today"}</span>
        <span class="rc-tools"><button type="button" class="icon-btn rc-edit" aria-label="Edit ${pdEsc(t.name)}" title="Edit">${iconSvg("edit")}</button>
          <button type="button" class="icon-btn rc-del" aria-label="Delete ${pdEsc(t.name)}" title="Delete">${iconSvg("trash")}</button></span></div>
      <div class="rc-meta">${pdEsc(t.zone.label || "Zone")} · ${t.zone.radiusKm} km radius${t.startTime ? " · starts " + pdEsc(t.startTime) : ""}</div>
      <div class="rc-days" aria-label="Service days">${RT_DAYS.map((d, i) => `<span class="${t.days.includes(i) ? "on" : ""}" title="${RT_DAY_NAMES[i]}">${d}</span>`).join("")}</div>
      <div class="rc-stats"><span><b>${t.zoneParcelCount}</b> pending in zone</span><span><b>${t.pickups.length}</b> recurring pickup${t.pickups.length === 1 ? "" : "s"}</span>
        <span>${driver ? "Driver " + pdEsc(driver.name) : "No default driver"}</span></div>
      <div class="rc-actions"><select class="rc-driver" aria-label="Driver for ${pdEsc(t.name)}">${driverOptions(t.driverId)}</select>
        <button type="button" class="btn btn-primary rc-run">Run today’s route</button>
        <button type="button" class="btn btn-ghost rc-preview-btn" aria-expanded="false">What’s included</button></div>
      <div class="field-err form-err rc-err" role="alert" hidden></div>
      <div class="rc-preview" hidden></div>`;
    const err = card.querySelector(".rc-err");
    card.querySelector(".rc-run").onclick = async e => {
      const btn = e.currentTarget, sel = card.querySelector(".rc-driver");
      err.hidden = true;
      if (!sel.value) { err.textContent = "Choose a driver first."; err.hidden = false; return; }
      btn.disabled = true; btn.setAttribute("aria-busy", "true");
      try {
        const trip = await api(`/api/route-templates/${t.id}/run`, { method: "POST", body: JSON.stringify({ driverId: sel.value }) });
        await Promise.all([loadTrips(), loadParcels()]);
        await loadRouteTemplates();
        Promise.resolve(selectTrip(trip.id)).catch(() => {});       // opening the run is a convenience; the run already exists
      } catch (e2) { err.textContent = e2.message; err.hidden = false; btn.disabled = false; btn.removeAttribute("aria-busy"); }
    };
    card.querySelector(".rc-preview-btn").onclick = async e => {
      const pv = card.querySelector(".rc-preview"), btn = e.currentTarget;
      if (!pv.hidden) { pv.hidden = true; btn.setAttribute("aria-expanded", "false"); return; }
      pv.hidden = false; btn.setAttribute("aria-expanded", "true");
      pv.innerHTML = `<div class="hint">Checking…</div>`;
      try {
        const r = await api(`/api/route-templates/${t.id}/preview`);
        pv.innerHTML = `
          <h4>Recurring pickups</h4>${r.pickups.length ? r.pickups.map(p => `<div class="rc-line"><span>${pdEsc(p.label)}${p.readyBy ? ` · cut-off ${pdEsc(p.readyBy)}` : ""}</span><span class="pill ${p.state === "will be created" ? "info" : ""}">${pdEsc(p.state)}</span></div>`).join("") : `<div class="hint">None.</div>`}
          <h4>Pending parcels in this zone</h4>${r.zoneParcels.length ? r.zoneParcels.map(p => `<div class="rc-line"><span>${pdEsc(p.trackingNumber || p.name)} · ${pdEsc(p.name)}</span><span class="pill">${p.requestType === "pickup" ? "pickup" : "delivery"}</span></div>`).join("") : `<div class="hint">None right now.</div>`}`;
      } catch (e2) { pv.innerHTML = `<div class="hint">${pdEsc(e2.message)}</div>`; }
    };
    card.querySelector(".rc-edit").onclick = () => openRouteForm(t);
    card.querySelector(".rc-del").onclick = async () => {
      if (!confirm(`Delete the route ${t.code} — ${t.name}? Runs already created from it are kept.`)) return;
      try { await api(`/api/route-templates/${t.id}`, { method: "DELETE" }); await loadRouteTemplates(); }
      catch (e2) { err.textContent = e2.message; err.hidden = false; }
    };
    box.append(card);
  });
}

function openRouteForm(t) {
  rtEditing = t ? t.id : null;
  rtZone = t ? { lat: t.zone.lat, lng: t.zone.lng, label: t.zone.label || "" } : null;
  rtPickups = t ? t.pickups.map(p => ({ ...p })) : [];
  rtDays = new Set(t ? t.days : [0, 1, 2, 3, 4]);
  rtPendingPickup = null;
  $("rt-code").value = t ? t.code : "";
  $("rt-name").value = t ? t.name : "";
  $("rt-radius").value = t ? t.zone.radiusKm : 5;
  $("rt-start").value = t && t.startTime ? t.startTime : "08:00";
  $("rt-driver").innerHTML = driversReg.map(d => `<option value="${d.id}">${pdEsc(d.name)}</option>`).join("");
  $("rt-driver").insertAdjacentHTML("afterbegin", `<option value="">No default driver</option>`);
  $("rt-driver").value = t && t.driverId ? t.driverId : "";
  $("rt-zone-label").textContent = rtZone ? rtZone.label || `${rtZone.lat.toFixed(4)}, ${rtZone.lng.toFixed(4)}` : "No zone centre set";
  $("rt-pk-label").textContent = "No place found yet";
  ["rt-zone", "rt-pk-search", "rt-pk-name", "rt-pk-ready", "rt-pk-by"].forEach(id => { $(id).value = ""; });
  ["rt-err", "rt-zone-err", "rt-pk-err"].forEach(id => { $(id).hidden = true; });
  $("rt-title").textContent = t ? `Edit ${t.code}` : "New predefined route";
  syncRouteDays();
  renderRoutePickups();
  $("route-form").hidden = false;
  $("rt-code").focus();
}

function closeRouteForm() { $("route-form").hidden = true; rtEditing = null; }

function renderRoutePickups() {
  $("rt-pickups").innerHTML = rtPickups.length ? rtPickups.map((p, i) => `
    <div class="rc-line"><span><b>${pdEsc(p.name || "Pickup")}</b> · ${pdEsc(p.label)}${p.readyBy ? ` · cut-off ${pdEsc(p.readyBy)}` : ""}${p.collectBy ? ` · by ${pdEsc(p.collectBy)}` : ""}</span>
      <button type="button" class="icon-btn" data-rm="${i}" aria-label="Remove ${pdEsc(p.label)}">${iconSvg("trash")}</button></div>`).join("")
    : `<div class="hint">No recurring pickups. Add the customers collected on this route.</div>`;
  $("rt-pickups").querySelectorAll("[data-rm]").forEach(b => { b.onclick = () => { rtPickups.splice(Number(b.dataset.rm), 1); renderRoutePickups(); }; });
}

function addRoutePickup() {
  const err = $("rt-pk-err");
  if (!rtPendingPickup) { err.textContent = "Search for the pickup address and press Enter first."; err.hidden = false; return; }
  rtPickups.push({ ...rtPendingPickup, name: $("rt-pk-name").value.trim() || null,
    readyBy: $("rt-pk-ready").value || null, collectBy: $("rt-pk-by").value || null, size: "small" });
  rtPendingPickup = null;
  ["rt-pk-search", "rt-pk-name", "rt-pk-ready", "rt-pk-by"].forEach(id => { $(id).value = ""; });
  $("rt-pk-label").textContent = "No place found yet";
  err.hidden = true;
  renderRoutePickups();
}

async function saveRouteTemplate() {
  const err = $("rt-err");
  err.hidden = true;
  if (!$("rt-code").value.trim() || !$("rt-name").value.trim()) { err.textContent = "Give the route a code and a name."; err.hidden = false; return; }
  if (!rtZone) { err.textContent = "Search for the zone centre and press Enter."; err.hidden = false; return; }
  const body = {
    code: $("rt-code").value.trim(), name: $("rt-name").value.trim(),
    zone: { lat: rtZone.lat, lng: rtZone.lng, label: rtZone.label || null, radiusKm: Number($("rt-radius").value) },
    driverId: $("rt-driver").value || null, startTime: $("rt-start").value || null,
    days: [...rtDays].sort(), pickups: rtPickups,
  };
  const btn = $("rt-save");
  btn.disabled = true; btn.setAttribute("aria-busy", "true");
  try {
    await api(rtEditing ? `/api/route-templates/${rtEditing}` : "/api/route-templates",
      { method: rtEditing ? "PUT" : "POST", body: JSON.stringify(body) });
    closeRouteForm();
    await loadRouteTemplates();
  } catch (e) { err.textContent = e.message; err.hidden = false; }
  finally { btn.disabled = false; btn.removeAttribute("aria-busy"); }
}
