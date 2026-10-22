// Command palette (Ctrl/Cmd+K), single-key shortcuts and the compact-view toggle.
// Search-first navigation: find a parcel, ride or driver by typing, or run a
// command without hunting through the menus. Shortcuts are scoped to the
// console and ignored while you are typing or a dialog is open.

const PAL = { items: [], idx: 0, goUntil: 0 };

function palCommands() {
  const dispatcher = roleAtLeast("dispatcher"), supervisor = roleAtLeast("supervisor");
  const goto = (label, page, key) => ({ group: "Go to", label, key: key ? `g ${key}` : "", run: () => switchTab(page) });
  const list = [
    goto("Dashboard", "dashboard", "d"), goto("Parcels", "parcels", "p"), goto("Runs", "fleet", "r"),
    goto("Attention queue", "attention", "a"), goto("Fleet and drivers", "registry", "f"),
    { group: "Do", label: "Show rides live", key: "l", run: () => { switchTab("dashboard"); goLive(); } },
  ];
  if (supervisor) list.splice(5, 0, { group: "Go to", label: "Impact: savings, fuel and CO2", key: "", run: () => $("btn-impact").click() });
  if (supervisor) list.splice(6, 0, { group: "Go to", label: "Reports", key: "", run: () => $("btn-reports").click() });
  if (dispatcher) {
    list.push(
      { group: "Do", label: "Add a parcel", key: "n", run: () => { switchTab("parcels"); toggleParcelForm(true); $("pf-name").focus(); } },
      { group: "Do", label: "Add a pickup request", key: "", run: () => { switchTab("parcels"); toggleParcelForm(true); setPfKind("pickup"); $("pf-name").focus(); } },
      { group: "Do", label: "Suggest runs for pending parcels", key: "", run: () => { switchTab("parcels"); autoAssign("rules"); } },
      { group: "Do", label: "Import a manifest (CSV or Excel)", key: "", run: () => { switchTab("parcels"); $("btn-import").click(); } },
    );
  }
  if (supervisor) list.push({ group: "Do", label: "Change the hub (start point)", key: "", run: () => { switchTab("parcels"); if (!depotEditing) startDepotEdit(); } });
  list.push(
    { group: "View", label: "Switch light / dark theme", key: "", run: () => setTheme(currentTheme() === "dark" ? "light" : "dark") },
    { group: "View", label: "Switch compact / comfortable view", key: "", run: () => setDensity(!document.body.classList.contains("compact")) },
  );
  if (typeof authEnabled !== "undefined" && authEnabled) list.push({ group: "Account", label: "Sign out", key: "", run: () => $("btn-logout").click() });
  return list;
}

const palMatch = (q, hay) => q.toLowerCase().split(/\s+/).filter(Boolean).every(w => hay.toLowerCase().includes(w));

function palBuild(q) {
  const items = palCommands().filter(c => !q || palMatch(q, c.label + " " + c.group));
  if (q) {
    Object.values(parcels).filter(p => palMatch(q, [p.trackingNumber, p.name, p.receiverName, p.destination.label, p.recipientPhone].filter(Boolean).join(" ")))
      .slice(0, 6).forEach(p => items.push({
        group: "Parcels", label: `${p.trackingNumber || p.name}  ${p.name}`, sub: `${p.receiverName || ""}${p.receiverName ? " · " : ""}${p.destination.label} · ${p.status.replace(/_/g, " ")}`,
        run: () => openParcelDetail(p.id),
      }));
    const rides = (dash.data ? dash.data.rides : Object.values(trips));
    rides.filter(r => palMatch(q, `${r.driverName || ""} ${r.label || ""} ${r.id}`)).slice(0, 4).forEach(r => items.push({
      group: "Rides", label: `${r.driverName || "No driver"}  ${r.label || r.id}`, sub: r.id,
      run: () => { switchTab("dashboard"); dashSelect(r.id); },
    }));
    driversReg.filter(d => palMatch(q, d.name)).slice(0, 3).forEach(d => items.push({
      group: "Drivers", label: d.name, sub: d.phone || "", run: () => switchTab("registry"),
    }));
  }
  return items;
}

function palRender() {
  const q = $("pal-input").value.trim();
  PAL.items = palBuild(q);
  PAL.idx = Math.min(PAL.idx, Math.max(0, PAL.items.length - 1));
  const list = $("pal-list");
  if (!PAL.items.length) { list.innerHTML = `<li class="pal-empty" role="presentation">Nothing matches “${pdEsc(q)}”. Try a tracking number, a receiver or a driver.</li>`; return; }
  let group = "", html = "";
  PAL.items.forEach((it, i) => {
    if (it.group !== group) { group = it.group; html += `<li class="pal-group" role="presentation">${pdEsc(group)}</li>`; }
    html += `<li role="option" id="pal-${i}" data-i="${i}" aria-selected="${i === PAL.idx}"><span>${pdEsc(it.label)}${it.sub ? `<br><small>${pdEsc(it.sub)}</small>` : ""}</span>${it.key ? `<span class="k"><kbd>${pdEsc(it.key)}</kbd></span>` : ""}</li>`;
  });
  list.innerHTML = html;
  list.querySelectorAll("li[role=option]").forEach(li => {
    li.onclick = () => palRun(Number(li.dataset.i));
    li.onmousemove = () => { if (PAL.idx !== Number(li.dataset.i)) { PAL.idx = Number(li.dataset.i); palSelect(); } };
  });
  $("pal-input").setAttribute("aria-activedescendant", `pal-${PAL.idx}`);
}

function palSelect() {
  $("pal-list").querySelectorAll("li[role=option]").forEach(li => li.setAttribute("aria-selected", String(Number(li.dataset.i) === PAL.idx)));
  const cur = $(`pal-${PAL.idx}`);
  if (cur) { cur.scrollIntoView({ block: "nearest" }); $("pal-input").setAttribute("aria-activedescendant", cur.id); }
}

function palOpen() {
  PAL.idx = 0;
  $("palette").hidden = false;
  $("pal-input").value = "";
  palRender();
  $("pal-input").focus();
}

function palClose() { $("palette").hidden = true; }

function palRun(i) {
  const it = PAL.items[i];
  if (!it) return;
  palClose();
  setTimeout(it.run, 0);
}

// a dialog or field is taking keyboard input: don't treat letters as shortcuts
function typing(e) {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return true;
  return ["import-modal", "reports", "pw-modal"].some(id => { const m = $(id); return m && m.style.display === "flex"; });
}

function setupPalette() {
  setupMenus();
  $("palette").addEventListener("mousedown", e => { if (e.target === $("palette")) palClose(); });
  $("pal-input").addEventListener("input", () => { PAL.idx = 0; palRender(); });
  $("pal-input").addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); PAL.idx = Math.min(PAL.items.length - 1, PAL.idx + 1); palSelect(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); PAL.idx = Math.max(0, PAL.idx - 1); palSelect(); }
    else if (e.key === "Enter") { e.preventDefault(); palRun(PAL.idx); }
    else if (e.key === "Escape") { e.preventDefault(); palClose(); }
  });

  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("palette").hidden ? palOpen() : palClose(); return; }
    if (!$("palette").hidden || typing(e) || e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key;
    if (Date.now() < PAL.goUntil) {                      // second key of "g then …"
      PAL.goUntil = 0;
      const page = { d: "dashboard", p: "parcels", r: "fleet", a: "attention", f: "registry" }[k.toLowerCase()];
      if (page) { e.preventDefault(); switchTab(page); }
      return;
    }
    if (k === "g") { PAL.goUntil = Date.now() + 1500; return; }
    if (k === "/") { e.preventDefault(); palOpen(); }
    else if (k === "?") { e.preventDefault(); palOpen(); }
    else if (k === "n" && roleAtLeast("dispatcher")) { e.preventDefault(); switchTab("parcels"); toggleParcelForm(true); $("pf-name").focus(); }
    else if (k === "l") { switchTab("dashboard"); goLive(); }
  });

  let compact = false;
  try { compact = localStorage.getItem("fleetops-density") === "compact"; } catch (_) {}
  setDensity(compact);
}

// compact view, remembered per browser; the Display menu mirrors it
function setDensity(on) {
  document.body.classList.toggle("compact", on);
  document.querySelectorAll("[data-density]").forEach(b => b.setAttribute("aria-pressed", String((b.dataset.density === "compact") === on)));
  try { localStorage.setItem("fleetops-density", on ? "compact" : "comfortable"); } catch (_) {}
}

// sidebar: collapsible on desktop (remembered), a slide-in drawer on phones
function setupNav() {
  const body = document.body, btn = $("nav-collapse"), burger = $("nav-burger");
  const setCollapsed = on => {
    body.classList.toggle("nav-collapsed", on);
    btn.setAttribute("aria-expanded", String(!on));
    const label = on ? "Expand sidebar" : "Collapse sidebar";
    btn.setAttribute("aria-label", label); btn.title = label;
    try { localStorage.setItem("fleetops-nav", on ? "collapsed" : "open"); } catch (_) {}
  };
  let saved = null;
  try { saved = localStorage.getItem("fleetops-nav"); } catch (_) {}
  if (saved === "collapsed") setCollapsed(true);
  btn.onclick = () => setCollapsed(!body.classList.contains("nav-collapsed"));
  const setDrawer = on => {
    body.classList.toggle("nav-open", on);
    burger.setAttribute("aria-expanded", String(on));
    burger.setAttribute("aria-label", on ? "Close menu" : "Open menu");
  };
  burger.onclick = () => setDrawer(!body.classList.contains("nav-open"));
  $("nav-scrim").onclick = () => setDrawer(false);
  $("sidebar").addEventListener("click", e => { if (e.target.closest(".nav-item")) setDrawer(false); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && body.classList.contains("nav-open")) { setDrawer(false); burger.focus(); } });
}

// the two top-bar menus: Display (theme, density) and Account
function setupMenus() {
  setupNav();
  const pairs = [["display-btn", "display-menu"], ["user-btn", "user-menu"]];
  const closeAll = () => pairs.forEach(([b, m]) => { $(m).hidden = true; $(b).setAttribute("aria-expanded", "false"); });
  pairs.forEach(([b, m]) => {
    $(b).onclick = e => {
      e.stopPropagation();
      const open = $(m).hidden;
      closeAll();
      $(m).hidden = !open;
      $(b).setAttribute("aria-expanded", String(open));
      if (open) { const first = $(m).querySelector("button"); if (first) first.focus(); }
    };
  });
  document.addEventListener("click", e => { if (!e.target.closest(".menu-wrap")) closeAll(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeAll(); });

  const syncTheme = t => document.querySelectorAll("[data-theme-set]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.themeSet === t)));
  document.querySelectorAll("[data-theme-set]").forEach(b => { b.onclick = () => setTheme(b.dataset.themeSet); });
  document.querySelectorAll("[data-density]").forEach(b => { b.onclick = () => setDensity(b.dataset.density === "compact"); });
  onThemeChange(syncTheme);
  syncTheme(currentTheme());
}
