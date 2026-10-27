// Reports overlay: KPIs, deliveries per day, failure reasons, driver table,
// cost rates, CSV exports and recent customer messages.

let rpDays = "7";

function setupReports() {
  $("rp-days").onchange = () => { rpDays = $("rp-days").value; loadReport(); };
}

// Reports is a page (opened from the side menu); this runs when it is shown
function startReports() {
  $("rp-days").value = rpDays;
  loadReport();
}

const rpNum = (n, d = 0) => n == null ? "–" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d });
const rpPct = n => n == null ? "–" : n + "%";

async function loadReport() {
  const days = rpDays;
  $("rp-status").textContent = "Loading…";
  let r;
  try {
    r = await api(`/api/reports/summary?days=${days}`);
  } catch (err) {
    $("rp-status").textContent = "Couldn’t load the report: " + err.message;
    return;
  }
  if (days !== rpDays) return;
  $("rp-status").textContent = "";
  const p = r.parcels, c = r.cost;

  const kpi = (value, label, note = "") =>
    `<div class="rp-kpi"><b>${value}</b><span>${label}</span>${note ? `<small>${note}</small>` : ""}</div>`;
  $("rp-kpis").innerHTML = [
    kpi(rpNum(p.delivered), "Delivered", `${rpNum(p.created)} parcels added`),
    kpi(rpPct(p.onTimeRate), "On time", `${rpNum(p.late)} late`),
    kpi(rpPct(p.firstAttemptRate), "First attempt", "delivered without a retry"),
    kpi(rpNum(p.failedAttempts), "Failed attempts", `${rpNum(p.returned)} returned`),
    kpi(rpNum(r.runs.count), "Runs", `${rpNum(r.runs.completed)} finished`),
    kpi(rpNum(r.runs.km, 1) + " km", "Distance", `${rpNum(r.runs.plannedKm, 1)} km planned · ${rpNum(r.runs.hours, 1)} h · driven once a run ends`),
    kpi(c.perDelivery == null ? "–" : `${c.currency} ${rpNum(c.perDelivery)}`, "Cost per delivery", `${c.currency} ${rpNum(c.total)} total`),
  ].join("");

  // deliveries per day (last 30 days shown)
  const days30 = r.daily.slice(-30);
  const max = Math.max(1, ...days30.map(d => d.delivered + d.failed));
  $("rp-daily").innerHTML = days30.length ? `<div class="rp-bars" role="img" aria-label="Deliveries and failed attempts per day">${
    days30.map(d => `<div class="rp-bar" title="${d.date}: ${d.delivered} delivered, ${d.failed} failed">
      <div class="seg-fail" style="height:${d.failed / max * 100}%"></div>
      <div class="seg-ok" style="height:${d.delivered / max * 100}%"></div>
      <small>${d.date.slice(5)}</small></div>`).join("")}</div>
    <div class="rp-legend"><i class="ok"></i> Delivered <i class="bad"></i> Failed attempts</div>`
    : `<div class="hint">No deliveries in this period.</div>`;

  loadInsights(days);

  const fmax = Math.max(1, ...r.failuresByReason.map(f => f.count));
  $("rp-fail").innerHTML = r.failuresByReason.length ? r.failuresByReason.map(f => `
    <div class="rp-hbar"><span>${pdEsc(f.reason)}</span>
      <div><i style="width:${f.count / fmax * 100}%"></i></div><b>${f.count}</b></div>`).join("")
    : `<div class="hint">No failed deliveries in this period.</div>`;


}


// what to act on: cost of failures, weak sellers and areas, service level, delivery hours
async function loadInsights(days) {
  const box = $("rp-insights");
  if (!box) return;
  let r;
  try { r = await api(`/api/reports/insights?days=${days}`); } catch (_) { box.innerHTML = ""; return; }
  if (days !== rpDays) return;
  const f = r.failedCost, pct = v => v == null ? "–" : v + "%";
  const prio = { express: "Express", priority: "Priority", standard: "Standard" };
  const hmax = Math.max(1, ...r.byHour);
  const first = r.byHour.findIndex(n => n), last = r.byHour.length - 1 - [...r.byHour].reverse().findIndex(n => n);
  const hours = first < 0 ? "" : r.byHour.slice(first, last + 1).map((n, i) => `<div class="rp-bar" title="${first + i}:00, ${n} delivered"><div class="seg-ok" style="height:${n / hmax * 100}%"></div><small>${first + i}</small></div>`).join("");
  const table = (head, rows) => `<div class="rp-tablewrap"><table><thead><tr>${head.map((h, i) => `<th${i ? ' class="n"' : ""}>${h}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
  box.innerHTML = `
    <div class="rp-kpis">
      <div class="rp-kpi"><b>${f.currency} ${rpNum(f.total)}</b><span>Cost of failed attempts</span><small>${rpNum(f.attempts)} attempts × ${f.currency} ${rpNum(f.perAttempt)} (assumed)</small></div>
      <div class="rp-kpi"><b>${rpNum(f.preventableParcels)}</b><span>Failed for a preventable reason</span><small>nobody home, bad address, refused: a confirmation call helps</small></div>
    </div>
    <div class="rp-grid">
      <div class="rp-panel"><h3>Where deliveries fail most</h3>${r.areas.length ? table(["Area", "Parcels", "First time", "Failed attempts"],
        r.areas.map(a => `<tr><td>${pdEsc(a.area)}</td><td class="n">${a.parcels}</td><td class="n">${pct(a.firstAttemptPct)}</td><td class="n">${a.failedAttempts}</td></tr>`).join("")) : `<div class="hint">Not enough finished parcels yet.</div>`}</div>
      <div class="rp-panel"><h3>Service level by priority</h3>${r.service.length ? table(["Service", "Delivered", "On time"],
        r.service.map(s => `<tr><td>${prio[s.priority]}</td><td class="n">${s.delivered}</td><td class="n">${pct(s.onTimePct)}</td></tr>`).join("")) : `<div class="hint">No deliveries yet.</div>`}
        <h3 style="margin-top:14px">When deliveries happen</h3>${hours ? `<div class="rp-bars" role="img" aria-label="Deliveries by hour of the day">${hours}</div>` : `<div class="hint">No deliveries yet.</div>`}</div>
    </div>
    <div class="rp-panel"><h3>Sellers</h3>${r.sellers.length ? table(["Seller", "Parcels", "First time", "Returned", "Cash collected", "Still owed"],
      r.sellers.map(s => `<tr><td>${pdEsc(s.seller)}</td><td class="n">${s.parcels}</td><td class="n">${pct(s.firstAttemptPct)}</td><td class="n">${pct(s.returnedPct)}</td><td class="n">${rpNum(s.codCollected)}</td><td class="n">${rpNum(s.codOwed)}</td></tr>`).join(""))
      : `<div class="hint">No seller data yet: add the seller when you create cash-on-delivery parcels.</div>`}</div>`;
}
