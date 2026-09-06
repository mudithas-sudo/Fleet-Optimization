# Design System Master File

> **LOGIC:** When building a specific page, first check `design-system/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the rules below.

---

**Project:** FleetOps
**Generated:** 2026-09-04 15:41:52
**Category:** Logistics/Delivery
**Design Dials:** Density 8/10 (Dense / Dashboard)

---

## Global Rules

### Color Palette

> The generator's raw palette put blue text on a blue background and white on
> `#EA580C` — both below WCAG 4.5:1. The **shipped** tokens below keep the
> tracking-blue + delivery-orange direction but pass AA in both themes. They
> live inline in `static/admin.html` / `static/driver.html` (`:root` +
> `[data-theme="dark"]`), with legacy `--blue/--red/...` aliases for the
> helper scripts. Verified pairs (light): CTA text 5.1, muted/surface 6.0,
> subtle/surface 5.4, primary/surface 5.2, danger/danger-soft 4.8.

**Light — semantic tokens**

| Role | Token | Hex |
|------|-------|-----|
| Background | `--bg` | `#f1f4f9` |
| Surface / Surface-2 / Surface-3 | `--surface` … | `#ffffff` / `#eef2f7` / `#e4eaf2` |
| Border / Border-strong | `--border` … | `#d5dde8` / `#b7c3d4` |
| Text / Muted / Subtle | `--text` … | `#0f1b2d` / `#55647a` / `#5e6b80` |
| Primary (tracking blue) | `--primary` | `#2563eb` (on: `#ffffff`) |
| Accent / CTA (delivery orange) | `--accent` | `#ea580c` (on: `#201203` near-black) |
| Success / Warning / Danger | `--success` … | `#0f7a43` / `#8a5200` / `#c42a17` |

**Dark** — same roles, `--bg #0a0e16`, `--surface #121926`, `--text #e9eef6`,
`--primary #5b9bff`, `--accent #ff7f43` (on `#1c0f06`), subtle `#8091a9`.

**Spacing** `--space-1..6` = 4/8/12/16/24/32 (dense-dashboard).
**Radius** 6 / 10 / 14 / 999. **Motion** 180ms `cubic-bezier(.2,0,0,1)`,
`prefers-reduced-motion` honoured.

### Typography

- **Font (single family):** Plus Jakarta Sans, weights 400–800, loaded via
  `<link>` in both HTML shells. System-font fallback stack on `--font`.
- **Scale (admin):** `--fs-xs 11` / `sm 12.5` / `base 14` / `md 15` /
  `lg 18` / `xl 22` / `2xl 27`. Section labels are `--fs-xs` 800 uppercase.
- **Icons:** Lucide-style inline stroke SVGs only — **no emoji in chrome**.
  Defined in `static/js/common.js` (`ICONS`, `iconSvg()`, `vehIconSvg()`);
  `.i` sizing tokens (14 / 16 / 20). Vehicle glyphs: car / van / truck / bike.

### Spacing Variables

*Density: 8/10 — Dense / Dashboard*

| Token | Value | Usage |
|-------|-------|-------|
| `--space-xs` | `2px` / `0.125rem` | Tight gaps |
| `--space-sm` | `4px` / `0.25rem` | Icon gaps, inline spacing |
| `--space-md` | `8px` / `0.5rem` | Standard padding |
| `--space-lg` | `12px` / `0.75rem` | Section padding |
| `--space-xl` | `16px` / `1rem` | Large gaps |
| `--space-2xl` | `24px` / `1.5rem` | Section margins |
| `--space-3xl` | `32px` / `2rem` | Hero padding |

### Shadow Depths

| Level | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.05)` | Subtle lift |
| `--shadow-md` | `0 4px 6px rgba(0,0,0,0.1)` | Cards, buttons |
| `--shadow-lg` | `0 10px 15px rgba(0,0,0,0.1)` | Modals, dropdowns |
| `--shadow-xl` | `0 20px 25px rgba(0,0,0,0.15)` | Hero images, featured cards |

---

## Component Specs

### Buttons

```css
/* Primary Button */
.btn-primary {
  background: #EA580C;
  color: white;
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  transition: all 200ms ease;
  cursor: pointer;
}

.btn-primary:hover {
  opacity: 0.9;
  transform: translateY(-1px);
}

/* Secondary Button */
.btn-secondary {
  background: transparent;
  color: #2563EB;
  border: 2px solid #2563EB;
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  transition: all 200ms ease;
  cursor: pointer;
}
```

### Cards

```css
.card {
  background: #EFF6FF;
  border-radius: 12px;
  padding: 24px;
  box-shadow: var(--shadow-md);
  transition: all 200ms ease;
  cursor: pointer;
}

.card:hover {
  box-shadow: var(--shadow-lg);
  transform: translateY(-2px);
}
```

### Inputs

```css
.input {
  padding: 12px 16px;
  border: 1px solid #E2E8F0;
  border-radius: 8px;
  font-size: 16px;
  transition: border-color 200ms ease;
}

.input:focus {
  border-color: #2563EB;
  outline: none;
  box-shadow: 0 0 0 3px #2563EB20;
}
```

### Modals

```css
.modal-overlay {
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
}

.modal {
  background: white;
  border-radius: 16px;
  padding: 32px;
  box-shadow: var(--shadow-xl);
  max-width: 500px;
  width: 90%;
}
```

---

## Style Guidelines

**Style:** Minimalism & Swiss Style

**Keywords:** Clean, simple, spacious, functional, white space, high contrast, geometric, sans-serif, grid-based, essential

**Best For:** Enterprise apps, dashboards, documentation sites, SaaS platforms, professional tools

**Key Effects:** Subtle hover (200-250ms), smooth transitions, sharp shadows if any, clear type hierarchy, fast loading

### Page Pattern

**Pattern Name:** Real-Time / Operations Landing

- **Conversion Strategy:** Offer a demo or sandbox and show trust signals. Label telemetry as live only when backed by a current source, with update time and stale state. Provide pause/hide or update-frequency controls for tickers and previews, stop offscreen/hidden work, support keyboard controls, and render a static final snapshot under reduced motion.
- **CTA Placement:** Primary CTA in nav + After metrics
- **Section Order:** Hero (product + live preview or status) > Key metrics/indicators > How it works > CTA (Start trial / Contact)

---

## Anti-Patterns (Do NOT Use)

- ❌ Static tracking
- ❌ No map integration
- ❌ AI purple/pink gradients

### Additional Forbidden Patterns

- ❌ **Emojis as icons** — Use SVG icons (Heroicons, Lucide, Simple Icons)
- ❌ **Missing cursor:pointer** — All clickable elements must have cursor:pointer
- ❌ **Layout-shifting hovers** — Avoid scale transforms that shift layout
- ❌ **Low contrast text** — Maintain 4.5:1 minimum contrast ratio
- ❌ **Instant state changes** — Always use transitions (150-300ms)
- ❌ **Invisible focus states** — Focus states must be visible for a11y

---

## Pre-Delivery Checklist

Before delivering any UI code, verify:

- [ ] No emojis used as icons (use SVG instead)
- [ ] All icons from consistent icon set (Heroicons/Lucide)
- [ ] `cursor-pointer` on all clickable elements
- [ ] Hover states with smooth transitions (150-300ms)
- [ ] Light mode: text contrast 4.5:1 minimum
- [ ] Focus states visible for keyboard navigation
- [ ] `prefers-reduced-motion` respected
- [ ] Responsive: 375px, 768px, 1024px, 1440px
- [ ] No content hidden behind fixed navbars
- [ ] No horizontal scroll on mobile
