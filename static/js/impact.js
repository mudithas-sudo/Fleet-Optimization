// Impact screen: what the optimisation is worth — savings against the as-entered order,
// fuel and CO2, planned vs actual, ETA accuracy, and the assumptions behind every figure.
// Reads GET /api/impact; fuel price and consumption are editable (PUT /api/settings/fuel).

let imDays = "7";

function setupImpact() {
  $("btn-impact").onclick = openImpact;
  $("im-close").onclick = closeImpact;
  $("impact").addEventListener("click", e => { if (e.target === $("impact")) closeImpact(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && $("impact").style.display === "flex") closeImpact(); });
  $("im-days").onchange = () => { imDays = $("im-days").value; loadImpact(); };
  $("im-fuel-save").onclick = saveFuel;
}

function openImpact() {
  $("impact").style.display = "flex";
  $("im-days").value = imDays;
  loadImpact();
  $("im-close").focus();
}

function closeImpact() { $("impact").style.display = "none"; }

const imN = (n, d = 0) => n == null ? "–" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d });

async function loadImpact() {
  const days = imDays;
  $("im-status").textContent = "Loading…";
  let r;
  try { r = await api(`/api/impact?days=${days}`); }
  catch (err) { $("im-status").textContent = "Couldn’t load the impact figures: " + err.message; return; }
  if (days !== imDays) return;
  $("im-status").textContent = "";
  const s = r.savings, f = r.footprint, cur = r.currency;
  const period = { today: "today", "7": "in the last 7 days", "30": "in the last 30 days", all: "so far" }[days] || "";

  const yearly = s.perYear.factor && days !== "all" ? Math.round(s.savedCost * s.perYear.factor) : null;
  $("im-hero").innerHTML = s.runs ? `
    <div class="im-lead">
      <span class="im-eyebrow">Saved ${period} on ${s.runs} run${s.runs === 1 ? "" : "s"}</span>
      <b>${imN(s.savedKm, 1)} km <small>${s.savedPct}% less driving</small></b>
      <p>Compared with the same parcels visited in the order they were entered. ${yearly
        ? `Repeated all year this is worth about <b>${cur} ${imN(yearly)}</b>.` : ""}</p>
    </div>
    <div class="im-stats">
      <div><b>${imN(s.savedHours, 1)} h</b><span>driver time</span></div>
      <div><b>${imN(s.savedLitres, 1)} L</b><span>fuel</span></div>
      <div><b>${imN(s.savedCo2Kg, 1)} kg</b><span>CO₂</span></div>
      <div><b>${cur} ${imN(s.savedCost)}</b><span>operating cost</span></div>
    </div>` : `<div class="hint">No runs in this period yet — plan a run to see the savings.</div>`;

  const kpi = (v, label, note = "") => `<div class="rp-kpi"><b>${v}</b><span>${label}</span>${note ? `<small>${note}</small>` : ""}</div>`;
  const sv = r.service, u = r.utilisation, e = r.eta, p = r.planVsActual;
  $("im-kpis").innerHTML = [
    kpi(imN(f.km, 1) + " km", "Driven (optimised)", `${imN(s.baselineKm, 1)} km without optimisation`),
    kpi(imN(f.litres, 1) + " L", "Fuel used", `${cur} ${imN(f.fuelCost)}`),
    kpi(imN(f.co2Kg, 1) + " kg", "CO₂ emitted", "from the fuel above"),
    kpi(sv.onTimeRate == null ? "–" : sv.onTimeRate + "%", "On time", `${imN(sv.delivered)} delivered`),
    kpi(sv.firstAttemptRate == null ? "–" : sv.firstAttemptRate + "%", "First attempt", `${imN(sv.failedAttempts)} failed attempts`),
    kpi(u.avgLoadPct == null ? "–" : u.avgLoadPct + "%", "Vehicle fill", `${imN(u.parcelsPerRun, 1)} parcels per run`),
    kpi(e.within15Pct == null ? "–" : e.within15Pct + "%", "ETA within ±15 min", e.stops ? `average miss ${imN(e.meanErrorMin, 1)} min · ${e.stops} stops` : "needs finished runs"),
    kpi(sv.costPerDelivery == null ? "–" : `${cur} ${imN(sv.costPerDelivery)}`, "Cost per delivery", "km and hours at your rates"),
  ].join("");

  const max = Math.max(1, ...r.perRun.map(x => x.baselineKm));
  $("im-runs").innerHTML = r.perRun.length ? `
    <thead><tr><th>Run</th><th>Driver · vehicle</th><th class="n">Stops</th><th>Distance: before → after</th>
      <th class="n">Saved</th><th class="n">Fuel saved</th><th class="n">CO₂ saved</th></tr></thead>
    <tbody>${r.perRun.map(x => `<tr>
      <td>${pdEsc(x.label || x.tripId)}</td><td>${pdEsc(x.driver || "–")} · ${pdEsc(x.vehicle || x.vehicleType)}</td>
      <td class="n">${x.stops}</td>
      <td><div class="im-pair" title="${x.baselineKm} km → ${x.optimisedKm} km">
        <i class="before" style="width:${x.baselineKm / max * 100}%"></i><i class="after" style="width:${x.optimisedKm / max * 100}%"></i></div>
        <small>${imN(x.baselineKm, 1)} → ${imN(x.optimisedKm, 1)} km</small></td>
      <td class="n"><b>${imN(x.savedKm, 1)} km</b><small>${x.savedPct}%</small></td>
      <td class="n">${imN(x.savedLitres, 1)} L</td><td class="n">${imN(x.savedCo2Kg, 1)} kg</td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="hint">No runs in this period.</td></tr></tbody>`;

  $("im-pva").innerHTML = p.runs.length ? `
    <div class="im-pva-sum">Finished runs drove <b>${imN(p.actualKm, 1)} km</b> against <b>${imN(p.plannedKm, 1)} km</b> planned
      (${p.kmVarPct > 0 ? "+" : ""}${p.kmVarPct}%) and took <b>${imN(p.actualMin)} min</b> against <b>${imN(p.plannedMin)} min</b>
      (${p.minVarPct > 0 ? "+" : ""}${p.minVarPct}%).</div>
    <table class="im-table"><thead><tr><th>Run</th><th class="n">Planned km</th><th class="n">Actual km</th><th class="n">Δ</th>
      <th class="n">Planned min</th><th class="n">Actual min</th><th class="n">Δ</th><th class="n">Off-route</th></tr></thead>
    <tbody>${p.runs.map(x => `<tr><td>${pdEsc(x.label || x.tripId)}</td><td class="n">${x.plannedKm}</td><td class="n">${x.actualKm}</td>
      <td class="n">${x.kmVarPct > 0 ? "+" : ""}${x.kmVarPct}%</td><td class="n">${x.plannedMin}</td><td class="n">${x.actualMin}</td>
      <td class="n">${x.minVarPct > 0 ? "+" : ""}${x.minVarPct}%</td><td class="n">${x.deviations}</td></tr>`).join("")}</tbody></table>`
    : `<div class="hint">Appears once a run has finished: planned distance and time next to what the vehicle actually did.</div>`;

  $("im-method").textContent = r.method;
  $("im-price").value = r.fuel.pricePerL;
  $("im-co2").value = r.fuel.co2PerL;
  ["bike", "car", "van", "truck"].forEach(t => { $(`im-l-${t}`).value = r.fuel.l100[t]; });
  $("im-cur").textContent = cur;
}

async function saveFuel() {
  const btn = $("im-fuel-save");
  btn.disabled = true;
  try {
    await api("/api/settings/fuel", { method: "PUT", body: JSON.stringify({
      pricePerL: Number($("im-price").value) || 0, co2PerL: Number($("im-co2").value) || null,
      l100: Object.fromEntries(["bike", "car", "van", "truck"].map(t => [t, Number($(`im-l-${t}`).value) || 0])) }) });
    await loadImpact();
  } catch (err) { $("im-status").textContent = err.message; }
  finally { btn.disabled = false; }
}
