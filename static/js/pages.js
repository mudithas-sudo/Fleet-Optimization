// Page chrome shared by the list pages: closing floating panels when the page changes, the status chips on
// Parcels, and the Vehicles / Drivers / Team tabs on Fleet and drivers.

// a panel (parcel detail, run detail, suggested runs, driver picker) belongs to the page it was opened on
function closeFloatingPanels() {
  if (typeof closeParcelDetail === "function") closeParcelDetail();
  ["dispatch-card", "run-picker"].forEach(id => { const el = $(id); if (el) el.style.display = "none"; });
  if (selectedId && typeof deselectTrip === "function") deselectTrip();
  else if ($("trip-detail")) $("trip-detail").style.display = "none";
}

const PARCEL_CHIPS = [
  ["all", "All", () => true],
  ["pending", "Pending", p => isPending(p)],
  ["out", "Out for delivery", p => isOut(p)],
  ["late", "Late", p => isLate(p)],
  ["delivered", "Delivered", p => p.status === "delivered"],
  ["returned", "Returned", p => p.status === "returned"],
];

function renderParcelChips(everything) {
  const box = $("flt-chips");
  if (!box) return;
  const active = FILTERS.status || "all";
  box.innerHTML = PARCEL_CHIPS.map(([key, label, test]) => {
    const n = everything.filter(test).length;
    return `<button type="button" data-chip="${key}" aria-pressed="${key === active}" class="${key === "late" && n ? "warn" : ""}">${label} <span class="n">${n}</span></button>`;
  }).join("");
  const count = $("parcel-count");
  if (count) count.textContent = everything.length ? `${everything.length} parcels` : "";
}

function setupPages() {
  const chips = $("flt-chips");
  if (chips) chips.addEventListener("click", e => {
    const b = e.target.closest("[data-chip]");
    if (!b) return;
    const key = b.dataset.chip;
    if (key === "all") { clearFilters(); return; }
    if (FILTERS.status === key) { clearFilters(); return; }
    FILTERS.status = null;                  // setStatusFilter toggles, so start from none
    setStatusFilter(key);
  });
  const tabs = $("reg-tabs");
  if (tabs) tabs.addEventListener("click", e => {
    const b = e.target.closest("[data-reg]");
    if (b) showRegistryTab(Number(b.dataset.reg));
  });
  document.addEventListener("click", e => {          // the "more" menu closes after a choice or a click elsewhere
    document.querySelectorAll("details.pg-more[open]").forEach(d => { if (!d.contains(e.target) || e.target.closest(".pg-menu button")) d.removeAttribute("open"); });
  });
}

let regTab = 0;
function showRegistryTab(i) {
  if (typeof i === "number") regTab = i;
  const team = $("reg-tab-team");
  if (team) team.hidden = !can("users");
  if (regTab === 2 && !can("users")) regTab = 0;
  const secs = [...document.querySelectorAll("#tab-registry > section.acc")];
  secs.forEach((s, n) => { s.classList.toggle("reg-hidden", n !== regTab); s.classList.toggle("open", n === regTab); });
  document.querySelectorAll("#reg-tabs [data-reg]").forEach(b => b.setAttribute("aria-selected", String(Number(b.dataset.reg) === regTab)));
  const v = $("veh-count"), d = $("drv-count");
  if (v && $("reg-n-veh")) $("reg-n-veh").textContent = v.textContent;
  if (d && $("reg-n-drv")) $("reg-n-drv").textContent = d.textContent;
}
