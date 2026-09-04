// Shared helpers for admin + driver pages.

// ---- theme: light by default, dark optional, persisted per browser ----
const THEME_KEY = "fleetops-theme";
const _themeListeners = [];

function currentTheme() {
  try { return localStorage.getItem(THEME_KEY) || "light"; } catch (_) { return "light"; }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = document.getElementById("theme-toggle");
  if (btn) btn.textContent = theme === "dark" ? "☀ Light" : "☾ Dark";
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
// traffic overlays and alert colors stay unambiguous
function routeColor() { return currentTheme() === "dark" ? "#5b93ff" : "#276ef1"; }

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
function stopMarker(map, position, index, total) {
  const isEnd = index === total - 1;
  const fill = index === 0 ? "#05944f" : isEnd ? "#e11900" : "#131619";
  const label = index === 0 ? "S" : isEnd ? "E" : String(index);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="34" height="44" viewBox="0 0 34 44">
    <path d="M17 0C7.6 0 0 7.6 0 17c0 12.8 17 27 17 27s17-14.2 17-27C34 7.6 26.4 0 17 0z" fill="${fill}"/>
    <circle cx="17" cy="16" r="10" fill="#ffffff"/>
    <text x="17" y="21" font-family="Manrope,Arial,sans-serif" font-size="13" font-weight="800" fill="${fill}" text-anchor="middle">${label}</text>
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
    <circle cx="14" cy="14" r="13" fill="#131619" opacity="0.2"/>
    <circle cx="14" cy="14" r="7.5" fill="#131619" stroke="#ffffff" stroke-width="2.5"/>
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
const TRAFFIC_COLORS = { SLOW: "#f6a609", TRAFFIC_JAM: "#e11900" };

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
