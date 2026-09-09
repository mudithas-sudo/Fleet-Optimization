// Driver PWA — navigator-style UI over a simulated drive.
// Anatomy modeled on real navigation apps: top turn-instruction banner,
// bottom ETA bar, speedometer bubble, chevron vehicle marker, tilted
// heading-up camera, traveled route grayed out.

const $ = id => document.getElementById(id);

let map, trip, path = [], cum = [], steps = [];
let routeLine = null, traveledLine = null, stopMarkers = [], trafficLines = [];
let vehicle = null, vehicleEl = null;
let traveled = 0, timer = null, reloading = false;
let deviating = false, offroute = false, offset = 0;
let stopped = false;   // driver simulated a stop (not traffic) — freezes motion
let speedKmh = 50;
let camHeading = 0, follow = true;
let stepIdx = 0;
let rafId = null, lastFrameT = 0, arrived = false, camAccumT = 0;
let lastPos = null, lastLo = 0;   // most recent simulated position (for the 1s server tick)
let driverId = null;              // set when opened via a per-driver queue link

const OFFSET_RATE = 15 /* m per second */, OFFSET_MAX = 300, TICK_MS = 1000;
const SPEED_PRESETS = [30, 50, 80, 110];
const NAV_ZOOM = 17.5;

// ---- maneuver icons (stroke SVGs, Google-nav style) ----
const MANEUVER_PATHS = {
  straight: '<path d="M12 21 V7"/><path d="M7 11 L12 6 L17 11"/>',
  left: '<path d="M16 21 V14 Q16 11 13 11 H7"/><path d="M10 7.5 L6.5 11 L10 14.5"/>',
  right: '<path d="M8 21 V14 Q8 11 11 11 H17"/><path d="M14 7.5 L17.5 11 L14 14.5"/>',
  uturn: '<path d="M16 21 V10 A4 4 0 0 0 8 10 V15"/><path d="M5 12.5 L8 16 L11 12.5"/>',
  roundabout: '<circle cx="12" cy="14" r="4.5"/><path d="M12 21 V18.5"/><path d="M12 9.5 V4"/><path d="M9 6.5 L12 3.5 L15 6.5"/>',
  arrive: '<path d="M8 21 V4"/><path d="M8 5 H17 L14.5 8 L17 11 H8"/>',
};

function maneuverIcon(m) {
  m = (m || "").toUpperCase();
  let body = MANEUVER_PATHS.straight, rot = 0;
  if (m.includes("UTURN")) body = MANEUVER_PATHS.uturn;
  else if (m.includes("ROUNDABOUT")) body = MANEUVER_PATHS.roundabout;
  else if (m === "ARRIVE" || m.includes("DESTINATION")) body = MANEUVER_PATHS.arrive;
  else if (m.includes("SLIGHT_LEFT") || m === "FORK_LEFT" || m === "RAMP_LEFT") rot = -35;
  else if (m.includes("SLIGHT_RIGHT") || m === "FORK_RIGHT" || m === "RAMP_RIGHT") rot = 35;
  else if (m.includes("LEFT")) body = MANEUVER_PATHS.left;
  else if (m.includes("RIGHT")) body = MANEUVER_PATHS.right;
  const g = rot ? `<g transform="rotate(${rot} 12 12)">${body}</g>` : body;
  return `<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4"
    stroke-linecap="round" stroke-linejoin="round">${g}</svg>`;
}

init();

async function init() {
  initTheme();
  const params = new URLSearchParams(location.search);
  const tripId = params.get("trip");
  driverId = params.get("driver");

  if (driverId && !tripId) return initQueue();     // per-driver queue view
  if (!tripId) { $("briefing").textContent = "No trip id. Open this page via the link from the dispatch console."; return; }
  if (driverId) {
    $("btn-queue").classList.remove("hidden");
    $("btn-queue").onclick = () => { location.search = `?driver=${driverId}`; };
  }

  await loadMaps();
  sizeMapForRotation();
  window.addEventListener("resize", () => { sizeMapForRotation(); if (!follow) fitVisible(); });
  map = new google.maps.Map($("map"), {
    center: { lat: 6.9271, lng: 79.8612 }, zoom: 12,
    mapId: "DEMO_MAP_ID",           // enables AdvancedMarker
    disableDefaultUI: true, clickableIcons: false,
  });

  try {
    trip = await api(`/api/trips/${tripId}`);
  } catch (err) {
    $("briefing").textContent = "Could not load trip: " + err.message;
    return;
  }

  buildRouteGeometry();
  routeLine = new google.maps.Polyline({
    map, path, strokeColor: routeColor(), strokeOpacity: 0.95, strokeWeight: 7,
  });
  traveledLine = new google.maps.Polyline({
    map, path: [], strokeColor: "#9aa0a6", strokeOpacity: 0.9, strokeWeight: 7, zIndex: 2,
  });
  trafficLines = drawTraffic(map, path, trip.route.traffic);
  drawStops();
  fitVisible();

  $("trip-meta").innerHTML =
    `${vehIconSvg(trip.vehicleType)} <span>${trip.id} · ${fmtKm(trip.route.totalDistanceMeters)} · ${fmtMin(trip.route.totalDurationSeconds)}</span>`;
  if (trip.vehicleType === "truck") {   // trucks are speed-capped in the simulator
    $("speed").max = 80;
    if (speedKmh > 80) setSpeed(80);
  }
  $("briefing").textContent = trip.route.briefing;
  $("btn-start").disabled = false;

  $("speed").oninput = () => setSpeed(Number($("speed").value));
  const cycleSpeed = () => {
    const max = Number($("speed").max);
    const presets = SPEED_PRESETS.filter(v => v <= max);
    const next = presets[(presets.indexOf(speedKmh) + 1) % presets.length] ?? presets[0];
    setSpeed(next);
  };
  $("speed-bubble").onclick = cycleSpeed;
  $("speed-bubble").onkeydown = e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); cycleSpeed(); }
  };
  $("btn-start").onclick = startTrip;
  $("btn-deviate").onclick = toggleDeviate;
  $("btn-stop").onclick = toggleStop;
  $("btn-end").onclick = () => endTrip(false);
  $("btn-overview").onclick = toggleOverview;

  // trip already live (page refreshed mid-drive): resume from the server's
  // last stored position instead of restarting from the origin
  if (["active", "deviating"].includes(trip.status) && trip.lastPosition) {
    beginDrive(projectTraveled(trip.lastPosition));
  }
}

// Perspective tilt for the "behind the driver" navigator view (CSS, so it
// works on every renderer — the vector camera's native tilt does not).
const NAV_TILT_DEG = 50;

// Make #map an oversized centered square so no corner or horizon-gap shows
// while the element is CSS-rotated and perspective-tilted.
function mapSide() {
  return Math.ceil(Math.hypot(innerWidth, innerHeight) * 1.9);
}

function sizeMapForRotation() {
  const side = mapSide();
  const el = $("map");
  el.style.width = el.style.height = side + "px";
  el.style.left = Math.round((innerWidth - side) / 2) + "px";
  el.style.top = Math.round((innerHeight - side) / 2) + "px";
}

function setMapRotation(deg) {
  // rotate happens in the map plane first, then the plane tilts away from the
  // viewer — heading-up plus the chase-camera perspective
  $("map").style.transform =
    `perspective(1100px) rotateX(${NAV_TILT_DEG}deg) rotate(${-deg}deg)`;
}

function setMapFlat() {
  $("map").style.transform = "rotate(0deg)";
}

// fitBounds targets the oversized square; pad so the route lands inside the
// actual visible viewport crop
function fitVisible() {
  if (!path.length) return;
  const side = mapSide();
  const padX = (side - innerWidth) / 2;
  const padY = (side - innerHeight) / 2;
  const bounds = new google.maps.LatLngBounds();
  path.forEach(p => bounds.extend(p));
  map.fitBounds(bounds, {
    top: padY + 90, bottom: padY + 260, left: padX + 40, right: padX + 40,
  });
}

// meters per screen pixel at this zoom/latitude — used to place the vehicle
// in the lower third of the view by centering ahead of it
function lookAheadMeters(lat) {
  const mpp = 156543.03392 * Math.cos(lat * Math.PI / 180) / 2 ** map.getZoom();
  return 175 * mpp;
}

function buildRouteGeometry() {
  path = decodedPath(trip);
  cum = [0];
  for (let i = 1; i < path.length; i++) cum.push(cum[i - 1] + meters(path[i - 1], path[i]));
  // scale step end-distances so they line up with the decoded path length
  const rawSteps = trip.route.steps || [];
  const scale = rawSteps.length && rawSteps[rawSteps.length - 1].endDist > 0
    ? cum[cum.length - 1] / rawSteps[rawSteps.length - 1].endDist : 1;
  steps = rawSteps.map(s => ({ ...s, endDist: s.endDist * scale }));
}

function drawStops() {
  stopMarkers.forEach(m => m.setMap(null));
  stopMarkers = trip.route.orderedStops.map((s, i) =>
    stopMarker(map, s, i, trip.route.orderedStops.length, s.kind));
}

// The server rerouted (deviation): reload the new route and keep driving
// from the current position, which is the new route's origin.
async function reloadRoute() {
  reloading = true;
  try {
    trip = await api(`/api/trips/${trip.id}`);
    buildRouteGeometry();
    routeLine.setPath(path);
    traveledLine.setPath([]);
    trafficLines.forEach(l => l.setMap(null));
    trafficLines = drawTraffic(map, path, trip.route.traffic);
    drawStops();
    traveled = 0;
    stepIdx = 0;
    deviating = false;
    offset = 0;
    $("btn-deviate").classList.remove("on");
    setOffroute(false);
  } finally {
    reloading = false;
  }
}

function setSpeed(v) {
  speedKmh = v;
  $("speed-cur").textContent = v;
  $("speed-val").textContent = v + " km/h";
  $("speed").value = v;
}

function meters(a, b) {
  return google.maps.geometry.spherical.computeDistanceBetween(a, b);
}

// Top-view vehicle sprites are shared with the admin fleet map — see
// VEHICLE_SPRITES in common.js. They lie flat in the map plane, so the
// chase-camera perspective tilt foreshortens them like the road — the
// vehicle reads as 3D, seen from behind while navigating.
// ---- per-driver queue mode ----

async function initQueue() {
  $("queue").classList.remove("hidden");
  $("sheet").classList.add("hidden");
  const btn = $("theme-toggle-q");
  setThemeToggle(btn, currentTheme());
  btn.onclick = () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    try { localStorage.setItem("fleetops-theme", next); } catch (_) {}
    applyTheme(next);
  };
  await refreshQueue();
  setInterval(() => { if (!document.hidden) refreshQueue(); }, 30000);
}

async function refreshQueue() {
  let q;
  try {
    q = await api(`/api/drivers/${driverId}/queue`);
  } catch (err) {
    $("q-runs").innerHTML = `<div class="q-empty">${iconSvg("alert")}
      <div>Could not load your runs<br>${err.message}</div></div>`;
    return;
  }
  $("q-name").textContent = q.driver.name;
  $("q-vehicle").innerHTML = q.vehicle
    ? `${vehIconSvg(q.vehicle.type)} <span>${q.vehicle.name} · ${q.vehicle.plate || q.vehicle.type}</span>`
    : `${iconSvg("help")} <span>No vehicle assigned</span>`;
  const wrap = $("q-runs");
  wrap.innerHTML = "";
  if (!q.runs.length) {
    wrap.innerHTML = `<div class="q-empty">${iconSvg("truck")}
      <div><b>No runs assigned yet</b><br>Dispatch will send work your way.</div></div>`;
    return;
  }
  q.runs.forEach(r => {
    const live = r.status === "active" || r.status === "deviating";
    const done = r.status === "completed";
    const row = document.createElement("div");
    row.className = "q-run" + (done ? " done" : "");
    row.innerHTML = `
      <div class="info">
        <div class="label">${r.label}</div>
        <div class="meta">${r.parcelCount} parcel${r.parcelCount === 1 ? "" : "s"} ·
          ${fmtKm(r.totalDistanceMeters)} · ${fmtMin(r.totalDurationSeconds)}
          ${done ? ` · done (${r.deliveredCount}/${r.parcelCount})` : ""}</div>
      </div>
      ${done ? "" : `<button class="go ${live ? "live" : ""}">${live ? "Continue" : "Start"}</button>`}`;
    const go = row.querySelector(".go");
    if (go) go.onclick = () => { location.search = `?trip=${r.id}&driver=${driverId}`; };
    wrap.append(row);
  });
}

function makeVehicle(position) {
  const sprite = VEHICLE_SPRITES[trip.vehicleType] || VEHICLE_SPRITES.car;
  vehicleEl = document.createElement("div");
  // rotation set per-frame; the drop shadow adds lift off the road plane
  vehicleEl.style.cssText =
    `width:${sprite.w}px;height:${sprite.h}px;` +
    "filter:drop-shadow(0 3px 3px rgba(0,0,0,.4))";
  vehicleEl.innerHTML = spriteHTML(sprite);
  // AdvancedMarker anchors content at bottom-center (pin convention); shift
  // down half the height so the sprite centers on the coordinate. The wrapper
  // keeps this offset separate from the per-frame rotation.
  const wrap = document.createElement("div");
  wrap.style.transform = "translateY(50%)";
  wrap.append(vehicleEl);
  try {
    return new google.maps.marker.AdvancedMarkerElement({
      map, position, content: wrap, zIndex: 999,
    });
  } catch (_) {
    vehicleEl = null;               // raster fallback: no rotation
    return vehicleMarker(map, position);
  }
}

async function startTrip() {
  await api(`/api/trips/${trip.id}/start`, { method: "POST" });
  beginDrive(0);
}

// enter navigation mode at a given progress along the route — 0 for a fresh
// start, or the projected position when resuming a live trip after a refresh
function beginDrive(fromTraveled) {
  $("sheet").classList.add("hidden");
  ["banner", "eta-bar", "speed-bubble"].forEach(id => $(id).classList.remove("hidden"));
  $("btn-deviate").classList.remove("hidden");
  $("btn-stop").classList.remove("hidden");
  traveled = fromTraveled; stepIdx = 0; arrived = false;
  const [lo, hi] = segmentAt(traveled);
  const t = (traveled - cum[lo]) / (cum[hi] - cum[lo] || 1);
  lastPos = {
    lat: path[lo].lat + (path[hi].lat - path[lo].lat) * t,
    lng: path[lo].lng + (path[hi].lng - path[lo].lng) * t,
  };
  vehicle = makeVehicle(lastPos);
  camHeading = bearingAt(traveled);
  map.setZoom(NAV_ZOOM);
  lastFrameT = performance.now();
  rafId = requestAnimationFrame(frame);   // smooth motion, every display frame
  timer = setInterval(tick, TICK_MS);     // server updates + text, once a second
  updateBanner();
  updateEta();
}

// cumulative meters along the route at the point closest to `p` — used to
// resume from the server's last stored position after a page refresh
function projectTraveled(p) {
  const ref = Math.cos(p.lat * Math.PI / 180);
  const px = p.lng * ref, py = p.lat;
  let best = Infinity, bestAlong = 0;
  for (let i = 1; i < path.length; i++) {
    const ax = path[i - 1].lng * ref, ay = path[i - 1].lat;
    const bx = path[i].lng * ref, by = path[i].lat;
    const dx = bx - ax, dy = by - ay;
    let t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy || 1e-12);
    t = Math.max(0, Math.min(1, t));
    const d2 = (px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2;
    if (d2 < best) { best = d2; bestAlong = cum[i - 1] + t * (cum[i] - cum[i - 1]); }
  }
  return bestAlong;
}

function segmentAt(dist) {
  let lo = 0, hi = cum.length - 1;
  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (cum[mid] <= dist) lo = mid; else hi = mid;
  }
  return [lo, hi];
}

function bearingAt(dist) {
  const [lo, hi] = segmentAt(dist);
  return google.maps.geometry.spherical.computeHeading(
    new google.maps.LatLng(path[lo]), new google.maps.LatLng(path[hi]));
}

function lerpAngle(a, b, t) {
  let d = ((b - a + 540) % 360) - 180;
  return a + d * t;
}

// physics: advance the simulation by dt seconds and update lastPos/lastLo.
// Kept separate from rendering so a backgrounded tab (rAF paused, timers
// throttled) can still make progress from the server tick.
function advanceSim(dt) {
  const total = cum[cum.length - 1];
  // a simulated stop freezes forward progress — position keeps posting, so the
  // backend sees no movement and (after STALL_SECONDS) alerts the dispatcher
  if (!stopped) traveled = Math.min(traveled + (speedKmh / 3.6) * dt, total);
  offset = deviating
    ? Math.min(offset + OFFSET_RATE * dt, OFFSET_MAX)
    : Math.max(offset - OFFSET_RATE * dt, 0);

  const [lo, hi] = segmentAt(traveled);
  const segLen = cum[hi] - cum[lo] || 1;
  const t = (traveled - cum[lo]) / segLen;
  let lat = path[lo].lat + (path[hi].lat - path[lo].lat) * t;
  let lng = path[lo].lng + (path[hi].lng - path[lo].lng) * t;
  const bearing = bearingAt(traveled);
  if (offset > 0) {
    const p = google.maps.geometry.spherical.computeOffset(
      new google.maps.LatLng({ lat, lng }), offset, bearing + 90);
    lat = p.lat(); lng = p.lng();
  }
  lastPos = { lat, lng };
  lastLo = lo;
  return { pos: lastPos, bearing, lat };
}

// render loop: advances the simulation continuously so motion, rotation and
// camera are updated every display frame instead of jumping once a second
function frame(now) {
  rafId = requestAnimationFrame(frame);
  const dt = Math.min((now - lastFrameT) / 1000, 0.2); // clamp tab-restore jumps
  lastFrameT = now;
  if (reloading || arrived) return;

  const { pos, bearing, lat } = advanceSim(dt);

  if (vehicle.position !== undefined) vehicle.position = pos; else vehicle.setPosition(pos);
  if (vehicleEl) vehicleEl.style.transform = `rotate(${bearing}deg)`;
  if (follow) {
    // time-based smoothing of the heading, applied directly each frame (CSS only)
    camHeading = lerpAngle(camHeading, bearing, 1 - Math.exp(-2.2 * dt));
    setMapRotation(camHeading);
    // recenter at ~8 Hz: per-frame setCenter thrashes the tile loader and the
    // map never finishes rendering; a ~2 px step at nav zoom is invisible
    camAccumT += dt;
    if (camAccumT >= 0.12) {
      camAccumT = 0;
      const ahead = google.maps.geometry.spherical.computeOffset(
        new google.maps.LatLng(pos), lookAheadMeters(lat), bearing);
      map.setCenter(ahead);
    }
  }

  if (traveled >= cum[cum.length - 1] && offset === 0) { arrived = true; endTrip(true); }
}

// server tick: report position, refresh trail and text — once a second
async function tick() {
  if (reloading || !lastPos) return;

  // hidden tab: rAF is paused, so advance the physics from here using real
  // elapsed time (browsers throttle this timer but keep it alive) — the trip
  // keeps progressing for the dispatcher even while backgrounded
  if (document.hidden && !arrived) {
    const now = performance.now();
    advanceSim(Math.min((now - lastFrameT) / 1000, 90));
    lastFrameT = now;
    if (traveled >= cum[cum.length - 1] && offset === 0) { arrived = true; endTrip(true); }
  }
  traveledLine.setPath([...path.slice(0, lastLo + 1), lastPos]);
  updateBanner();
  updateEta();
  try {
    const res = await api(`/api/trips/${trip.id}/position`, {
      method: "POST",
      body: JSON.stringify({ lat: lastPos.lat, lng: lastPos.lng, ts: Date.now() / 1000 }),
    });
    if (res.alert) setOffroute(res.alert.type === "deviation");
    if (res.routeVersion && res.routeVersion !== trip.routeVersion) {
      await reloadRoute();
    }
  } catch (err) {
    if (String(err.message).includes("Trip not found")) tripCancelled();
    // otherwise: transient network error, keep driving
  }
}

// dispatch deleted this trip while we were driving it
function tripCancelled() {
  clearInterval(timer);
  timer = null;
  cancelAnimationFrame(rafId);
  arrived = true;
  $("banner").classList.add("offroute");
  $("maneuver-icon").innerHTML = maneuverIcon("ARRIVE");
  $("maneuver-dist").textContent = "Cancelled";
  $("maneuver-text").textContent = "This trip was removed by dispatch";
  $("btn-deviate").classList.add("hidden");
  $("btn-stop").classList.add("hidden");
  $("speed-bubble").classList.add("hidden");
  $("eta-sub").textContent = "Trip cancelled";
}

function updateBanner() {
  if (stopped && !offroute) {
    $("maneuver-icon").innerHTML =
      `<svg viewBox="0 0 24 24" fill="#fff"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg>`;
    $("maneuver-dist").textContent = "Stopped";
    $("maneuver-text").textContent = "Simulated stop — dispatch is alerted if it lasts";
    return;
  }
  if (offroute) {
    $("maneuver-icon").innerHTML =
      `<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3 L22 20 H2 Z"/><path d="M12 9 V14"/><path d="M12 17.5 V17.6"/></svg>`;
    $("maneuver-dist").textContent = "Off route";
    $("maneuver-text").textContent = "Return to the planned route";
    return;
  }
  while (stepIdx < steps.length - 1 && steps[stepIdx].endDist - traveled < 12) stepIdx++;
  const step = steps[stepIdx];
  if (!step) return;
  // a step's instruction happens at its START; the maneuver coming up at the
  // END of the current step is the NEXT step's instruction — that's what the
  // distance counts down to
  const upcoming = steps[stepIdx + 1] || step;
  const remain = Math.max(0, step.endDist - traveled);
  $("maneuver-icon").innerHTML = maneuverIcon(upcoming.maneuver);
  $("maneuver-dist").textContent =
    remain < 20 ? "Now" :
    remain < 1000 ? `${Math.round(remain / 10) * 10} m` : `${(remain / 1000).toFixed(1)} km`;
  $("maneuver-text").textContent = upcoming.instruction;
}

function updateEta() {
  const remainM = cum[cum.length - 1] - traveled;
  const remainSec = remainM / (speedKmh / 3.6);
  const arrival = new Date(Date.now() + remainSec * 1000);
  $("eta-time").textContent =
    arrival.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("eta-sub").textContent = `${fmtMin(remainSec)} · ${fmtKm(remainM)}`;
}

function setOffroute(on) {
  offroute = on;
  $("banner").classList.toggle("offroute", on);
}

function toggleDeviate() {
  deviating = !deviating;
  $("btn-deviate").classList.toggle("on", deviating);
  $("btn-deviate").setAttribute("aria-pressed", String(deviating));
}

function toggleStop() {
  stopped = !stopped;
  $("btn-stop").classList.toggle("on", stopped);
  $("btn-stop").setAttribute("aria-pressed", String(stopped));
  updateBanner();
}

function toggleOverview() {
  follow = !follow;
  if (!follow) {
    setMapFlat();
    fitVisible();
  } else {
    map.setZoom(NAV_ZOOM);
  }
}

async function endTrip(auto) {
  if (!timer) return;
  clearInterval(timer);
  timer = null;
  cancelAnimationFrame(rafId);
  arrived = true;
  $("banner").classList.remove("offroute");
  $("maneuver-icon").innerHTML = maneuverIcon("ARRIVE");
  $("maneuver-dist").textContent = auto ? "Arrived" : "Ended";
  $("maneuver-text").textContent = auto
    ? `You have arrived at ${trip.route.orderedStops.at(-1).label}` : "Navigation ended";
  $("btn-deviate").classList.add("hidden");
  $("btn-stop").classList.add("hidden");
  $("speed-bubble").classList.add("hidden");
  $("eta-sub").textContent = "Trip completed";
  setMapFlat();
  fitVisible();
  await api(`/api/trips/${trip.id}/end`,
    { method: "POST", body: JSON.stringify({ arrived: auto }) });
}
