// Parcel bulk-import parsing: hand-rolled CSV (no deps), SheetJS lazy-loaded
// from cdnjs only when an Excel file is chosen.

// canonical field -> accepted header names (lower-case, trimmed)
const IMPORT_COLUMNS = {
  trackingNumber: ["tracking number", "tracking no", "tracking", "tracking id", "awb", "waybill", "consignment"],
  name: ["name", "parcel", "parcel name", "item", "description", "package"],
  receiverName: ["receiver", "receiver name", "recipient", "recipient name", "consignee", "customer"],
  recipientPhone: ["phone", "receiver phone", "recipient phone", "contact", "mobile", "telephone"],
  destination: ["destination", "address", "receiver address", "delivery address", "deliver to", "drop-off"],
  type: ["type", "category"],
  size: ["size", "package size"],
  weightKg: ["weight", "weight kg", "weight (kg)", "weightkg"],
  length: ["length", "length cm", "length (cm)", "l"],
  width: ["width", "width cm", "width (cm)", "w"],
  height: ["height", "height cm", "height (cm)", "h"],
  fragile: ["fragile"],
  notes: ["notes", "instructions", "delivery notes", "remarks"],
  deadline: ["deadline", "deliver by", "due", "due date"],
  priority: ["priority", "service", "service level"],
  windowStart: ["window start", "deliver after", "not before", "earliest"],
  receiverEmail: ["email", "receiver email", "recipient email"],
};

function parseCSV(text) {
  const rows = [];
  let row = [], field = "", inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') inQuotes = false;
      else field += c;
    } else if (c === '"') inQuotes = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some(f => f.trim() !== "")) rows.push(row);
      row = [];
    } else field += c;
  }
  row.push(field);
  if (row.some(f => f.trim() !== "")) rows.push(row);
  return rows;
}

function rowsToObjects(rows) {
  if (!rows.length) return [];
  const header = rows[0].map(h => String(h).trim().toLowerCase());
  const index = {};
  Object.entries(IMPORT_COLUMNS).forEach(([field, names]) => {
    index[field] = header.findIndex(h => names.includes(h));
  });
  return rows.slice(1).map(r => {
    const obj = {};
    Object.keys(IMPORT_COLUMNS).forEach(field => {
      const idx = index[field];
      obj[field] = idx >= 0 ? String(r[idx] ?? "").trim() : "";
    });
    return obj;
  });
}

let _sheetJsLoading = null;
function loadSheetJS() {
  if (window.XLSX) return Promise.resolve();
  if (!_sheetJsLoading) {
    _sheetJsLoading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js";
      s.onload = resolve;
      s.onerror = () => reject(new Error("Could not load the Excel parser"));
      document.head.append(s);
    });
  }
  return _sheetJsLoading;
}

// -> [{trackingNumber, name, receiverName, destination, size, weightKg, length, …}] (all strings, untrusted)
async function parseImportFile(file) {
  if (/\.(xlsx|xls)$/i.test(file.name)) {
    await loadSheetJS();
    const wb = XLSX.read(await file.arrayBuffer(), { type: "array" });
    const sheet = wb.Sheets[wb.SheetNames[0]];
    const rows = XLSX.utils.sheet_to_json(sheet, { header: 1, raw: false });
    return rowsToObjects(rows);
  }
  return rowsToObjects(parseCSV(await file.text()));
}

function parseDeadline(text) {
  if (!text) return null;
  const t = Date.parse(text.replace(" ", "T"));
  return Number.isNaN(t) ? null : t / 1000;
}

// small/medium/large from the longest edge (cm) when the sheet gives no size
function sizeFromDimensions(l, w, h) {
  const longest = Math.max(l, w, h);
  return longest <= 30 ? "small" : longest <= 60 ? "medium" : "large";
}

function parseFlag(text) { return /^(y|yes|true|1|fragile|x)$/i.test((text || "").trim()); }
