# Page override — Driver navigator PWA (`static/driver.html`)

Inherits MASTER. Surface-specific rules:

- **Full-bleed map** with floating chrome; every overlay respects
  `env(safe-area-inset-*)`. Do not change the `#map` oversize / CSS-rotation
  logic in `driver.js` — visual only.
- **Navigation banner** (`#banner`) is the one dark surface (`--nav-bg`);
  turns `--danger` when off-route. Maneuver arrows are white stroke SVGs
  generated in `driver.js` (`maneuverIcon()`), kept as-is.
- **Round controls:** speed bubble, deviate (`--danger` ring; `.on` = filled +
  pulse + `aria-pressed`), ETA bar buttons (`.round-btn`). All ≥44px targets.
- **Queue view** (`?driver=<id>`): `.q-run .go` is `--accent` (near-black text);
  `.go.live` is `--success`. Empty / error states use `.q-empty` with an icon.
- **Theme toggle** in both the sheet and the queue is an icon button painted by
  `setThemeToggle()` in `common.js` (sun/moon + `aria-pressed`).
- **PWA:** bump `static/sw.js` `CACHE` on any driver shell change (done: v5).
  `manifest.json` `theme_color` = `#2563eb`.
- Emoji removed from `driver.js`: `VEH_ICON` → `vehIconSvg()`, `☰/✕/▦/⚠`
  → inline SVG in the HTML or `iconSvg()`.
