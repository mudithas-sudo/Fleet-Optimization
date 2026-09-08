// Shared helpers for admin + driver pages.

// ---- icon set (Lucide-style stroke SVGs; no emoji anywhere in the chrome) ----
const ICONS = {
  car: '<path d="M5 17H3a1 1 0 0 1-1-1v-3.3a1 1 0 0 1 .7-.95l1.9-.64a1 1 0 0 0 .58-.5l1.54-3.08A2 2 0 0 1 9 6h6a2 2 0 0 1 1.8 1.1l1.54 3.07a1 1 0 0 0 .58.5l1.9.64a1 1 0 0 1 .7.95V16a1 1 0 0 1-1 1h-2"/><circle cx="7.5" cy="17" r="2"/><circle cx="16.5" cy="17" r="2"/><path d="M9.5 17h5"/>',
  van: '<path d="M3 6h11a2 2 0 0 1 2 2v9H3z"/><path d="M16 9h3l3 4v4h-6"/><circle cx="7.5" cy="17" r="2"/><circle cx="17.5" cy="17" r="2"/>',
  truck: '<path d="M14 17V6a1 1 0 0 0-1-1H3a1 1 0 0 0-1 1v11h2"/><path d="M14 9h4l3 3v5h-3"/><path d="M10 17h1"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
  bike: '<circle cx="6" cy="17" r="3.2"/><circle cx="18" cy="17" r="3.2"/><path d="M6 17 12 6h3"/><path d="m12 6 3.5 6.5H18"/><circle cx="15" cy="5" r="1"/>',
  user: '<circle cx="12" cy="8" r="3.5"/><path d="M5.5 20a6.5 6.5 0 0 1 13 0"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.2 9a3 3 0 0 1 5.5 1.2c0 1.8-2.7 2.3-2.7 3.8"/><path d="M12 17h.01"/>',
  depot: '<path d="M3 21h18M4 21V10l8-6 8 6v11M9 21v-6h6v6"/>',
  pin: '<path d="M12 21s-7-6.3-7-12a7 7 0 0 1 14 0c0 5.7-7 12-7 12Z"/><circle cx="12" cy="9" r="2.5"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  alert: '<path d="M12 3 2 20h20Z"/><path d="M12 9v5"/><path d="M12 17.5h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  trash: '<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M18 6l-1 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L6 6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  map: '<path d="m9 4 6 2 6-2v14l-6 2-6-2-6 2V6z"/><path d="M9 4v14M15 6v14"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M5 5l1.5 1.5M17.5 17.5 19 19M2 12h2M20 12h2M5 19l1.5-1.5M17.5 6.5 19 5"/>',
};

// inline <svg> string for an icon name (decorative by default)
function iconSvg(name, cls = "") {
  return `<svg viewBox="0 0 24 24" class="i ${cls}" aria-hidden="true" fill="none" ` +
    `stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
    `${ICONS[name] || ICONS.help}</svg>`;
}

const VEHICLE_ICON_NAME = { car: "car", van: "van", truck: "truck", bike: "bike" };
function vehIconSvg(type, cls = "") { return iconSvg(VEHICLE_ICON_NAME[type] || "help", cls); }

// ---- theme: light by default, dark optional, persisted per browser ----
const THEME_KEY = "fleetops-theme";
const _themeListeners = [];

function currentTheme() {
  try { return localStorage.getItem(THEME_KEY) || "light"; } catch (_) { return "light"; }
}

// paint a theme-toggle button as an icon control with the right a11y state
function setThemeToggle(btn, theme) {
  if (!btn) return;
  const dark = theme === "dark";
  btn.innerHTML = dark ? iconSvg("sun") : iconSvg("moon");
  const label = dark ? "Switch to light theme" : "Switch to dark theme";
  btn.setAttribute("aria-label", label);
  btn.setAttribute("aria-pressed", String(dark));
  btn.title = label;
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  setThemeToggle(document.getElementById("theme-toggle"), theme);
  setThemeToggle(document.getElementById("theme-toggle-q"), theme);
  _themeListeners.forEach(fn => fn(theme));
}

function initTheme() {
  applyTheme(currentTheme());
  const btn = document.getElementById("theme-toggle");
  if (btn) btn.onclick = () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    try { localStorage.setItem(THEME_KEY, next); } catch (_) {}
    applyTheme(next);
  };
}

function onThemeChange(fn) { _themeListeners.push(fn); }

function mapThemeStyles() { return currentTheme() === "dark" ? MAP_DARK_STYLE : null; }
// routes are blue (the universal navigation convention) so the amber/red
// traffic overlays and alert colors stay unambiguous — tracking blue from
// the design system
function routeColor() { return currentTheme() === "dark" ? "#5b9bff" : "#2563eb"; }

const MAP_DARK_STYLE = [
  { elementType: "geometry", stylers: [{ color: "#1d2228" }] },
  { elementType: "labels.text.fill", stylers: [{ color: "#8b949e" }] },
  { elementType: "labels.text.stroke", stylers: [{ color: "#14171c" }] },
  { featureType: "road", elementType: "geometry", stylers: [{ color: "#2c333b" }] },
  { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#3a4450" }] },
  { featureType: "water", elementType: "geometry", stylers: [{ color: "#101418" }] },
  { featureType: "poi", stylers: [{ visibility: "off" }] },
  { featureType: "transit", stylers: [{ visibility: "off" }] },
];

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function decodedPath(trip) {
  return google.maps.geometry.encoding
    .decodePath(trip.route.polyline)
    .map(p => ({ lat: p.lat(), lng: p.lng() }));
}

function fmtKm(m) { return (m / 1000).toFixed(1) + " km"; }
function fmtMin(s) { return Math.round(s / 60) + " min"; }
function fmtClock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// Numbered stop marker (classic Marker with inline SVG icon).
// kind: "depot" | "pickup" | "delivery" (optional) — pickups are amber "P".
function stopMarker(map, position, index, total, kind) {
  const isEnd = index === total - 1;
  const isPickup = kind === "pickup";
  const fill = index === 0 ? "#0f7a43" : isPickup ? "#8a5200" : isEnd ? "#ea580c" : "#2563eb";
  const label = index === 0 ? "S" : isPickup ? "P" : isEnd ? "E" : String(index);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="34" height="44" viewBox="0 0 34 44">
    <path d="M17 0C7.6 0 0 7.6 0 17c0 12.8 17 27 17 27s17-14.2 17-27C34 7.6 26.4 0 17 0z" fill="${fill}"/>
    <circle cx="17" cy="16" r="10" fill="#ffffff"/>
    <text x="17" y="21" font-family="'Plus Jakarta Sans',Arial,sans-serif" font-size="13" font-weight="800" fill="${fill}" text-anchor="middle">${label}</text>
  </svg>`;
  return new google.maps.Marker({
    map, position,
    icon: {
      url: "data:image/svg+xml;charset=UTF-8," + encodeURIComponent(svg),
      scaledSize: new google.maps.Size(34, 44),
      anchor: new google.maps.Point(17, 44),
    },
  });
}

// Vehicle marker (pulsing dot).
function vehicleMarker(map, position) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28">
    <circle cx="14" cy="14" r="13" fill="#2563eb" opacity="0.2"/>
    <circle cx="14" cy="14" r="7.5" fill="#2563eb" stroke="#ffffff" stroke-width="2.5"/>
  </svg>`;
  return new google.maps.Marker({
    map, position, zIndex: 999,
    icon: {
      url: "data:image/svg+xml;charset=UTF-8," + encodeURIComponent(svg),
      scaledSize: new google.maps.Size(28, 28),
      anchor: new google.maps.Point(14, 14),
    },
  });
}

// ---- top-view vehicle sprites (nose up), shared by driver nav + admin map ----
// `svg` entries are inline markup (no xmlns; injected where needed);
// `url` entries are served assets.
const VEHICLE_SPRITES = {
  car: { w: 32, h: 64, svg: `<svg viewBox="0 0 52 104">
    <defs><linearGradient id="vg-car" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#4d565f"/><stop offset=".5" stop-color="#14181d"/><stop offset="1" stop-color="#4d565f"/>
    </linearGradient></defs>
    <rect x="2" y="29" width="9" height="6" rx="3" fill="#14181d"/>
    <rect x="41" y="29" width="9" height="6" rx="3" fill="#14181d"/>
    <rect x="8" y="3" width="36" height="98" rx="17" fill="url(#vg-car)" stroke="#fff" stroke-width="3.5"/>
    <path d="M13 26 Q26 18 39 26 L37 40 Q26 33 15 40 Z" fill="#aac9e9"/>
    <rect x="14" y="43" width="24" height="30" rx="6" fill="#2c343e"/>
    <path d="M15 77 Q26 83 37 77 L39 90 Q26 97 13 90 Z" fill="#8fb0d4"/>
  </svg>` },
  van: { w: 34, h: 70, svg: `<svg viewBox="0 0 52 110">
    <rect x="2" y="24" width="9" height="6" rx="3" fill="#14181d"/>
    <rect x="41" y="24" width="9" height="6" rx="3" fill="#14181d"/>
    <rect x="7" y="3" width="38" height="104" rx="14" fill="#f7f8f9" stroke="#14181d" stroke-width="3"/>
    <path d="M12 20 Q26 13 40 20 L38 33 Q26 27 14 33 Z" fill="#3d5875"/>
    <rect x="13" y="37" width="26" height="60" rx="6" fill="#dde2e7"/>
    <rect x="15" y="99" width="22" height="5" rx="2.5" fill="#3d5875"/>
  </svg>` },
  truck: { w: 36, h: 75, svg: `<svg viewBox="0 0 56 116">
    <defs>
      <linearGradient id="vg-trk" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#4d565f"/><stop offset=".5" stop-color="#14181d"/><stop offset="1" stop-color="#4d565f"/>
      </linearGradient>
      <linearGradient id="vg-box" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#ccd2d9"/><stop offset=".2" stop-color="#f7f8f9"/><stop offset=".8" stop-color="#f7f8f9"/><stop offset="1" stop-color="#ccd2d9"/>
      </linearGradient>
    </defs>
    <rect x="3" y="15" width="10" height="6" rx="3" fill="#14181d"/>
    <rect x="43" y="15" width="10" height="6" rx="3" fill="#14181d"/>
    <rect x="9" y="2" width="38" height="33" rx="10" fill="url(#vg-trk)" stroke="#fff" stroke-width="3"/>
    <path d="M14 12 Q28 5 42 12 L40 23 Q28 17 16 23 Z" fill="#aac9e9"/>
    <rect x="16" y="26" width="24" height="7" rx="3.5" fill="#2c343e"/>
    <rect x="8" y="40" width="40" height="73" rx="7" fill="url(#vg-box)" stroke="#14181d" stroke-width="3"/>
    <line x1="12" y1="58" x2="44" y2="58" stroke="#c6ccd3" stroke-width="2.5"/>
    <line x1="12" y1="76" x2="44" y2="76" stroke="#c6ccd3" stroke-width="2.5"/>
    <line x1="12" y1="94" x2="44" y2="94" stroke="#c6ccd3" stroke-width="2.5"/>
    <line x1="28" y1="99" x2="28" y2="110" stroke="#c6ccd3" stroke-width="2"/>
  </svg>` },
  // public-domain top-view sport bike artwork (OpenClipart), served as an asset
  bike: { w: 30, h: 66, url: "/static/img/bike-top.svg" },
};

function spriteHTML(sprite) {
  return sprite.url
    ? `<img src="${sprite.url}" style="width:100%;height:100%">`
    : sprite.svg;
}

// ---- rotatable fleet icons (admin map) ----
// The admin map uses classic Markers (no mapId, so no AdvancedMarker), whose
// image icons can't be CSS-rotated — instead the sprite is baked into a
// square data-URI SVG rotated to the travel bearing, cached per 10°.
const _fleetIconCache = {};
let _bikeInline = null;
// prefetch the bike artwork: a data-URI SVG rendered as an image may not load
// external resources, so the asset must be inlined to rotate
if (typeof fetch === "function") {
  fetch(VEHICLE_SPRITES.bike.url)
    .then(r => r.text())
    .then(t => { _bikeInline = t.replace(/^<svg[^>]*>/, "").replace(/<\/svg>\s*$/, ""); })
    .catch(() => {});
}

function _spriteNatural(sprite) {
  if (sprite.url) return { W: 287, H: 633, vb: "35 72 287 633", inner: _bikeInline };
  const m = sprite.svg.match(/viewBox="0 0 (\d+) (\d+)"/);
  return {
    W: +m[1], H: +m[2], vb: `0 0 ${m[1]} ${m[2]}`,
    inner: sprite.svg.replace(/^<svg[^>]*>/, "").replace(/<\/svg>\s*$/, ""),
  };
}

function _roundBearing(bearing) {
  return ((Math.round(bearing / 10) * 10) % 360 + 360) % 360;
}

function _fleetIcon(type, bearing) {
  const sprite = VEHICLE_SPRITES[type] || VEHICLE_SPRITES.car;
  const b = _roundBearing(bearing);
  const key = `${type}:${b}`;
  if (_fleetIconCache[key]) return _fleetIconCache[key];

  const { W, H, vb, inner } = _spriteNatural(sprite);
  if (!inner) {  // bike artwork not fetched yet: north-up fallback, uncached
    const w = Math.round(sprite.w * 0.8), h = Math.round(sprite.h * 0.8);
    return {
      url: sprite.url,
      scaledSize: new google.maps.Size(w, h),
      anchor: new google.maps.Point(w / 2, h / 2),
    };
  }
  // square canvas of the sprite's diagonal so no corner clips at any angle
  const D = Math.ceil(Math.hypot(W, H));
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${D} ${D}">` +
    `<g transform="rotate(${b} ${D / 2} ${D / 2})">` +
    `<svg x="${(D - W) / 2}" y="${(D - H) / 2}" width="${W}" height="${H}" viewBox="${vb}">${inner}</svg>` +
    `</g></svg>`;
  const side = Math.round(Math.hypot(sprite.w, sprite.h) * 0.8);
  const icon = {
    url: "data:image/svg+xml;charset=UTF-8," + encodeURIComponent(svg),
    scaledSize: new google.maps.Size(side, side),
    anchor: new google.maps.Point(side / 2, side / 2),
  };
  _fleetIconCache[key] = icon;
  return icon;
}

// Vehicle-type marker for the admin fleet map, facing `bearing` (0 = north).
function fleetVehicleMarker(map, position, type, bearing = 0) {
  const m = new google.maps.Marker({
    map, position, zIndex: 999,
    icon: _fleetIcon(type, bearing),
  });
  m._brg = _roundBearing(bearing);
  return m;
}

// Point an existing fleet marker toward its travel bearing.
function setFleetVehicleBearing(marker, type, bearing) {
  const b = _roundBearing(bearing);
  if (marker._brg === b) return;
  marker._brg = b;
  marker.setIcon(_fleetIcon(type, b));
}

function drawRoute(map, path) {
  return new google.maps.Polyline({
    map, path,
    strokeColor: routeColor(),
    strokeOpacity: 0.95,
    strokeWeight: 7,
  });
}

// congestion overlays from route.traffic ([startIdx, endIdx, speed] over path)
const TRAFFIC_COLORS = { SLOW: "#e8890c", TRAFFIC_JAM: "#d02f1f" };

function drawTraffic(map, path, traffic) {
  return (traffic || []).map(([s, e, speed]) => new google.maps.Polyline({
    map,
    path: path.slice(s, e + 1),
    strokeColor: TRAFFIC_COLORS[speed] || TRAFFIC_COLORS.SLOW,
    strokeOpacity: 1,
    strokeWeight: 5,
    zIndex: 1,
  }));
}

// keeps a map and its route polyline in sync with the active theme
function themeAwareMap(map, getRouteLine) {
  onThemeChange(() => {
    map.setOptions({ styles: mapThemeStyles() });
    google.maps.event.trigger(map, "resize"); // force stale tiles to repaint
    const line = getRouteLine && getRouteLine();
    if (line) line.setOptions({ strokeColor: routeColor() });
  });
}

function fitToPath(map, path) {
  const bounds = new google.maps.LatLngBounds();
  path.forEach(p => bounds.extend(p));
  map.fitBounds(bounds, 60);
}
