// Driver "My stops" sheet: see the run's parcels in stop order, confirm a
// delivery with proof (signature, photo, who received it) or report that it
// couldn't be delivered. Loads before driver.js; uses its `trip` / `lastPos`.

const STOP_FAIL_REASONS = [
  ["no_one_home", "Nobody home"],
  ["wrong_address", "Wrong or incomplete address"],
  ["refused", "Receiver refused the parcel"],
  ["damaged", "Parcel damaged"],
  ["access_problem", "Couldn’t get access (gate, road, security)"],
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
  rows.forEach((p, i) => body.append(stopCard(p, i + 1)));
}

function stopCard(p, n) {
  const onRoad = ["in_transit", "picked_up", "assigned"].includes(p.status);
  const state = p.status === "delivered" ? "done"
    : p.status === "awaiting_redelivery" || p.status === "returned" ? "failed"
    : p.collectFirst ? "collect" : "todo";
  const chip = { done: "Delivered", failed: p.status === "returned" ? "Returned" : "Not delivered",
    collect: "Collect first", todo: "To deliver" }[state];
  const card = document.createElement("div");
  card.className = `stop-card ${state}`;
  card.innerHTML = `
    <div class="stop-top"><span class="stop-n">${n}</span>
      <b>${stopEsc(p.trackingNumber || p.name)}</b><span class="stop-chip">${chip}</span></div>
    <div class="stop-name">${stopEsc(p.name)}${p.fragile ? " · fragile" : ""}</div>
    <div class="stop-line">${stopEsc(p.receiverName || "Receiver")}${p.recipientPhone
      ? ` · <a href="tel:${stopEsc(p.recipientPhone)}">${stopEsc(p.recipientPhone)}</a>` : ""}</div>
    <div class="stop-line">${stopEsc(p.destination.label)}</div>
    ${p.notes ? `<div class="stop-note">${stopEsc(p.notes)}</div>` : ""}
    ${p.status === "delivered" && p.pod ? `<div class="stop-note">${stopEsc(p.pod.label)}${
      p.pod.receivedBy ? ` — ${stopEsc(p.pod.receivedBy)}` : ""}</div>` : ""}
    ${state === "failed" && p.lastFailure ? `<div class="stop-note">${stopEsc(p.lastFailure.label)}</div>` : ""}
    <div class="stop-actions"></div>`;
  const actions = card.querySelector(".stop-actions");
  if (onRoad && state === "todo") {
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
      <div class="stop-label">Signature <span>(optional)</span></div>
      <canvas id="sd-sign" width="600" height="220" aria-label="Signature pad"></canvas>
      <div class="stop-row"><button type="button" class="stop-link" id="sd-clear">Clear signature</button>
        <label class="stop-photo">Add photo<input id="sd-photo" type="file" accept="image/*" capture="environment" hidden></label></div>
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
  const pad = signaturePad(box.querySelector("#sd-sign"));
  box.querySelector("#sd-clear").onclick = pad.clear;
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
    const signature = pad.isEmpty() ? null : pad.data();
    // strongest evidence wins the label; the rest still rides along
    const chosen = signature ? "signature" : photo ? "photo" : method;
    const save = box.querySelector("#sd-save");
    save.disabled = true;
    stopError(box, "");
    try {
      await api(`/api/parcels/${p.id}/pod`, {
        method: "POST",
        body: JSON.stringify({
          method: chosen, receivedBy: by || null, note: note || null, signature, photo,
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
      ${STOP_FAIL_REASONS.map(([k, v], i) => `
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

function signaturePad(canvas) {
  const ctx = canvas.getContext("2d");
  ctx.lineWidth = 5; ctx.lineCap = "round"; ctx.lineJoin = "round";
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#1a2234";
  let drawing = false, dirty = false;
  const at = e => {
    const r = canvas.getBoundingClientRect();
    return [(e.clientX - r.left) * canvas.width / r.width, (e.clientY - r.top) * canvas.height / r.height];
  };
  canvas.style.touchAction = "none";
  canvas.onpointerdown = e => { drawing = true; canvas.setPointerCapture(e.pointerId); ctx.beginPath(); ctx.moveTo(...at(e)); };
  canvas.onpointermove = e => { if (!drawing) return; ctx.lineTo(...at(e)); ctx.stroke(); dirty = true; };
  canvas.onpointerup = canvas.onpointercancel = () => { drawing = false; };
  return {
    clear() { ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height); dirty = false; },
    isEmpty: () => !dirty,
    data: () => canvas.toDataURL("image/png"),
  };
}

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
