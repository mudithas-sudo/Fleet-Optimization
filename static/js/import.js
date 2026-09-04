// Parcel bulk-import parsing: hand-rolled CSV (no deps), SheetJS lazy-loaded
// from cdnjs only when an Excel file is chosen.

const IMPORT_COLUMNS = ["name", "type", "size", "destination", "deadline"];

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
  return rows.slice(1).map(r => {
    const obj = {};
    IMPORT_COLUMNS.forEach(col => {
      const idx = header.indexOf(col);
      obj[col] = idx >= 0 ? String(r[idx] ?? "").trim() : "";
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

// -> [{name, type, size, destination, deadline}] (all strings, untrusted)
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
