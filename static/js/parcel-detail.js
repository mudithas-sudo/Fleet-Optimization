// Parcel detail card (admin): history timeline, proof of delivery, failed
// deliveries and what to do next. Loads before admin.js; every function runs
// after admin.js has defined `$`, `api`, `parcels`, `trips`, etc.

const PD_STATUS = {
  pending: "Pending", assigned: "Assigned", in_transit: "Out for delivery",
  picked_up: "Collected, out for delivery", delivered: "Delivered",
  awaiting_redelivery: "Back at the hub", returned: "Returned to sender",
};
const PD_STATUS_TONE = {
  delivered: "ok", in_transit: "info", picked_up: "info", assigned: "info",
  awaiting_redelivery: "warn", returned: "bad", pending: "",
};
const PD_FAIL_REASONS = {
  no_one_home: "Nobody home",
  wrong_address: "Wrong or incomplete address",
  refused: "Receiver refused the parcel",
  damaged: "Parcel damaged",
  access_problem: "Couldn’t get access (gate, road, security)",
  other: "Other reason",
};
const PD_EVENT_TONE = {
  delivered: "ok", pod: "ok", collected: "ok",
  failed: "bad", returned: "bad", returned_to_hub: "warn",
  rescheduled: "info", edited: "info",
};

let pdOpenId = null;

function pdEsc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function pdWhen(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === today.toDateString() ? time
    : d.toLocaleDateString([], { day: "numeric", month: "short" }) + " " + time;
}
// <input type="datetime-local"> value -> epoch seconds (or null)
function pdEpoch(v) { const t = v ? new Date(v).getTime() : NaN; return Number.isNaN(t) ? null : t / 1000; }
function pdDefaultTime(hoursAhead) {
  const d = new Date(Date.now() + hoursAhead * 3600e3);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

function setupParcelDetail() {
  $("btn-close-pd").onclick = closeParcelDetail;
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && pdOpenId && !depotEditing) closeParcelDetail();
  });
}

function closeParcelDetail() {
  pdOpenId = null;
  $("parcel-detail").style.display = "none";
}

async function openParcelDetail(id) {
  pdOpenId = id;
  ["trip-detail", "run-picker", "dispatch-card"].forEach(x => { $(x).style.display = "none"; });
  $("parcel-detail").style.display = "flex";
  $("pd-body").innerHTML = `<div class="working"><span class="spinner"></span><span>Loading…</span></div>`;
  await refreshParcelDetail();
}

async function refreshParcelDetail() {
  if (!pdOpenId) return;
  const id = pdOpenId;
  let p;
  try {
    p = await api(`/api/parcels/${id}`);
  } catch (err) {
    if (id === pdOpenId) $("pd-body").innerHTML = `<div class="warn-row">Couldn’t load the parcel: ${pdEsc(err.message)}</div>`;
    return;
  }
  if (id !== pdOpenId) return;
  renderParcelDetail(p);
}

function renderParcelDetail(p) {
  const tone = PD_STATUS_TONE[p.status] || "";
  const dims = p.dimensionsCm ? `${p.dimensionsCm.l}×${p.dimensionsCm.w}×${p.dimensionsCm.h} cm` : "";
  const facts = [p.priority && p.priority !== "standard" ? p.priority : "", p.size, dims, p.weightKg ? `${(+p.weightKg).toLocaleString()} kg` : "", p.fragile ? "fragile" : "", p.type !== "general" ? p.type : ""]
    .filter(Boolean).join(" · ");
  const deadline = p.status === "delivered" && p.deliveredAt
    ? `Delivered ${pdWhen(p.deliveredAt)}${p.deadlineMissed ? " — after the deadline" : ""}`
    : `Deliver by ${pdWhen(p.deadline)}${p.deadlineMissed ? " — overdue" : ""}`;

  $("pd-title").textContent = p.trackingNumber || p.name;
  $("pd-body").innerHTML = `
    <div class="pd-status ${tone}">${PD_STATUS[p.status] || pdEsc(p.status)}${
      p.failedAttempts ? ` · ${p.failedAttempts} failed attempt${p.failedAttempts === 1 ? "" : "s"}` : ""}</div>
    <div class="pd-facts">
      <b>${pdEsc(p.name)}</b>
      <div>${pdEsc(p.receiverName || "No receiver name")}${p.recipientPhone
        ? ` · <a href="tel:${pdEsc(p.recipientPhone)}">${pdEsc(p.recipientPhone)}</a>` : ""}</div>
      <div>${pdEsc(p.destination.label)}</div>
      ${p.pickup ? `<div class="hint">Collected from ${pdEsc(p.pickup.label)}</div>` : ""}
      ${facts ? `<div class="hint">${pdEsc(facts)}</div>` : ""}
      ${p.notes ? `<div class="hint">Note: ${pdEsc(p.notes)}</div>` : ""}
      ${p.windowStart ? `<div class="hint">Not before ${pdWhen(p.windowStart)}</div>` : ""}
      <div class="hint">${deadline}${p.driverName ? ` · ${pdEsc(p.driverName)}` : ""}</div>
    </div>
    ${failureBlock(p)}
    ${p.pod ? accHtml("Proof of delivery", `<div id="pd-pod" class="pd-pod">${podBlock(p)}</div>`, { open: true }) : ""}
    ${accHtml("History", `<ol class="timeline">${p.timeline.map(e => `
      <li class="${PD_EVENT_TONE[e.type] || ""}"><time>${pdWhen(e.ts)}</time><span>${pdEsc(e.message)}</span></li>`).join("")}
    </ol>`, { open: true, count: p.timeline.length })}
    ${accHtml("Customer updates", `<div id="pd-notify" class="pd-notify"><div class="hint">Loading…</div></div>`)}
    <div id="pd-actions">${actionsBlock(p)}</div>`;
  loadNotifications(p);
  if (p.pod && (p.pod.hasSignature || p.pod.hasPhoto)) loadPodImages(p.id);
  wirePdActions(p);
}

function failureBlock(p) {
  const f = p.lastFailure;
  if (!f || !["awaiting_redelivery", "returned", "pending"].includes(p.status)) return "";
  const left = p.status === "awaiting_redelivery"
    ? `${p.attemptsLeft} attempt${p.attemptsLeft === 1 ? "" : "s"} left before it is returned to the sender.` : "";
  return `<div class="pd-fail"><b>${pdEsc(f.label)}</b>${f.note ? ` — ${pdEsc(f.note)}` : ""}
    <div class="hint">${pdWhen(f.ts)}. ${left}</div></div>`;
}

function podBlock(p) {
  const d = p.pod;
  return `
    <div><b>${pdEsc(d.label)}</b>${d.receivedBy ? ` — ${pdEsc(d.receivedBy)}` : ""}</div>
    ${d.note ? `<div class="hint">${pdEsc(d.note)}</div>` : ""}
    <div class="hint">${pdWhen(d.ts)} · recorded by ${d.by === "dispatcher" ? "dispatch" : "the driver"}${
      d.position ? ` · <a href="https://www.google.com/maps?q=${d.position.lat},${d.position.lng}" target="_blank" rel="noopener">location</a>` : ""}</div>
    <div class="pd-images" id="pd-images"></div>`;
}

async function loadPodImages(id) {
  try {
    const r = await api(`/api/parcels/${id}/pod`);
    if (id !== pdOpenId) return;
    const box = $("pd-images");
    if (!box) return;
    box.innerHTML = [
      r.signature ? `<figure><img alt="Receiver’s signature" src="${r.signature}"><figcaption>Signature</figcaption></figure>` : "",
      r.photo ? `<figure><img alt="Delivery photo" src="${r.photo}"><figcaption>Photo</figcaption></figure>` : "",
    ].join("");
  } catch (_) { /* images are a nice-to-have */ }
}

async function loadNotifications(p) {
  try {
    const r = await api(`/api/parcels/${p.id}/notifications`);
    if (p.id !== pdOpenId) return;
    const box = $("pd-notify");
    if (!box) return;
    const chip = m => m.status === "sent" ? `<span class="nt ok">sent</span>`
      : m.status === "failed" ? `<span class="nt bad">failed</span>`
      : m.status === "sending" ? `<span class="nt">sending</span>`
      : `<span class="nt" title="No SMS/email provider is configured (NOTIFY_WEBHOOK_URL), so this was only logged">logged, not sent</span>`;
    const contact = p.recipientPhone || p.receiverEmail;
    box.innerHTML = `
      <div class="hint">Customer tracking link: <a href="${pdEsc(r.trackingUrl)}" target="_blank" rel="noopener">${pdEsc(r.trackingUrl)}</a>
        <button type="button" class="link-action" id="pd-copy">Copy</button></div>
      ${r.messages.length ? r.messages.map(m => `
        <div class="pd-msg"><div class="pd-msg-top"><b>${m.channel === "sms" ? "SMS" : "Email"}</b> to ${pdEsc(m.to)} ${chip(m)}
          <time>${pdWhen(m.createdAt)}</time></div><div>${pdEsc(m.message)}</div></div>`).join("")
        : `<div class="hint">${contact ? "Nothing sent yet — messages go out when the parcel is out for delivery, nearby, delivered or fails." : "No phone number or email on this parcel, so no updates are sent."}</div>`}`;
    $("pd-copy").onclick = async () => {
      try { await navigator.clipboard.writeText(r.trackingUrl); $("pd-copy").textContent = "Copied"; }
      catch (_) { $("pd-copy").textContent = "Select and copy"; }
    };
  } catch (_) { /* optional section */ }
}

// ---- actions

function actionsBlock(p) {
  const onRoad = ["in_transit", "picked_up"].includes(p.status);
  const back = ["awaiting_redelivery", "pending"].includes(p.status);
  const btns = [];
  const runs = Object.values(trips).filter(t => t.status !== "completed" && t.id !== p.tripId);
  if (runs.length && (onRoad || back || p.status === "assigned")) {
    btns.push(`<button type="button" class="btn btn-ghost" data-act="move">${p.tripId ? "Move to another run" : "Add to a run"}</button>`);
  }
  if (p.tripId && ["assigned", "in_transit", "picked_up"].includes(p.status)) {
    btns.push(`<button type="button" class="btn btn-ghost" data-act="unassign">Take off run</button>`);
  }
  if (onRoad) {
    btns.unshift(`<button type="button" class="btn btn-primary" data-act="deliver">Record delivery</button>`);
    btns.push(`<button type="button" class="btn btn-ghost danger" data-act="fail">Report failed delivery</button>`);
  } else if (back) {
    btns.push(`<button type="button" class="btn btn-primary" data-act="reschedule">Reschedule</button>`);
    btns.push(`<button type="button" class="btn btn-ghost danger" data-act="return">Return to sender</button>`);
  } else if (p.status === "delivered" && !p.pod) {
    btns.push(`<button type="button" class="btn btn-ghost" data-act="deliver">Add proof of delivery</button>`);
  }
  if (!btns.length) return "";
  return `<div class="row">${btns.join("")}</div><div id="pd-form"></div>`;
}

const pdField = (id, label, control) =>
  `<div class="field"><label for="${id}">${label}</label>${control}</div>`;

function wirePdActions(p) {
  document.querySelectorAll("#pd-actions [data-act]").forEach(b => {
    b.onclick = () => showPdForm(b.dataset.act, p);
  });
}

function showPdForm(act, p) {
  const box = $("pd-form");
  const err = `<div class="field-err form-err" id="pd-err" role="alert" hidden></div>`;
  const buttons = label => `${err}<div class="row">
    <button type="button" class="btn btn-ghost" id="pd-cancel">Cancel</button>
    <button type="button" class="btn btn-primary" id="pd-submit">${label}</button></div>`;

  if (act === "deliver") {
    box.innerHTML = `<div class="form-card">
      ${pdField("pd-method", "How was it handed over?", `<select id="pd-method">
        <option value="handed_over">Handed to the receiver</option>
        <option value="left_safe">Left in a safe place</option></select>`)}
      ${pdField("pd-by", "Received by", `<input id="pd-by" placeholder="${pdEsc(p.receiverName || "Name")}" maxlength="80" autocomplete="off">`)}
      ${pdField("pd-note", "Note", `<input id="pd-note" placeholder="e.g. left with the security desk" maxlength="300" autocomplete="off">`)}
      ${buttons("Confirm delivery")}</div>`;
    $("pd-submit").onclick = () => submitPdAction(`/api/parcels/${p.id}/pod`, {
      method: $("pd-method").value, receivedBy: $("pd-by").value.trim() || null,
      note: $("pd-note").value.trim() || null, by: "dispatcher",
    }, "Confirm delivery");
  } else if (act === "fail") {
    box.innerHTML = `<div class="form-card">
      ${pdField("pd-reason", "What went wrong?", `<select id="pd-reason">${
        Object.entries(PD_FAIL_REASONS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>`)}
      ${pdField("pd-note", "Note", `<input id="pd-note" maxlength="300" autocomplete="off">`)}
      ${pdField("pd-retry", "Try again by (optional)", `<input id="pd-retry" type="datetime-local" min="${pdDefaultTime(0)}">`)}
      ${buttons("Report failed delivery")}</div>`;
    $("pd-submit").onclick = () => submitPdAction(`/api/parcels/${p.id}/fail`, {
      reason: $("pd-reason").value, note: $("pd-note").value.trim() || null,
      retryAt: pdEpoch($("pd-retry").value), by: "dispatcher",
    }, "Report failed delivery");
  } else if (act === "reschedule") {
    box.innerHTML = `<div class="form-card">
      ${pdField("pd-when", "Deliver by", `<input id="pd-when" type="datetime-local" min="${pdDefaultTime(0)}" value="${pdDefaultTime(24)}">`)}
      ${pdField("pd-addr", "Corrected address (optional)", `<input id="pd-addr" placeholder="Leave blank to keep ${pdEsc(p.destination.label)}" autocomplete="off">`)}
      ${buttons("Reschedule")}</div>`;
    $("pd-submit").onclick = async () => {
      const when = pdEpoch($("pd-when").value);
      if (!when) return pdFormError("Choose a delivery time.");
      let destination = null;
      const q = $("pd-addr").value.trim();
      if (q) {
        try { destination = await api(`/api/geocode?q=${encodeURIComponent(q)}`); }
        catch (_) { return pdFormError(`Couldn’t find “${q}”. Try a more specific address.`); }
      }
      submitPdAction(`/api/parcels/${p.id}/reschedule`, { deadline: when, destination }, "Reschedule");
    };
  } else if (act === "move") {
    const runs = Object.values(trips).filter(t => t.status !== "completed" && t.id !== p.tripId);
    box.innerHTML = `<div class="form-card">
      ${pdField("pd-run", p.tripId ? "Move to" : "Add to", `<select id="pd-run">${
        runs.map(t => `<option value="${t.id}">${pdEsc(t.label)} · ${pdEsc(t.driverName || "no driver")} (${t.status === "planned" ? "planned" : "under way"})</option>`).join("")}</select>`)}
      <div class="hint">The route is recalculated${runs.some(t => LIVE.includes(t.status)) ? " — for a run under way, from the vehicle’s current position" : ""}.</div>
      ${buttons(p.tripId ? "Move parcel" : "Add to run")}</div>`;
    $("pd-submit").onclick = async () => {
      const toTripId = $("pd-run").value;
      const url = p.tripId ? `/api/parcels/${p.id}/move` : `/api/runs/${toTripId}/parcels`;
      const body = p.tripId ? { toTripId } : { parcelIds: [p.id] };
      await submitPdAction(url, body, p.tripId ? "Move parcel" : "Add to run");
      if (typeof refreshAfterRunEdit === "function") refreshAfterRunEdit();
    };
  } else if (act === "unassign") {
    box.innerHTML = `<div class="form-card"><div class="hint">The parcel goes back to the pending list and the run’s route is recalculated.</div>
      ${buttons("Take off run")}</div>`;
    $("pd-submit").onclick = async () => {
      await submitPdAction(`/api/runs/${p.tripId}/parcels/${p.id}`, undefined, "Take off run", "DELETE");
      if (typeof refreshAfterRunEdit === "function") refreshAfterRunEdit();
    };
  } else if (act === "return") {
    box.innerHTML = `<div class="form-card"><div class="hint">The parcel will be marked as returned to the sender and leave the delivery queue.</div>
      ${buttons("Return to sender")}</div>`;
    $("pd-submit").onclick = () => submitPdAction(`/api/parcels/${p.id}/return`, undefined, "Return to sender");
  }
  $("pd-cancel").onclick = () => { box.innerHTML = ""; };
}

function pdFormError(msg) {
  const el = $("pd-err");
  el.textContent = msg;
  el.hidden = !msg;
}

async function submitPdAction(url, body, label, method = "POST") {
  const btn = $("pd-submit");
  btn.disabled = true;
  pdFormError("");
  try {
    await api(url, { method, body: body === undefined ? undefined : JSON.stringify(body) });
    await refreshParcelDetail();
  } catch (err) {
    pdFormError(err.message || "That didn’t work — try again.");
    btn.disabled = false;
    btn.textContent = label;
  }
}
