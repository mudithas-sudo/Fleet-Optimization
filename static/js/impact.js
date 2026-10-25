// Impact screen: what the optimisation is worth — savings against the as-entered order,
// fuel and CO2, planned vs actual, ETA accuracy, and the assumptions behind every figure.
// Reads GET /api/impact; fuel price and consumption are editable (PUT /api/settings/fuel).

let imDays = "7";

function setupImpact() {
  $("im-days").onchange = () => { imDays = $("im-days").value; loadImpact(); };
}

// Impact is a page (opened from the side menu); this runs when it is shown
function startImpact() {
  $("im-days").value = imDays;
  loadImpact();
}

const imN = (n, d = 0) => n == null ? "–" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d });

async function loadImpact() {
  const days = imDays;
  $("im-status").textContent = "Loading…";
  let r;
  try { r = await api(`/api/impact?days=${days}`); }
  catch (err) { $("im-status").textContent = "Couldn’t load the impact figures: " + err.message; return; }
  if (days !== imDays) return;
  $("im-status").textContent = "";
  const s = r.savings, f = r.footprint, cur = r.currency;
  const period = { today: "today", "7": "in the last 7 days", "30": "in the last 30 days", all: "so far" }[days] || "";

  const yearly = s.perYear.factor && days !== "all" ? Math.round(s.savedCost * s.perYear.factor) : null;
  $("im-hero").innerHTML = s.runs ? `
    <div class="im-lead">
      <span class="im-eyebrow">Saved ${period} on ${s.runs} run${s.runs === 1 ? "" : "s"}</span>
      <b>${imN(s.savedKm, 1)} km <small>${s.savedPct}% less driving</small></b>
      <p>Compared with the same parcels visited in the order they were entered. ${yearly
        ? `Repeated all year this is worth about <b>${cur} ${imN(yearly)}</b>.` : ""}</p>
    </div>
    <div class="im-stats">
      <div><b>${imN(s.savedHours, 1)} h</b><span>driver time</span></div>
      <div><b>${imN(s.savedLitres, 1)} L</b><span>fuel</span></div>
      <div><b>${imN(s.savedCo2Kg, 1)} kg</b><span>CO₂</span></div>
      <div><b>${cur} ${imN(s.savedCost)}</b><span>operating cost</span></div>
    </div>` : `<div class="hint">No runs in this period yet — plan a run to see the savings.</div>`;
  if (typeof renderScorecard === "function") renderScorecard(days);


}
