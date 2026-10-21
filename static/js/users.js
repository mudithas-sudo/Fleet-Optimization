// Who is signed in, role-based hiding, password change, and (for administrators)
// the team list. The server enforces every permission; hiding controls here only
// keeps people from clicking things that would be refused.

let currentUser = null;
let authEnabled = false;
const ROLE_ORDER = ["viewer", "dispatcher", "supervisor", "admin"];
const ROLE_HELP = {
  viewer: "Can look, can’t change anything",
  dispatcher: "Plans and changes deliveries",
  supervisor: "Dispatcher + fleet, hub, reports",
  admin: "Everything, including people",
};

async function loadMe() {
  const me = await api("/api/auth/me");
  authEnabled = me.authEnabled;
  currentUser = me.user;
  document.body.classList.add("role-" + currentUser.role);
  $("menu-auth").hidden = !authEnabled;
  $("menu-note").hidden = authEnabled;
  $("user-name").textContent = $("menu-name").textContent = currentUser.name;
  $("user-role").textContent = $("menu-role").textContent = currentUser.roleLabel;
  const initials = currentUser.name.split(/\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase() || "–";
  $("top-avatar").textContent = initials;
  if (authEnabled) {
    $("btn-logout").onclick = async () => {
      try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
      location.href = "/static/login.html";
    };
    $("btn-password").onclick = openPasswordDialog;
  }
}

function roleAtLeast(role) { return ROLE_ORDER.indexOf(currentUser.role) >= ROLE_ORDER.indexOf(role); }

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
        `<option value="${r}" ${r === u.role ? "selected" : ""}>${r[0].toUpperCase() + r.slice(1)}</option>`).join("")}</select>
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
  $("role-help").textContent = ROLE_ORDER.map(r => `${r[0].toUpperCase() + r.slice(1)}: ${ROLE_HELP[r]}`).join(" · ");
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
