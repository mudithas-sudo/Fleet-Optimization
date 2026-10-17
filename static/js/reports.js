// Reports overlay: KPIs, deliveries per day, failure reasons, driver table,
// cost rates, CSV exports and recent customer messages.

let rpDays = "7";

function setupReports() {
  $("btn-reports").onclick = openReports;
  $("rp-close").onclick = closeReports;
  $("reports").addEventListener("click", e => { if (e.target === $("reports")) closeReports(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && $("reports").style.display === "flex") closeReports(); });
  $("rp-days").onchange = () => { rpDays = $("rp-days").value; loadReport(); };
  $("rp-costs-save").onclick = saveCosts;
}

function openReports() {
  $("reports").style.display = "flex";
  $("rp-days").value = rpDays;
  loadReport();
  $("rp-close").focus();
}

function closeReports() { $("reports").style.display = "none"; }

const rpNum = (n, d = 0) => n == null ? "–" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d });
const rpPct = n => n == null ? "–" : n + "%";

async function loadReport() {
  const days = rpDays;
  $("rp-status").textContent = "Loading…";
  let r, notes;
  try {
    [r, notes] = await Promise.all([api(`/api/reports/summary?days=${days}`), api("/api/notifications?limit=8")]);
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

  $("rp-drivers").innerHTML = r.perDriver.length ? `
    <thead><tr><th>Driver</th><th class="n">Runs</th><th class="n">Deliveries</th><th class="n">On time</th>
      <th class="n">Failed</th><th class="n">Km</th><th class="n">Hours</th><th class="n">Stops/h</th>
      <th class="n">Cost / delivery</th></tr></thead>
    <tbody>${r.perDriver.map(d => `<tr><td>${pdEsc(d.driver)}</td><td class="n">${d.runs}</td>
      <td class="n">${d.deliveries}</td><td class="n">${rpPct(d.onTimeRate)}</td><td class="n">${d.failedAttempts}</td>
      <td class="n">${rpNum(d.km, 1)}</td><td class="n">${rpNum(d.hours, 1)}</td><td class="n">${rpNum(d.stopsPerHour, 1)}</td>
      <td class="n">${d.costPerDelivery == null ? "–" : rpNum(d.costPerDelivery)}</td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="hint">No runs in this period.</td></tr></tbody>`;

  $("rp-perkm").value = r.costs.perKm;
  $("rp-perhour").value = r.costs.perHour;
  $("rp-currency").value = r.costs.currency;
  ["parcels", "runs", "drivers"].forEach(k => {
    $(`rp-export-${k}`).href = `/api/reports/export?kind=${k}&days=${days}`;
  });

  $("rp-notes").innerHTML = notes.messages.length ? notes.messages.map(m => `
    <div class="pd-msg"><div class="pd-msg-top"><b>${m.channel === "sms" ? "SMS" : "Email"}</b> ${pdEsc(m.trackingNumber)} → ${pdEsc(m.to)}
      <span class="nt ${m.status === "sent" ? "ok" : m.status === "failed" ? "bad" : ""}">${m.status === "not_sent" ? "logged, not sent" : m.status}</span>
      <time>${pdWhen(m.createdAt)}</time></div><div>${pdEsc(m.message)}</div></div>`).join("")
    : `<div class="hint">No customer messages yet.</div>`;
  $("rp-notes-hint").textContent = notes.providerConfigured ? ""
    : "No SMS/email provider is connected (set NOTIFY_WEBHOOK_URL), so messages are recorded but not sent.";
}

async function saveCosts() {
  const btn = $("rp-costs-save");
  btn.disabled = true;
  try {
    await api("/api/settings/costs", { method: "PUT", body: JSON.stringify({
      perKm: Number($("rp-perkm").value) || 0, perHour: Number($("rp-perhour").value) || 0,
      currency: $("rp-currency").value.trim() || "LKR" }) });
    await loadReport();
  } catch (err) { $("rp-status").textContent = err.message; }
  finally { btn.disabled = false; }
}
