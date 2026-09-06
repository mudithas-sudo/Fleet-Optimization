# Page override — Admin dispatch console (`static/admin.html`)

Inherits MASTER. Surface-specific rules:

- **Layout:** fixed 58px header + `main` flex row — 420px `aside` (scrolls) and
  a flex-1 map with absolutely-positioned `.float-card` panels (trip detail,
  dispatch proposal, driver picker). Below 900px it stacks: aside on top
  (max 46vh), map below; float-cards go full-width.
- **Tabs:** ARIA `tablist` / `tab` / `tabpanel`; `switchTab()` sets
  `aria-selected` + roving `tabindex` and toggles panel `hidden`. Selected tab
  = `--surface` fill + `--shadow-sm` on the `--surface-2` track.
- **CTA:** primary buttons (`.btn-primary`, `.dispatch-btn`) use `--accent`
  with near-black text. The Dispatch pill is the one accent-coloured control in
  the header row — keep it the only one.
- **Status colour is never alone:** parcel `.p-dot`, trip `.dev-badge`,
  `.deadline` states and `.alert-row` all pair colour with an icon or text.
- **Empty states** (`.empty`): icon + `<b>` headline + one guiding sentence.
- **Alerts** (`#alerts`, `#alert-banner`) are `aria-live`; banner is `role=alert`.
- Emoji removed from `admin.js`: `VEH_ICON` → `vehIconSvg()`, all `✕/✓/⚠/🧑`
  → `iconSvg()`. `<select>` options stay text-only (no SVG in options).
