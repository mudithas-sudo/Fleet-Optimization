// Real-world operations (from the Sri Lanka courier research): the confirmation call list and delivery-risk score,
// area holds (flood / hartal), absent riders, the capacity forecast, international imports held at Customs, and the
// first-attempt figures. Reads /api/risk, /api/disruptions, /api/capacity, /api/customs.

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
  const [q, zones, cap, cu] = await Promise.all([
    api("/api/risk/queue").catch(() => null), api("/api/disruptions").catch(() => []), api("/api/capacity?days=5").catch(() => null),
    api("/api/customs").catch(() => null)]);
  const live = zones.filter(z => z.isActive);
  const editable = can("runs.plan");
  box.innerHTML = [
    cu ? accHtml("Held at Customs", customsBlock(cu), { open: cu.held > 0, count: cu.count, warn: cu.held > 0 }) : "",
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
        <span class="hint">${fxEsc(r.label || "")}</span>
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
  return `<div class="hint" style="margin-bottom:8px">${fxEsc(cap.note)}</div>` + cap.days.map(d => `<div class="fx-row">
      <span class="fx-score ${d.status === "short" ? "high" : d.status === "tight" ? "medium" : "low"}">${label[d.status]}</span>
      <div class="fx-main"><b>${fxDay(d.date)}</b><span class="hint">${d.demand} parcels${d.blockedByDisruption ? ` (${d.blockedByDisruption} held in blocked areas)` : ""} for ${d.riders} riders who can serve about ${d.capacity}${d.utilisationPct != null ? " · " + d.utilisationPct + "% full" : ""}</span>
      ${d.extraRiders ? `<span class="hint"><b>Needs about ${d.extraRiders} more rider${d.extraRiders === 1 ? "" : "s"}</b>: ask the contractor for extra capacity on this day.</span>` : ""}</div></div>`).join("");
}

async function attentionClick(e) {
  const t = e.target;
  const row = t.closest("[data-id]");
  try {
    if (t.closest("[data-customs]")) {
      await api(`/api/parcels/${row.dataset.id}/customs`, { method: "POST", body: JSON.stringify({ state: t.closest("[data-customs]").dataset.customs }) });
      toast(t.closest("[data-customs]").dataset.customs === "cleared" ? "Cleared. The parcel can now go on a run." : "Marked as held. The receiver has been told to attend Customs.");
      startAttentionField(); return;
    }
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

// ------------------------------------------------------------------ parcel detail: risk

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
  const t = k.targets;
  const card = (v, label, target, note = "") => `<div class="rp-kpi"><b>${v}</b><span>${label}</span><small>${note ? note + " · " : ""}target ${target}</small></div>`;
  box.innerHTML = `<h3>Scorecard against the research targets</h3><div class="rp-kpis">${[
    card(pct(k.firstAttemptPct), "Delivered first time", t.firstAttemptPct, k.confirmedFirstPct != null ? `${k.confirmedFirstPct}% when confirmed, ${pct(k.unconfirmedFirstPct)} when not` : ""),
    card(pct(k.returnedPct), "Sent back to sender", t.returnedPct),
    card(pct(k.onTimePct), "On time", t.onTimePct),
    card(k.stopsPerHour == null ? "–" : k.stopsPerHour, "Stops per rider-hour", t.stopsPerHour),
    card(k.kmPerParcel == null ? "–" : k.kmPerParcel + " km", "Distance per parcel", t.kmPerParcel),
  ].join("")}</div>
  ${k.failureReasons.length ? `<div class="hint" style="margin-top:6px">Why first attempts failed: ${k.failureReasons.map(r => `${fxEsc(r.reason)} (${r.parcels})`).join(" · ")}</div>` : ""}`;
}

async function refreshFieldFlags(force = false) {
  if (!force && Date.now() - fxFlags.at < 20000) return;
  fxFlags.at = Date.now();
  const [q, zones] = await Promise.all([
    can("parcels.read") ? api("/api/risk/queue").catch(() => null) : null,
    can("runs.read") ? api("/api/disruptions").catch(() => []) : []]);
  const rows = [];
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

// called by renderDock: puts the confirmation / area-hold flags at the top of "Needs attention"
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

// ------------------------------------------------------------------ international imports and Customs

function customsBlock(cu) {
  if (!cu.rows.length) return `<div class="hint">No international parcel is waiting for Customs.</div>`;
  return `<div class="hint" style="margin-bottom:8px">International imports cannot go on a run until Sri Lanka Customs releases them. If Customs detains one, the receiver must attend an examination and pay any duty and tax at the Customs point, never to the rider.</div>` +
    cu.rows.map(r => `<div class="fx-row" data-id="${r.id}">
      <span class="fx-score ${r.state === "held_exam" ? "high" : "medium"}">${r.state === "held_exam" ? "Held" : "In customs"}</span>
      <div class="fx-main"><b><a href="#" data-open>${fxEsc(r.trackingNumber)}</a> · ${fxEsc(r.receiverName || r.name)}</b>
        <span class="hint">${fxEsc(r.name)}${r.origin ? " from " + fxEsc(r.origin) : ""}${r.priority && r.priority !== "standard" ? " · " + fxEsc(r.priority) : ""}${r.deadline ? " · due " + new Date(r.deadline * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}</span></div>
      <div class="fx-btns">${r.state === "clearing" ? `<button type="button" class="btn btn-ghost" data-customs="held_exam">Customs is holding it</button>` : ""}
        <button type="button" class="btn btn-primary" data-customs="cleared">Cleared</button></div></div>`).join("");
}

function customsFacts(p) {
  if (!p.shipment) return "";
  const s = p.shipment.customs.state;
  const text = { clearing: "awaiting Customs clearance", held_exam: "held by Customs: the receiver must attend an examination", cleared: "cleared by Customs" }[s];
  return `<div class="hint"><b>International import${p.shipment.origin ? " from " + fxEsc(p.shipment.origin) : ""}</b> · ${text}</div>`;
}
