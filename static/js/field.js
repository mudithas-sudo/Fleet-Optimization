// Real-world operations (from the Sri Lanka courier research): the Cash page (cash on delivery held by
// riders, counted in at the hub, owed to merchants), the confirmation call list and delivery-risk
// score, area holds (flood / hartal), absent riders, the capacity forecast and fuel allowance, and
// the first-attempt figures on Reports. Reads /api/cod, /api/risk, /api/disruptions, /api/capacity.

let cashTimer = null;

const fxEsc = s => typeof pdEsc === "function" ? pdEsc(s) : String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fxMoney = (n, cur = "LKR") => `${cur} ${Math.round(Number(n) || 0).toLocaleString()}`;
const fxAgo = ts => {
  const h = (Date.now() / 1000 - ts) / 3600;
  return h < 1 ? Math.max(1, Math.round(h * 60)) + " min" : h < 48 ? Math.round(h) + " h" : Math.round(h / 24) + " days";
};
const fxDay = ts => new Date(ts * 1000).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });

function setupField() {
  const el = $("att-field");
  if (el) el.addEventListener("click", attentionClick);
}

// ------------------------------------------------------------------ cash page

function startCash() {
  loadCash();
  clearInterval(cashTimer);
  cashTimer = setInterval(() => { if (activeTab === "cash") loadCash(true); else clearInterval(cashTimer); }, 15000);
}

async function loadCash(quiet = false) {
  let led;
  try { led = await api("/api/cod/ledger"); }
  catch (err) { if (!quiet) $("cash-riders").innerHTML = `<div class="empty-state"><b>Couldn’t load the cash ledger</b>${fxEsc(err.message)}</div>`; return; }
  renderCash(led);
}

function renderCash(led) {
  const cur = led.settings.currency, t = led.totals, kpi = (v, l, n = "") => `<div class="rp-kpi"><b>${v}</b><span>${l}</span>${n ? `<small>${n}</small>` : ""}</div>`;
  $("cash-kpis").innerHTML = [
    kpi(fxMoney(t.onTheRoad, cur), "Out for collection", "on parcels still on the road"),
    kpi(fxMoney(t.withRiders, cur), "With riders", led.flags.overLimit ? `${led.flags.overLimit} over the limit` : "all within the limit"),
    kpi(fxMoney(t.atHub, cur), "Counted at the hub", "waiting to be paid to merchants"),
    kpi(fxMoney(t.paidOut, cur), "Paid to merchants"),
    kpi(fxMoney(t.shortfall, cur), "Short at the door", "collected less than due"),
  ].join("");

  const canCount = can("cod.write"), canPay = can("cod.remit");
  $("cash-riders").innerHTML = led.riders.length ? led.riders.map(r => `
    <div class="fx-rider${r.overLimit ? " over" : ""}" data-driver="${fxEsc(r.driverId)}">
      <div class="fx-rider-top"><b>${fxEsc(r.driverName)}</b>
        <span class="fx-cash">${fxMoney(r.cash, cur)}</span>
        ${r.overLimit ? `<span class="fx-flag bad">Over the ${fxMoney(led.settings.cashLimit, cur)} limit</span>` : ""}
        ${r.overdue ? `<span class="fx-flag warn">Holding cash for ${fxAgo(r.oldestAt)}</span>` : ""}</div>
      <div class="fx-meter" role="img" aria-label="${r.usedPct}% of the cash limit"><i style="width:${Math.min(100, r.usedPct)}%"></i></div>
      <div class="hint">${r.parcels.length} parcel${r.parcels.length === 1 ? "" : "s"}: ${r.parcels.map(p => `${fxEsc(p.trackingNumber)} (${fxMoney(p.amount, cur)}, ${fxEsc(p.merchant)})`).join(" · ")}</div>
      ${canCount ? `<div class="fx-count">
        <label>Cash counted<input type="number" min="0" step="1" inputmode="decimal" value="${Math.round(r.cash)}" data-counted></label>
        <label>Slip no.<input maxlength="60" placeholder="e.g. DEP-0412" data-ref></label>
        <button type="button" class="btn btn-primary" data-count>Count cash in</button></div>` : ""}
    </div>`).join("") : `<div class="empty-state"><b>No rider is holding cash</b>Cash taken at the door shows up here until it is counted in at the hub.</div>`;

  $("cash-merchants").innerHTML = led.merchants.length ? `<thead><tr><th>Merchant</th><th>With riders</th><th>At the hub</th><th>Waiting</th><th>Paid out</th>${canPay ? "<th></th>" : ""}</tr></thead><tbody>${
    led.merchants.map(m => `<tr data-merchant="${fxEsc(m.merchant)}"><td>${fxEsc(m.merchant)}</td><td>${fxMoney(m.withRiders, cur)}</td>
      <td>${fxMoney(m.awaitingPayout, cur)}</td><td>${m.waitingDays == null ? "–" : m.waitingDays + " days"}</td><td>${fxMoney(m.paidOut, cur)}</td>
      ${canPay ? `<td>${m.awaitingPayout > 0 ? `<span class="fx-pay"><input maxlength="60" placeholder="Bank ref" data-ref aria-label="Payment reference"><button type="button" class="btn btn-ghost" data-pay>Mark paid</button></span>` : ""}</td>` : ""}</tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="hint">No cash on delivery yet.</td></tr></tbody>`;

  $("cash-deposits").innerHTML = led.deposits.length ? `<thead><tr><th>When</th><th>Rider</th><th>Expected</th><th>Counted</th><th>Difference</th><th>Slip</th><th>By</th></tr></thead><tbody>${
    led.deposits.map(d => `<tr><td>${new Date(d.createdAt * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</td>
      <td>${fxEsc(d.driverName)}</td><td>${fxMoney(d.expected, cur)}</td><td>${fxMoney(d.counted, cur)}</td>
      <td class="${d.variance ? "fx-neg" : ""}">${d.variance ? (d.variance > 0 ? "+" : "") + fxMoney(d.variance, cur) : "none"}</td><td>${fxEsc(d.reference || "–")}</td><td>${fxEsc(d.by)}</td></tr>`).join("")}</tbody>`
    : `<tbody><tr><td class="hint">No deposits yet.</td></tr></tbody>`;

  $("cash-limit").value = led.settings.cashLimit;
  $("cash-hours").value = led.settings.depositHours;
  document.querySelectorAll("#tab-cash .fx-admin").forEach(e => { e.hidden = !can("cod.remit"); });
}

function setupCash() {
  const page = $("tab-cash");
  if (!page) return;
  page.addEventListener("click", async e => {
    const count = e.target.closest("[data-count]"), pay = e.target.closest("[data-pay]");
    try {
      if (count) {
        const card = count.closest(".fx-rider");
        count.disabled = true;
        const dep = await api("/api/cod/deposit", { method: "POST", body: JSON.stringify({
          driverId: card.dataset.driver, counted: Number(card.querySelector("[data-counted]").value), reference: card.querySelector("[data-ref]").value.trim() || null }) });
        toast(dep.variance ? `Counted in. ${fxMoney(Math.abs(dep.variance))} ${dep.variance < 0 ? "short" : "over"} against what the rider should hold.` : "Counted in. The cash matches.");
        loadCash();
      } else if (pay) {
        const row = pay.closest("tr");
        pay.disabled = true;
        const r = await api("/api/cod/remit", { method: "POST", body: JSON.stringify({ merchant: row.dataset.merchant, reference: row.querySelector("[data-ref]").value.trim() || null }) });
        toast(`Paid ${fxMoney(r.amount)} to ${r.merchant}.`);
        loadCash();
      }
    } catch (err) { toast(err.message); loadCash(); }
  });
  $("cash-settings-save").onclick = async () => {
    try {
      await api("/api/cod/settings", { method: "PUT", body: JSON.stringify({ cashLimit: Number($("cash-limit").value), depositHours: Number($("cash-hours").value) }) });
      toast("Cash limits saved."); loadCash();
    } catch (err) { toast(err.message); }
  };
}

function toast(msg) {
  if (typeof showToast === "function") return showToast(msg);
  const el = $("fx-toast") || Object.assign(document.body.appendChild(document.createElement("div")), { id: "fx-toast", className: "fx-toast", role: "status" });
  el.textContent = msg; el.classList.add("show");
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("show"), 4500);
}

// ------------------------------------------------------------------ attention extras

async function startAttentionField() {
  const box = $("att-field");
  if (!box) return;
  const [q, zones, cap] = await Promise.all([
    api("/api/risk/queue").catch(() => null), api("/api/disruptions").catch(() => []), api("/api/capacity?days=5").catch(() => null)]);
  const live = zones.filter(z => z.isActive);
  const editable = can("runs.plan");
  box.innerHTML = [
    q ? accHtml("Confirm before dispatch", confirmList(q), { open: q.high > 0, count: q.count, warn: q.high > 0 }) : "",
    accHtml("Area holds: floods, hartals, closures", zonesBlock(live, editable), { open: live.length > 0, count: live.length, warn: live.length > 0 }),
    editable ? accHtml("A rider isn’t coming in", absentBlock(), {}) : "",
    cap ? accHtml("Capacity for the next days", capacityBlock(cap), { open: cap.days.some(d => d.status !== "ok"), warn: cap.days.some(d => d.status === "short") }) : "",
  ].join("");
}

function confirmList(q) {
  if (!q.rows.length) return `<div class="hint">Every open parcel has been confirmed.</div>`;
  const levels = { high: "High risk", medium: "Medium", low: "Low" };
  return `<div class="hint" style="margin-bottom:8px">Parcels not yet confirmed with the receiver, riskiest first. Confirming before the rider leaves is the cheapest way to avoid a failed first attempt.</div>` +
    q.rows.slice(0, 12).map(r => `<div class="fx-row" data-id="${r.id}">
      <span class="fx-score ${r.level}" title="Risk score ${r.score} of 100">${levels[r.level]}</span>
      <div class="fx-main"><b><a href="#" data-open>${fxEsc(r.trackingNumber)}</a> · ${fxEsc(r.receiverName || r.name)}</b>
        <span class="hint">${fxEsc(r.label || "")}${r.cod ? " · cash " + Math.round(r.cod).toLocaleString() : ""}</span>
        <span class="hint">${r.top.map(fxEsc).join(" · ") || "No specific concern"}</span></div>
      <div class="fx-btns">${r.confirmation === "requested" ? `<span class="hint">Asked</span>` : r.phone ? `<button type="button" class="btn btn-ghost" data-ask>Send message</button>` : ""}
        <button type="button" class="btn btn-ghost" data-confirmed>Confirmed</button></div></div>`).join("") +
    (q.count > 12 ? `<div class="hint">and ${q.count - 12} more</div>` : "");
}

function zonesBlock(live, editable) {
  const kinds = { flood: "Flood", hartal: "Hartal or protest", closure: "Road closure", weather: "Severe weather", other: "Other" };
  return `${live.length ? live.map(z => `<div class="fx-row" data-zone="${z.id}">
      <span class="fx-score high">${fxEsc(kinds[z.kind] || z.kind)}</span>
      <div class="fx-main"><b>${fxEsc(z.label)}</b><span class="hint">${z.radiusKm} km around ${z.lat.toFixed(3)}, ${z.lng.toFixed(3)} · ${z.parcelsInside} parcel${z.parcelsInside === 1 ? "" : "s"} held${z.until ? " · until " + new Date(z.until * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}</span></div>
      ${editable ? `<div class="fx-btns"><button type="button" class="btn btn-ghost" data-clear>Reopen the area</button></div>` : ""}</div>`).join("")
    : `<div class="hint">No area is on hold. Parcels in a held area stay out of new runs and the customers are told why.</div>`}
    ${editable ? `<div class="form-card fx-form">
      <div class="form-grid">
        <div class="field"><label for="dz-kind">What happened?</label><select id="dz-kind">${Object.entries(kinds).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div>
        <div class="field"><label for="dz-area">Where? (a place or town)</label><input id="dz-area" placeholder="e.g. Kelaniya" autocomplete="off"></div>
        <div class="field"><label for="dz-radius">Radius (km)</label><input id="dz-radius" type="number" min="0.5" max="50" step="0.5" value="3"></div>
        <div class="field"><label for="dz-hours">Hold for (hours, blank = until reopened)</label><input id="dz-hours" type="number" min="1" step="1" placeholder="e.g. 12"></div>
      </div>
      <div class="field-err form-err" id="dz-err" role="alert" hidden></div>
      <div class="row"><button type="button" class="btn btn-primary" id="dz-save" data-hold>Hold this area</button></div>
    </div>` : ""}`;
}

function absentBlock() {
  const list = driversReg.filter(d => !d.onLeave);
  return `<div class="hint" style="margin-bottom:8px">Their planned runs go back to the queue so the parcels can be re-planned straight away.</div>
    <div class="form-card fx-form"><div class="form-grid">
      <div class="field"><label for="ab-driver">Rider</label><select id="ab-driver">${list.map(d => `<option value="${d.id}">${fxEsc(d.name)}</option>`).join("")}</select></div>
      <div class="field"><label for="ab-reason">Reason (optional)</label><input id="ab-reason" maxlength="120" placeholder="e.g. unwell, no fuel"></div></div>
      <div class="row"><button type="button" class="btn btn-ghost danger" data-absent>Mark absent today</button></div></div>`;
}

function capacityBlock(cap) {
  const label = { ok: "Enough riders", tight: "Tight", short: "Short of riders" };
  const f = cap.fuel;
  return `<div class="hint" style="margin-bottom:8px">${fxEsc(cap.note)}</div>` + cap.days.map(d => `<div class="fx-row">
      <span class="fx-score ${d.status === "short" ? "high" : d.status === "tight" ? "medium" : "low"}">${label[d.status]}</span>
      <div class="fx-main"><b>${fxDay(d.date)}</b><span class="hint">${d.demand} parcels${d.blockedByDisruption ? ` (${d.blockedByDisruption} held in blocked areas)` : ""} for ${d.riders} riders who can serve about ${d.capacity}${d.utilisationPct != null ? " · " + d.utilisationPct + "% full" : ""}</span>
      ${d.extraRiders ? `<span class="hint"><b>Needs about ${d.extraRiders} more rider${d.extraRiders === 1 ? "" : "s"}</b>: book gig or partner riders for this day.</span>` : ""}</div></div>`).join("") +
    `<div class="fx-fuel"><b>Fuel allowance</b>
      <div class="hint">When fuel is rationed, set the litres each vehicle may use per week. 0 means no limit.</div>
      <div class="row"><input id="fq-litres" type="number" min="0" step="1" value="${f.weeklyLitres}" aria-label="Weekly litres per vehicle">
      <button type="button" class="btn btn-ghost" data-fuel>Save allowance</button></div>
      ${f.vehicles.length ? `<div class="rp-tablewrap"><table><thead><tr><th>Vehicle</th><th>Used this week</th><th>Left</th></tr></thead><tbody>${f.vehicles.map(v =>
        `<tr><td>${fxEsc(v.vehicle)}</td><td>${v.litres} L</td><td>${v.remaining == null ? "–" : (v.remaining < 0 ? "over by " + Math.abs(v.remaining) : v.remaining) + " L"}</td></tr>`).join("")}</tbody></table></div>` : ""}
    </div>`;
}

async function attentionClick(e) {
  const t = e.target;
  const row = t.closest("[data-id]");
  try {
    if (t.closest("[data-open]")) { e.preventDefault(); if (typeof revealParcel === "function") revealParcel(row.dataset.id); return; }
    if (t.closest("[data-ask]")) {
      await api(`/api/parcels/${row.dataset.id}/confirmation`, { method: "POST", body: JSON.stringify({ action: "request" }) });
      toast("Confirmation message recorded for the receiver."); startAttentionField(); return;
    }
    if (t.closest("[data-confirmed]")) {
      await api(`/api/parcels/${row.dataset.id}/confirmation`, { method: "POST", body: JSON.stringify({ action: "confirm", note: "Confirmed by phone" }) });
      startAttentionField(); return;
    }
    if (t.closest("[data-clear]")) {
      await api(`/api/disruptions/${t.closest("[data-zone]").dataset.zone}/clear`, { method: "POST" });
      toast("Area reopened. Held parcels can be planned again."); startAttentionField(); return;
    }
    if (t.closest("[data-hold]")) return holdArea();
    if (t.closest("[data-absent]")) {
      const r = await api(`/api/drivers/${$("ab-driver").value}/absent`, { method: "POST", body: JSON.stringify({ reason: $("ab-reason").value.trim() || null }) });
      toast(`${r.driver.name} is marked absent. ${r.releasedParcels} parcel${r.releasedParcels === 1 ? "" : "s"} back in the queue${r.liveRuns.length ? "; a live run needs the breakdown tool" : ""}.`);
      if (typeof loadRegistry === "function") loadRegistry();
      startAttentionField(); return;
    }
    if (t.closest("[data-fuel]")) {
      await api("/api/fuel-quota", { method: "PUT", body: JSON.stringify({ weeklyLitres: Number($("fq-litres").value) }) });
      toast("Fuel allowance saved."); startAttentionField(); return;
    }
  } catch (err) { toast(err.message); }
}

async function holdArea() {
  const err = $("dz-err");
  err.hidden = true;
  const q = $("dz-area").value.trim();
  if (!q) { err.textContent = "Say where, for example a town or junction."; err.hidden = false; return; }
  let place;
  try { place = await api(`/api/geocode?q=${encodeURIComponent(q)}`); }
  catch (_) { err.textContent = `Couldn’t find “${q}”. Try a more specific place.`; err.hidden = false; return; }
  const hours = Number($("dz-hours").value);
  try {
    const r = await api("/api/disruptions", { method: "POST", body: JSON.stringify({
      kind: $("dz-kind").value, label: `${$("dz-kind").selectedOptions[0].text} at ${q}`, lat: place.lat, lng: place.lng,
      radiusKm: Number($("dz-radius").value) || 3, until: hours > 0 ? Date.now() / 1000 + hours * 3600 : null }) });
    toast(`${r.held} parcel${r.held === 1 ? "" : "s"} held, ${r.releasedFromRuns} taken off planned runs, ${r.customersTold} customer${r.customersTold === 1 ? "" : "s"} told${r.liveRunsAffected.length ? `. ${r.liveRunsAffected.length} live run(s) are heading there: call the rider` : ""}.`);
    startAttentionField();
  } catch (e) { err.textContent = e.message; err.hidden = false; }
}

// ------------------------------------------------------------------ parcel detail: cash and risk

function codFacts(p) {
  if (!p.cod) return "";
  const c = p.cod, cur = "LKR";
  const state = { due: "to collect at the door", collected: "collected, with the rider", deposited: "counted at the hub", remitted: "paid to the merchant" }[c.state];
  return `<div class="hint"><b>Cash on delivery ${fxMoney(c.amount, cur)}</b> · ${state}${c.short ? ` · ${fxMoney(c.short, cur)} short` : ""}${p.merchant ? ` · ${fxEsc(p.merchant)}` : ""}</div>`;
}

function riskBlock(p) {
  if (p.requestType === "pickup" || !["pending", "assigned", "awaiting_redelivery", "in_transit"].includes(p.status)) return "";
  return accHtml("Delivery risk", `<div id="pd-risk" class="hint">Checking…</div>`, { open: false });
}

async function loadRisk(p) {
  const box = document.getElementById("pd-risk");
  if (!box) return;
  let r;
  try { r = await api(`/api/parcels/${p.id}/risk`); } catch (err) { box.textContent = err.message; return; }
  const conf = r.confirmation && r.confirmation.state;
  const confText = { requested: "Asked, no reply yet", confirmed: "Confirmed", reschedule: "Receiver asked for another day", cancelled: "Cancelled by the receiver" }[conf] || "Not confirmed";
  const canWrite = can("parcels.write");
  if (r.level === "high") {          // show it straight away when it matters
    const acc = box.closest(".acc");
    if (acc) { acc.classList.add("open"); const h = acc.querySelector(".acc-head"); if (h) h.setAttribute("aria-expanded", "true"); }
  }
  box.innerHTML = `
    <div class="fx-riskhead"><span class="fx-score ${r.level}">${{ high: "High risk", medium: "Medium risk", low: "Low risk" }[r.level]}</span><b>${r.score}</b> of 100 · ${confText}</div>
    <ul class="fx-reasons">${r.reasons.map(x => `<li><span class="${x.points > 0 ? "fx-neg" : "fx-pos"}">${x.points > 0 ? "+" : ""}${x.points}</span> ${fxEsc(x.text)}</li>`).join("") || "<li>Nothing stands out.</li>"}</ul>
    <div class="hint">Address confidence ${r.address.score}%: ${r.address.reasons.map(fxEsc).join(" · ")}</div>
    ${canWrite && p.status !== "in_transit" ? `<div class="row" style="margin-top:8px">
      ${r.address.suggestedPin ? `<button type="button" class="btn btn-ghost" data-rk="pin">Use the pin from past deliveries</button>` : ""}
      ${conf !== "confirmed" ? `<button type="button" class="btn btn-ghost" data-rk="request">Send confirmation message</button>
        <button type="button" class="btn btn-ghost" data-rk="confirm">Receiver confirmed</button>` : ""}</div>` : ""}`;
  box.querySelectorAll("[data-rk]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      if (b.dataset.rk === "pin") await api(`/api/parcels/${p.id}/use-known-pin`, { method: "POST" });
      else await api(`/api/parcels/${p.id}/confirmation`, { method: "POST", body: JSON.stringify({ action: b.dataset.rk, note: b.dataset.rk === "confirm" ? "Confirmed by phone" : null }) });
      if (typeof openParcelDetail === "function") openParcelDetail(p.id);
    } catch (err) { toast(err.message); b.disabled = false; }
  });
}

// ------------------------------------------------------------------ reports: first attempt

async function renderFirstAttempt(days) {
  const box = $("rp-fa");
  if (!box) return;
  let k;
  try { k = await api(`/api/risk/kpis?days=${days === "all" ? 0 : days === "today" ? 1 : days}`); } catch (_) { box.innerHTML = ""; return; }
  const pct = v => v == null ? "–" : v + "%";
  const kpi = (v, l, n = "") => `<div class="rp-kpi"><b>${v}</b><span>${l}</span>${n ? `<small>${n}</small>` : ""}</div>`;
  box.innerHTML = `<h3>First-attempt delivery</h3><div class="rp-kpis">${[
    kpi(pct(k.firstAttemptPct), "Delivered first time", `${k.parcels} parcels finished`),
    kpi(pct(k.confirmed.firstAttemptPct), "When confirmed first", `${k.confirmed.parcels} parcels`),
    kpi(pct(k.unconfirmed.firstAttemptPct), "When not confirmed", `${k.unconfirmed.parcels} parcels`),
    kpi(pct(k.cod.firstAttemptPct), "Cash on delivery", `${k.cod.parcels} parcels`),
    kpi(pct(k.returnedPct), "Sent back to sender"),
  ].join("")}</div>
  ${k.failureReasons.length ? `<div class="hint" style="margin-top:6px">Why first attempts failed: ${k.failureReasons.map(r => `${fxEsc(r.reason)} (${r.parcels})`).join(" · ")}</div>` : ""}`;
}

// ------------------------------------------------------------------ scorecard, dashboard flags, parcel chips

let riskById = {};                     // parcel id -> "high" | "medium" | "low" (for the chips in the Parcels list)
let fxFlags = { at: 0, rows: [] };

// the research's KPI scorecard on the Impact page
async function renderScorecard(days) {
  const box = $("im-score");
  if (!box) return;
  let k;
  try { k = await api(`/api/scorecard?days=${days}`); } catch (_) { box.innerHTML = ""; return; }
  const pct = v => v == null ? "–" : v + "%";
  const t = k.targets, c = k.cod;
  const card = (v, label, target, note = "") => `<div class="rp-kpi"><b>${v}</b><span>${label}</span><small>${note ? note + " · " : ""}target ${target}</small></div>`;
  box.innerHTML = `<h3>Scorecard against the research targets</h3><div class="rp-kpis">${[
    card(pct(k.firstAttemptPct), "Delivered first time", t.firstAttemptPct, k.confirmedFirstPct != null ? `${k.confirmedFirstPct}% when confirmed, ${pct(k.unconfirmedFirstPct)} when not` : ""),
    card(pct(k.returnedPct), "Sent back to sender", t.returnedPct),
    card(pct(k.onTimePct), "On time", t.onTimePct),
    card(c.avgDaysToPay == null ? "–" : c.avgDaysToPay + " days", "Cash to merchant", t.avgDaysToPay, `${fxMoney(c.waitingAtHub)} waiting at the hub`),
    card(c.variancePct == null ? "–" : c.variancePct + "%", "Cash variance", t.variancePct, `${fxMoney(c.collected)} collected`),
    card(k.stopsPerHour == null ? "–" : k.stopsPerHour, "Stops per rider-hour", t.stopsPerHour),
    card(k.kmPerParcel == null ? "–" : k.kmPerParcel + " km", "Distance per parcel", t.kmPerParcel),
  ].join("")}</div>
  ${k.failureReasons.length ? `<div class="hint" style="margin-top:6px">Why first attempts failed: ${k.failureReasons.map(r => `${fxEsc(r.reason)} (${r.parcels})`).join(" · ")}</div>` : ""}`;
}

async function refreshFieldFlags(force = false) {
  if (!force && Date.now() - fxFlags.at < 20000) return;
  fxFlags.at = Date.now();
  const [led, q, zones] = await Promise.all([
    can("cod.read") ? api("/api/cod/ledger").catch(() => null) : null,
    can("parcels.read") ? api("/api/risk/queue").catch(() => null) : null,
    can("runs.read") ? api("/api/disruptions").catch(() => []) : []]);
  const rows = [];
  if (led && led.flags.overLimit) rows.push({ tag: "Cash", title: `${led.flags.overLimit} rider${led.flags.overLimit === 1 ? " is" : "s are"} over the cash limit`,
    detail: "Ask them to hand cash in at the hub", go: "cash" });
  if (led && led.flags.overdue) rows.push({ tag: "Cash", title: `${led.flags.overdue} rider${led.flags.overdue === 1 ? " has" : "s have"} held cash too long`,
    detail: "Past the hand-in time", go: "cash" });
  if (q && q.high) rows.push({ tag: "Confirm", title: `${q.high} high-risk parcel${q.high === 1 ? "" : "s"} not confirmed`,
    detail: "Call or message the receiver before dispatch", go: "attention" });
  (zones || []).filter(z => z.isActive).forEach(z => rows.push({ tag: "Area hold", title: z.label, detail: `${z.parcelsInside} parcel${z.parcelsInside === 1 ? "" : "s"} held`, go: "attention" }));
  fxFlags.rows = rows;
  if (q) {
    const next = Object.fromEntries(q.rows.map(r => [r.id, r.level]));
    const changed = JSON.stringify(next) !== JSON.stringify(riskById);
    riskById = next;
    if (changed && typeof renderParcels === "function") renderParcels();
  }
  if (typeof renderDock === "function") renderDock();
}

// called by renderDock: puts the cash / confirmation / area-hold flags at the top of "Needs attention"
function addFieldAttention(box) {
  refreshFieldFlags();
  if (!fxFlags.rows.length) return;
  const body = box.querySelector(".acc-body");
  if (!body) return;
  const html = fxFlags.rows.map((i, n) => `<button type="button" class="dock-item level-1" data-fgo="${i.go}">
      <span class="di-tag"><i aria-hidden="true"></i>${fxEsc(i.tag)}</span>
      <span class="di-main"><span class="di-title">${fxEsc(i.title)}</span><span class="di-sub">${fxEsc(i.detail)}</span></span>
      <svg viewBox="0 0 24 24" class="i di-go" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg></button>`).join("");
  body.insertAdjacentHTML("afterbegin", html);
  body.querySelectorAll("[data-fgo]").forEach(b => { b.onclick = () => switchTab(b.dataset.fgo); });
}
