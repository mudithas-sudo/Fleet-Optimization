# Delivery management UI: what the market does, and what we changed

*Researched October 2026 from public product documentation, help centres and vendor blogs.
Most sources are vendors describing their own products, so read the claims as "what these
platforms chose to build", not as independent usability research. Where evidence was thin it
says so.*

## Platforms looked at

| Platform | What the public material shows |
|---|---|
| **Onfleet** | Dispatch dashboard with a sidebar, a map and a sortable **table view**. Drag a task from *Unassigned* onto a driver (end of route, or into an exact position); the sidebar order matches the driver's app. Dragging on the map inserts after a task. A **Command Center** counts failed and delayed tasks for the day. *Delayed* = past the "complete before" time **or** the ETA lands after it. One-click sort by status surfaces delayed or failed tasks. Single-key shortcuts exist, scoped to the dashboard. |
| **Bringg** | A **Planning** area for unassigned orders, route KPIs and a **route planner timeline** comparing planned and actual per day. Orders can be postponed to a later route. Bringg itself steers users to planning rather than a thinner dispatch page. |
| **Circuit for Teams** | Web dispatcher plus driver app. Help articles centre on managing **unassigned stops**, changing a **live route**, and moving stops between routes. |
| **Track-POD, Routific, OptimoRoute** | Compared mainly on routing quality, price and **electronic proof of delivery** (custom ePOD templates, barcode scanning). OptimoRoute is noted for easy setup and administration. |
| **DispatchTrack, Fleetrabbit, Dispatch Science** | "One consolidated view" instead of hunting across screens; a **dedicated exceptions tab** with automated alerts; **manage by exception**; predict window breaches and rebalance in a few clicks; chat with drivers and customers. |
| **Driver-app write-ups** (Sherpa, DispatchTrack, others) | Used one-handed, in motion, in sunlight: **very large tap targets**, the **next stop and one clear action dominate** the screen, swipe-to-confirm for completing, proof-of-delivery forms that **only show relevant fields**, capture photo and signature together, work offline. |
| **Tracking pages** (Nash, bettermile, Locate2u, pattern guides) | **Clock-time ETA with a window**, SMS or push over email, a map-plus-ETA card, graceful fallback when location data is missing or stale, and one status source feeding both the page and the notifications. |

## What they have in common

1. **A queue of unassigned work beside the live board**, with drag to assign.
2. **Manage by exception.** Problems are surfaced and counted; healthy work stays quiet.
3. **A sortable table next to the map** for high volume, with one-click sort by status.
4. **A consistent definition of "delayed"** (past deadline, or planned to arrive after it).
5. **Planned versus actual on a timeline**, per driver.
6. **Dense, glanceable operator screens** (one design page argues operators who use the tool all
   day should get density and status at a glance, not whitespace).
7. **Driver app: the next action is the biggest thing on screen.**
8. **Customer tracking: a time window, not a single minute, and a fallback state.**

Thin evidence: nothing useful on **command palettes** specifically (only that Onfleet scopes
shortcuts to its dashboard), and nothing on FarEye's dispatch screens.

## Where FleetOps already was, and what we changed

| Pattern | Before | Now |
|---|---|---|
| Unassigned queue + drag to assign | Pending parcels lived on the Parcels page; drag worked between pages | **Dashboard dock** lists unassigned parcels (express first, then soonest) and you **drag them onto a ride in the table** |
| Manage by exception | Attention page and bell | **"Needs attention" sits in the dock** next to the table, plus **delayed / failed chips** above it and a **per-ride overdue / at-risk badge** |
| Delayed definition | Late flag only | Overdue **or** planned arrival after the deadline (Onfleet's definition), per ride |
| Sortable table | Fixed order | **Sort any column** (ride, status, parcels, next stop); within a state, rides in trouble come first |
| Capacity awareness | Hidden | **Load meter per ride** (fullest of count, weight, volume), turns red above 90% |
| Planned vs actual timeline | Day timeline per ride, time travel | Unchanged (already matched Bringg's idea) |
| Dense operator view | One density | **Compact view** toggle, remembered per browser |
| Search-first navigation | Search box only | **Command palette (Ctrl/Cmd+K)** for parcels, rides, drivers and commands, plus **single-key shortcuts** (`/` search, `n` new parcel, `l` live, `g` then `d p r a f` to go to a page), ignored while typing |
| Parcel list order | Deadline only | **Sort**: soonest deadline, priority first, newest, receiver A–Z |
| First-run guidance | Empty screens | **"Get started" checklist** on an empty system |
| Driver app: next action | Flat list of stops | The **next stop is highlighted** with larger buttons and one-tap **Call** and **Navigate** |
| Tracking page | "Arriving around 11:13" | **"Arriving 10:58–11:28"** with a best estimate, an updated-at time, and a fallback line when no ETA exists |

## Deliberately not built (and why)

- **Insert a parcel at an exact position in a route, or drag on the map.** Onfleet does this; our
  re-plan re-optimises the whole route, so a fixed insertion point would be misleading.
- **Swipe-to-confirm and offline mode in the driver app.** Valuable, but they need device testing
  that a simulated drive can't give.
- **Chat with drivers and customers, and predictive window-breach alerts.** Backend scope, not UI.
- **Role-specific dashboards.** Roles already hide controls; separate layouts weren't justified by
  the evidence found.

## Limits of this work

- Sources are mostly vendor-authored. I found no independent usability study to quantify any of
  these patterns.
- I could not try the competing products hands-on; findings come from their documentation.
- The new screens were checked in a browser with a placeholder Maps key, so marker and route
  drawing on a real map still need a look on your side.

## Sources

- Onfleet: [task assignment](https://support.onfleet.com/hc/en-us/articles/360023910111-Task-Assignment),
  [table view (blog)](https://onfleet.com/blog/the-new-onfleet-table-view/),
  [table view (help)](https://support.onfleet.com/hc/en-us/articles/360027522312-Table-view),
  [Command Center](https://support.onfleet.com/hc/en-us/articles/35183133144980-Command-Center),
  [task status](https://support.onfleet.com/hc/en-us/articles/20509786766228-Task-Status),
  [shortcuts](https://support.onfleet.com/hc/en-us/articles/360023669812),
  [product update](https://onfleet.com/blog/onfleet-product-update-q3-2026/)
- Bringg: [plan and dispatch routes](https://help.bringg.com/docs/plan-and-dispatch-routes)
- Circuit: [Circuit for Teams help](https://help.getcircuit.com/en/collections/1889210-circuit-for-teams)
- Comparisons: [Track-POD vs OptimoRoute](https://track-pod.com/blog/optimoroute-alternative-trackpod/),
  [Track-POD vs Routific](https://www.track-pod.com/blog/routific-alternative-trackpod)
- Exceptions and dashboards: [DispatchTrack](https://www.dispatchtrack.com/blog/intelligent-visibility-platform/),
  [Fleetrabbit](https://fleetrabbit.com/industry/e-commerce-logistics/dispatcher-dashboard-software-delivery-operations-guide),
  [Dispatch Science review](https://www.rfp.wiki/vendors/dispatch-science)
- Driver apps: [Sherpa proof-of-delivery UX](https://blog.sherpa.net.au/driver-app-feature-release-new-and-improved-pod-ux/),
  [DispatchTrack mobile UX](https://www.dispatchtrack.com/blog/mobile-app-ui-ux/),
  [driver route app template](https://vp0.com/blogs/delivery-driver-route-app-ui-template)
- Tracking pages: [Nash tracking page](https://help.usenash.com/articles/7380081859),
  [order tracking pattern](https://ecommerceguide.com/patterns/track-order/),
  [common tracking mistakes](https://www.locate2u.com/gps-tracking/real-time-tracking-10-common-mistakes-that-drive-customers-crazy)
- Operator UI density and role views: [logistics dashboard UX](https://www.aufaitux.com/blog/dashboard-design-logistics-supply-chain-ux/),
  [logistics UX/UI principles](https://www.lazarev.agency/articles/logistics-ux-ui-design)
