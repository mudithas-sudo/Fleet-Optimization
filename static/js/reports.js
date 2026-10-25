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

  const fmax = Math.max(1, ...r.failuresByReason.map(f => f.count));
  $("rp-fail").innerHTML = r.failuresByReason.length ? r.failuresByReason.map(f => `
    <div class="rp-hbar"><span>${pdEsc(f.reason)}</span>
      <div><i style="width:${f.count / fmax * 100}%"></i></div><b>${f.count}</b></div>`).join("")
    : `<div class="hint">No failed deliveries in this period.</div>`;


}
