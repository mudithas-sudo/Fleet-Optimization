// Who is signed in, role-based hiding, password change, and (for administrators)
// the team list. The server enforces every permission; hiding controls here only
// keeps people from clicking things that would be refused.

let currentUser = null;
let authEnabled = false;
let ROLES_INFO = [];                 // from /api/auth/roles: label, help, landing, pages, permissions
let ROLE_ORDER = [];
const ROLE_HELP = {};
const VIEW_AS_KEY = "fleetops-viewas";

async function loadMe() {
  const me = await api("/api/auth/me");
  authEnabled = me.authEnabled;
  currentUser = me.user;
  try {
    ROLES_INFO = await api("/api/auth/roles");
    ROLE_ORDER = ROLES_INFO.map(r => r.id);
    ROLES_INFO.forEach(r => { ROLE_HELP[r.id] = r.help; });
  } catch (_) { ROLES_INFO = []; }
  // sign-in is off: let a presenter look at the console as any role (it only changes what is shown)
  if (!authEnabled && ROLES_INFO.length) {
    let saved = null;
    try { saved = localStorage.getItem(VIEW_AS_KEY); } catch (_) {}
    const r = ROLES_INFO.find(x => x.id === saved);
    if (r) currentUser = { ...currentUser, role: r.id, roleLabel: r.label, permissions: r.permissions, pages: r.pages, landing: r.landing };
    const sel = $("viewas");
    $("viewas-sec").hidden = false;
    sel.innerHTML = ROLES_INFO.map(x => `<option value="${x.id}">${pdEsc(x.label)}</option>`).join("");
    sel.value = currentUser.role;
    sel.onchange = () => {
      try { localStorage.setItem(VIEW_AS_KEY, sel.value); } catch (_) {}
      location.reload();
    };
  }
  $("menu-auth").hidden = !authEnabled;
  $("menu-note").hidden = authEnabled;
  $("user-name").textContent = $("menu-name").textContent = currentUser.name;
  $("user-role").textContent = $("menu-role").textContent = currentUser.roleLabel;
  const initials = currentUser.name.split(/\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase() || "–";
  $("top-avatar").textContent = initials;
  applyRole();
  if (authEnabled) {
    $("btn-logout").onclick = async () => {
      try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
      location.href = "/static/login.html";
    };
    $("btn-password").onclick = openPasswordDialog;
  }
}

// what this person may do (permissions) and see (pages): body classes drive the CSS, `can()` drives scripts
function can(perm) { return !!currentUser && currentUser.permissions.includes(perm); }

function applyRole() {
  // Impact and Reports follow their permissions, even if an older server's page list does not mention them
  currentUser.pages = [...new Set([...currentUser.pages, ...(can("impact") ? ["impact"] : []), ...(can("reports") ? ["reports"] : []), ...(can("cod.read") ? ["cash"] : [])])];
  const body = document.body;
  [...body.classList].filter(c => c.startsWith("role-") || c.startsWith("can-")).forEach(c => body.classList.remove(c));
  body.classList.add("role-" + currentUser.role);
  currentUser.permissions.forEach(p => body.classList.add("can-" + p.replace(/\./g, "-")));
  ["dashboard", "parcels", "fleet", "attention", "warehouse", "cash", "registry", "impact", "reports"].forEach(page => {
    const btn = $("tab-btn-" + page);
    if (btn) btn.hidden = !currentUser.pages.includes(page);
  });
}

// older scripts ask "is this at least a dispatcher / supervisor / admin?"
function roleAtLeast(role) {
  if (role === "admin") return can("users");
  if (role === "supervisor") return can("settings");
  if (role === "dispatcher") return can("runs.plan");
  return true;
}

// ---------------------------------------------------------------- password

function openPasswordDialog() {
  $("pw-modal").style.display = "flex";
  $("pw-current").value = ""; $("pw-new").value = "";
  $("pw-err").textContent = "";
  $("pw-current").focus();
}

function setupPassword() {
  $("pw-cancel").onclick = () => { $("pw-modal").style.display = "none"; };
  $("pw-save").onclick = async () => {
    $("pw-err").textContent = "";
    $("pw-save").disabled = true;
    try {
      await api("/api/auth/password", { method: "POST", body: JSON.stringify({
        current: $("pw-current").value, new: $("pw-new").value }) });
      $("pw-modal").style.display = "none";
      alert("Password changed. Other devices you were signed in on have been signed out.");
    } catch (err) { $("pw-err").textContent = err.message; }
    finally { $("pw-save").disabled = false; }
  };
}

// -------------------------------------------------------------------- team

async function loadUsers() {
  if (!authEnabled || !roleAtLeast("admin")) return;
  $("users-section").hidden = false;
  let users;
  try { users = await api("/api/users"); } catch (err) { $("user-list").textContent = err.message; return; }
  const wrap = $("user-list");
  wrap.innerHTML = "";
  users.forEach(u => {
    const me = u.id === currentUser.id;
    const row = document.createElement("div");
    row.className = "reg-row" + (u.active ? "" : " off");
    row.innerHTML = `
      <span class="veh-ico">${iconSvg("user")}</span>
      <div class="info">
        <div class="label">${pdEsc(u.name)}${me ? " (you)" : ""}${u.active ? "" : " · switched off"}</div>
        <div class="meta">${pdEsc(u.username)} · ${u.lastLoginAt ? "last in " + pdWhen(u.lastLoginAt) : "never signed in"}</div>
      </div>
      <select aria-label="Role for ${pdEsc(u.name)}" ${me ? "disabled" : ""}>${ROLE_ORDER.map(r =>
        `<option value="${r}" ${r === u.role ? "selected" : ""}>${pdEsc((ROLES_INFO.find(x => x.id === r) || {}).label || r)}</option>`).join("")}</select>
      <button class="link-action" type="button" data-act="reset">Reset password</button>
      ${me ? "" : `<button class="link-action" type="button" data-act="toggle">${u.active ? "Switch off" : "Switch on"}</button>
      <button class="del" type="button" aria-label="Delete ${pdEsc(u.name)}">${iconSvg("trash")}</button>`}`;
    const patch = async body => {
      try { await api(`/api/users/${u.id}`, { method: "PUT", body: JSON.stringify(body) }); await loadUsers(); }
      catch (err) { alert(err.message); await loadUsers(); }
    };
    row.querySelector("select").onchange = e => patch({ role: e.target.value });
    row.querySelector('[data-act="reset"]').onclick = () => {
      const pw = prompt(`New password for ${u.name} (at least 8 characters). They'll be signed out everywhere.`);
      if (pw) patch({ password: pw });
    };
    const toggle = row.querySelector('[data-act="toggle"]');
    if (toggle) toggle.onclick = () => patch({ active: !u.active });
    const del = row.querySelector(".del");
    if (del) del.onclick = async () => {
      if (!confirm(`Delete ${u.name}? They'll lose access immediately.`)) return;
      try { await api(`/api/users/${u.id}`, { method: "DELETE" }); await loadUsers(); } catch (err) { alert(err.message); }
    };
    wrap.append(row);
  });
}

function setupUsers() {
  $("role-help").textContent = ROLES_INFO.map(r => `${r.label}: ${r.help}`).join(" · ");
  $("uf-role").innerHTML = ROLES_INFO.map(r => `<option value="${r.id}" ${r.id === "dispatcher" ? "selected" : ""}>${pdEsc(r.label)}</option>`).join("");
  $("btn-add-user").onclick = () => { $("user-form").style.display = "flex"; $("uf-username").focus(); };
  $("uf-cancel").onclick = () => { $("user-form").style.display = "none"; };
  $("uf-save").onclick = async () => {
    $("uf-err").hidden = true;
    try {
      await api("/api/users", { method: "POST", body: JSON.stringify({
        username: $("uf-username").value, name: $("uf-name").value,
        role: $("uf-role").value, password: $("uf-password").value }) });
      ["uf-username", "uf-name", "uf-password"].forEach(id => { $(id).value = ""; });
      $("user-form").style.display = "none";
      await loadUsers();
    } catch (err) { $("uf-err").textContent = err.message; $("uf-err").hidden = false; }
  };
}
