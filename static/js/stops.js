// Driver "My stops" sheet: see the run's parcels in stop order, confirm a
// delivery with proof (who received it, a photo, the receipt reference from the existing
// e-signature process) or report that it
// couldn't be delivered. Loads before driver.js; uses its `trip` / `lastPos`.

const STOP_FAIL_REASONS = [
  ["no_one_home", "Nobody home"],
  ["wrong_address", "Wrong or incomplete address"],
  ["refused", "Receiver refused the parcel"],
  ["damaged", "Parcel damaged"],
  ["access_problem", "Couldn’t get access (gate, road, security)"],
  ["other", "Other reason"],
];

const STOP_PICKUP_FAIL_REASONS = [
  ["not_ready", "Shipment not ready for pickup"],
  ["customer_unavailable", "Customer unavailable"],
  ["wrong_address", "Wrong or incomplete address"],
  ["access_problem", "Couldn’t get access (gate, road, security)"],
  ["cancelled", "Customer cancelled the pickup"],
  ["other", "Other reason"],
];

let stopsOpen = false;

function stopEsc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function closeStops() {
  stopsOpen = false;
  $("stops-sheet").classList.add("hidden");
}

async function openStops() {
  if (typeof trip === "undefined" || !trip) return;
  stopsOpen = true;
  $("stops-sheet").classList.remove("hidden");
  $("stops-close").onclick = closeStops;
  await loadStops();
}

async function loadStops() {
  const body = $("stops-body");
  let rows;
  try {
    rows = await api(`/api/trips/${trip.id}/parcels`);
  } catch (err) {
    body.innerHTML = `<div class="stop-note">Couldn’t load your stops: ${stopEsc(err.message)}</div>`;
    return;
  }
  const todo = rows.filter(p => ["in_transit", "picked_up", "assigned"].includes(p.status)).length;
  $("stops-count").textContent = !rows.length ? "No parcels on this run"
    : todo ? `${todo} to go` : "All stops handled";
  body.innerHTML = "";
  const nextId = (rows.find(p => ["todo", "pickup"].includes(stopState(p))) || {}).id;      // the one thing to do next
  rows.forEach((p, i) => body.append(stopCard(p, i + 1, p.id === nextId)));
}

function stopState(p) {
  if (p.requestType === "pickup") {       // collected from the customer, then taken to the hub
    return p.status === "delivered" ? "done"
      : ["failed", "returned", "pending"].includes(p.status) ? "failed"
      : p.status === "picked_up" ? "tohub" : "pickup";
  }
  return p.status === "delivered" ? "done"
    : p.status === "awaiting_redelivery" || p.status === "returned" || p.status === "failed" ? "failed"
    : p.collectFirst ? "collect" : "todo";
}

function stopCard(p, n, isNext = false) {
  const onRoad = ["in_transit", "picked_up", "assigned"].includes(p.status);
  const state = stopState(p);
  const request = p.requestType === "pickup";
  const chip = request
    ? { done: "At the hub", failed: p.status === "returned" ? "Cancelled" : "Not collected",
        pickup: "To collect", tohub: "Take to the hub" }[state]
    : { done: "Delivered", failed: p.status === "returned" ? "Returned" : "Not delivered",
        collect: "Collect first", todo: "To deliver" }[state];
  const card = document.createElement("div");
  card.className = `stop-card ${state}${isNext ? " next" : ""}`;
  const at = request && state === "pickup" ? p.pickup : p.destination;
  const ll = at && typeof at.lat === "number" ? `${at.lat},${at.lng}` : "";
  const hhmm = ts => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const pk = p.pickup || {};
  card.innerHTML = `
    ${isNext ? `<div class="stop-next-tag">Next stop</div>` : ""}
    <div class="stop-top"><span class="stop-n">${n}</span>
      <b>${stopEsc(p.trackingNumber || p.name)}</b><span class="stop-chip">${chip}</span></div>
    <div class="stop-name">${stopEsc(p.name)}${p.fragile ? " · fragile" : ""}</div>
    <div class="stop-line">${stopEsc(p.receiverName || "Receiver")}${p.recipientPhone
      ? ` · <a href="tel:${stopEsc(p.recipientPhone)}">${stopEsc(p.recipientPhone)}</a>` : ""}</div>
    <div class="stop-line">${request && state === "pickup" ? "Collect from " + stopEsc(pk.label)
      : request ? "To " + stopEsc(p.destination.label) : stopEsc(p.destination.label)}</div>
    ${request && state === "pickup" ? `<div class="stop-note">${pk.earliest && !p.earlyOk ? "Customer cut-off " + hhmm(pk.earliest) + " · " : ""}collect by ${pk.latest ? hhmm(pk.latest) : "—"}${
      p.earlyOk ? " · customer confirmed early collection" : ""}</div>` : ""}
    ${p.notes ? `<div class="stop-note">${stopEsc(p.notes)}</div>` : ""}
    ${p.status === "delivered" && p.pod ? `<div class="stop-note">${stopEsc(p.pod.label)}${
      p.pod.receivedBy ? ` — ${stopEsc(p.pod.receivedBy)}` : ""}</div>` : ""}
    ${state === "failed" && p.lastFailure ? `<div class="stop-note">${stopEsc(p.lastFailure.label)}</div>` : ""}
    ${isNext && state !== "tohub" && (p.recipientPhone || ll) ? `<div class="stop-contact">
      ${p.recipientPhone ? `<a class="stop-big" href="tel:${stopEsc(p.recipientPhone)}">Call</a>` : ""}
      ${ll ? `<a class="stop-big" target="_blank" rel="noopener" href="https://www.google.com/maps/dir/?api=1&destination=${ll}&travelmode=driving">Navigate</a>` : ""}</div>` : ""}
    <div class="stop-actions"></div>`;
  const actions = card.querySelector(".stop-actions");
  if (state === "pickup") {
    actions.innerHTML = `<button type="button" class="stop-btn primary">Collected</button>
      <button type="button" class="stop-btn">Couldn’t collect</button>`;
    const [ok, no] = actions.querySelectorAll("button");
    ok.onclick = () => showCollectForm(card, p);
    no.onclick = () => showFailForm(card, p);
  } else if (onRoad && state === "todo") {
    actions.innerHTML = `<button type="button" class="stop-btn primary">Delivered</button>
      <button type="button" class="stop-btn">Couldn’t deliver</button>`;
    const [ok, no] = actions.querySelectorAll("button");
    ok.onclick = () => showDeliverForm(card, p);
    no.onclick = () => showFailForm(card, p);
  } else if (state === "done" && !p.pod) {
    actions.innerHTML = `<button type="button" class="stop-btn">Add proof</button>`;
    actions.querySelector("button").onclick = () => showDeliverForm(card, p);
  }
  return card;
}

// ---------------------------------------------------------------- collected

function showCollectForm(card, p) {
  const box = card.querySelector(".stop-actions");
  box.innerHTML = `
    <div class="stop-form">
      <label>Note<input id="sc-note" maxlength="300" placeholder="e.g. sealed box, 2 items" autocomplete="off"></label>
      <div class="stop-err" id="sc-err" role="alert" hidden></div>
      <div class="stop-row">
        <button type="button" class="stop-btn" id="sc-cancel">Cancel</button>
        <button type="button" class="stop-btn primary" id="sc-save">Confirm collected</button>
      </div>
    </div>`;
  box.querySelector("#sc-cancel").onclick = () => loadStops();
  box.querySelector("#sc-save").onclick = async () => {
    const save = box.querySelector("#sc-save");
    save.disabled = true;
    try {
      await api(`/api/parcels/${p.id}/collect`, {
        method: "POST", body: JSON.stringify({ note: box.querySelector("#sc-note").value.trim() || null, by: "driver" }) });
      await loadStops();
    } catch (err) {
      const el = box.querySelector("#sc-err");
      el.textContent = err.message; el.hidden = false;
      save.disabled = false;
    }
  };
}

// ---------------------------------------------------------------- delivered

function showDeliverForm(card, p) {
  const box = card.querySelector(".stop-actions");
  box.innerHTML = `
    <div class="stop-form">
      <div class="seg" id="sd-method" role="group" aria-label="How was it handed over?">
        <button type="button" class="active" data-m="handed_over" aria-pressed="true">Handed over</button>
        <button type="button" data-m="left_safe" aria-pressed="false">Left safe</button>
      </div>
      <label>Received by<input id="sd-by" placeholder="${stopEsc(p.receiverName || "Name")}" maxlength="80" autocomplete="off"></label>
      <label>Receipt reference <span class="stop-label"><span>(from your e-signature app)</span></span>
        <input id="sd-ref" maxlength="60" placeholder="e.g. ESIG-004512" autocomplete="off"></label>
      <div class="stop-row"><label class="stop-photo">Add photo<input id="sd-photo" type="file" accept="image/*" capture="environment" hidden></label></div>
      <img id="sd-thumb" alt="Delivery photo preview" hidden>
      <label>Note<input id="sd-note" maxlength="300" placeholder="e.g. left with the security desk" autocomplete="off"></label>
      <div class="stop-err" id="sd-err" role="alert" hidden></div>
      <div class="stop-row">
        <button type="button" class="stop-btn" id="sd-cancel">Cancel</button>
        <button type="button" class="stop-btn primary" id="sd-save">Confirm delivery</button>
      </div>
    </div>`;
  let method = "handed_over", photo = null;
  box.querySelectorAll("#sd-method button").forEach(b => {
    b.onclick = () => {
      method = b.dataset.m;
      box.querySelectorAll("#sd-method button").forEach(x => {
        x.classList.toggle("active", x === b);
        x.setAttribute("aria-pressed", String(x === b));
      });
    };
  });
  box.querySelector("#sd-photo").onchange = async e => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      photo = await downscaleImage(file, 900);
      const t = box.querySelector("#sd-thumb");
      t.src = photo; t.hidden = false;
    } catch (_) { stopError(box, "Couldn’t read that photo."); }
  };
  box.querySelector("#sd-cancel").onclick = () => loadStops();
  box.querySelector("#sd-save").onclick = async () => {
    const by = box.querySelector("#sd-by").value.trim();
    const note = box.querySelector("#sd-note").value.trim();
    const reference = box.querySelector("#sd-ref").value.trim();
    const chosen = photo && method === "handed_over" && !by && !reference ? "photo" : method;
    const save = box.querySelector("#sd-save");
    save.disabled = true;
    stopError(box, "");
    try {
      await api(`/api/parcels/${p.id}/pod`, {
        method: "POST",
        body: JSON.stringify({
          method: chosen, receivedBy: by || null, note: note || null, reference: reference || null, photo,
          lat: lastKnown("lat"), lng: lastKnown("lng"), by: "driver",
        }),
      });
      await loadStops();
    } catch (err) {
      stopError(box, err.message);
      save.disabled = false;
    }
  };
}

function lastKnown(axis) {
  return (typeof lastPos !== "undefined" && lastPos && typeof lastPos[axis] === "number") ? lastPos[axis] : null;
}

function stopError(box, msg) {
  const el = box.querySelector(".stop-err");
  if (!el) return;
  el.textContent = msg;
  el.hidden = !msg;
}

// ------------------------------------------------------------------- failed

function showFailForm(card, p) {
  const box = card.querySelector(".stop-actions");
  box.innerHTML = `
    <div class="stop-form">
      <div class="stop-label">What happened?</div>
      ${(p.requestType === "pickup" ? STOP_PICKUP_FAIL_REASONS : STOP_FAIL_REASONS).map(([k, v], i) => `
        <label class="stop-radio"><input type="radio" name="sf-reason" value="${k}" ${i === 0 ? "checked" : ""}><span>${v}</span></label>`).join("")}
      <label>Note<input id="sf-note" maxlength="300" autocomplete="off" placeholder="Anything dispatch should know"></label>
      <div class="stop-err" id="sf-err" role="alert" hidden></div>
      <div class="stop-row">
        <button type="button" class="stop-btn" id="sf-cancel">Cancel</button>
        <button type="button" class="stop-btn danger" id="sf-save">Report problem</button>
      </div>
    </div>`;
  box.querySelector("#sf-cancel").onclick = () => loadStops();
  box.querySelector("#sf-save").onclick = async () => {
    const save = box.querySelector("#sf-save");
    save.disabled = true;
    try {
      await api(`/api/parcels/${p.id}/fail`, {
        method: "POST",
        body: JSON.stringify({
          reason: box.querySelector("input[name=sf-reason]:checked").value,
          note: box.querySelector("#sf-note").value.trim() || null, by: "driver",
        }),
      });
      await loadStops();
    } catch (err) {
      const el = box.querySelector("#sf-err");
      el.textContent = err.message; el.hidden = false;
      save.disabled = false;
    }
  };
}

// ------------------------------------------------------------------ helpers

// shrink a camera photo so the upload stays small (JPEG, longest edge `max` px)
function downscaleImage(file, max) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", 0.7));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("bad image")); };
    img.src = url;
  });
}
