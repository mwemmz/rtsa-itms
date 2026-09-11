"use strict";

/* ---------------- small helpers ---------------- */

function $(sel, root) { return (root || document).querySelector(sel); }
function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

function esc(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function money(n) {
  return Number(n || 0).toLocaleString("en-ZM") + " ZMW";
}

function dt(v) {
  if (!v) return "";
  var d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString("en-ZM", { dateStyle: "short", timeStyle: "short" });
}

function toast(msg, type) {
  var box = document.createElement("div");
  box.className = "toast " + (type || "");
  box.textContent = msg;
  $("#toast").appendChild(box);
  setTimeout(function () { box.remove(); }, 4500);
}

function formData(form) {
  var o = {};
  $$("input, select, textarea", form).forEach(function (f) {
    if (!f.name) return;
    if (f.type === "checkbox") o[f.name] = f.checked;
    else if (f.type === "datetime-local" && f.value) o[f.name] = new Date(f.value).toISOString();
    else if (f.value !== "") o[f.name] = f.value;
  });
  return o;
}

var VO_TYPES = [
  ["speeding", "Speeding"],
  ["running_red_light", "Running red light"],
  ["no_insurance", "No insurance"],
  ["expired_fitness", "Expired fitness"],
  ["no_psv_permit", "No PSV permit"],
  ["driving_without_licence", "Driving without licence"],
  ["illegal_parking", "Illegal parking"],
  ["overloading", "Overloading"],
  ["rear_seat_belt", "Rear seat belt"],
  ["using_phone", "Using phone"],
  ["blacklisted_vehicle", "Blacklisted vehicle"],
  ["other", "Other"]
];
var VEHICLE_STATUSES = ["active", "suspended", "deregistered", "stolen"];
var CHALLAN_STATUSES = ["unpaid", "paid", "overdue", "disputed"];
var DRIVER_STATUSES = ["active", "suspended", "disqualified", "expired"];
var LICENCE_CLASSES = ["A", "B", "C", "D", "E", "F"];

/* ---------------- API client ---------------- */

function getToken() { return localStorage.getItem("rtsa_token") || ""; }
function setToken(t) { t ? localStorage.setItem("rtsa_token", t) : localStorage.removeItem("rtsa_token"); }

async function api(path, opts) {
  opts = opts || {};
  opts.headers = opts.headers || {};
  if (opts.body && !(opts.body instanceof FormData) && !opts.headers["Content-Type"]) {
    opts.headers["Content-Type"] = "application/json";
  }
  var t = getToken();
  if (t) opts.headers["Authorization"] = "Bearer " + t;
  var res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error("Network error — is the server reachable?");
  }
  var ct = res.headers.get("content-type") || "";
  var data = ct.indexOf("application/json") !== -1 ? await res.json() : null;
  if (res.status === 401 && !/login/.test(path)) {
    logout();
    throw new Error("Session expired. Please sign in again.");
  }
  if (!res.ok) {
    var detail = data && (data.detail || data.message);
    if (Array.isArray(detail)) detail = detail.map(function (d) { return d.msg; }).join("; ");
    throw new Error(detail || ("HTTP " + res.status));
  }
  return data;
}

/* ---------------- auth / routing ---------------- */

var USER = null;
var NAV = {
  admin: [
    { id: "dashboard", label: "Dashboard" },
    { id: "vehicles", label: "Vehicles" },
    { id: "drivers", label: "Drivers" },
    { id: "users", label: "Users" },
    { id: "challans", label: "Challans" },
    { id: "audits", label: "Audit log" },
    { id: "rules", label: "Notification rules" },
    { id: "planner", label: "Route planner" }
  ],
  officer: [
    { id: "dashboard", label: "Dashboard" },
    { id: "violations", label: "Record violation" },
    { id: "challans", label: "Challans" },
    { id: "toll", label: "Toll gates" },
    { id: "planner", label: "Route planner" }
  ],
  citizen: [
    { id: "dashboard", label: "My dashboard" },
    { id: "fines", label: "Fines & payments" },
    { id: "licence", label: "My licence" },
    { id: "alerts", label: "Road alerts" },
    { id: "planner", label: "Route planner" }
  ]
};
var VIEW = { id: "dashboard", title: "Dashboard" };

function ensureViewId(id) {
  if (!USER) return "dashboard";
  var ids = NAV[USER.role] || [];
  if (ids.some(function (x) { return x.id === id; })) return id;
  return "dashboard";
}

function renderNav() {
  var nav = $("#nav");
  nav.innerHTML = "";
  var ids = NAV[USER.role] || [];
  ids.forEach(function (item) {
    var a = document.createElement("a");
    a.href = "#/" + item.id;
    a.textContent = item.label;
    if (item.id === VIEW.id) a.className = "active";
    nav.appendChild(a);
  });
  $("#user-chip").textContent = USER.full_name + " · " + USER.role;
  $("#sidebar-foot").textContent = "Signed in as " + USER.email;
}

function setTitle(t) {
  VIEW.title = t;
  $("#page-title").textContent = t;
  document.title = "RTSA ITMS — " + t;
}

async function loadRouter() {
  var id = ensureViewId((location.hash || "#/dashboard").replace(/^#\//, ""));
  location.hash = "#/" + id;
  VIEW.id = id;
  renderNav();
  refreshBell();
  try {
    await go(VIEW.id);
  } catch (e) {
    $("#content").innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

async function go(id) {
  id = ensureViewId(id);
  VIEW.id = id;
  renderNav();
  var t = (NAV[USER.role] || []).find ? (NAV[USER.role].find(function (x) { return x.id === id; }) || {}).label : id;
  setTitle(t || "Dashboard");
  var fn = VIEWS[id];
  if (!fn) { $("#content").innerHTML = '<div class="empty">Unknown view</div>'; return; }
  await fn();
}

/* ---------------- login / logout ---------------- */

async function doLogin(email, password, msgEl, btn) {
  msgEl.className = "login-msg";
  msgEl.textContent = "Signing in…";
  if (btn) { btn.disabled = true; }
  try {
    var data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: email, password: password })
    });
    setToken(data.access_token);
    await bootstrapApp();
  } catch (e) {
    msgEl.className = "login-msg err";
    msgEl.textContent = e.message;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function logout() {
  setToken(null);
  USER = null;
  $("#shell").classList.add("hidden");
  $("#login-screen").classList.remove("hidden");
  location.hash = "";
  refreshBell();
}

async function bootstrapApp() {
  USER = await api("/api/auth/me");
  $("#login-screen").classList.add("hidden");
  $("#shell").classList.remove("hidden");
  await loadRouter();
}

/* ---------------- notifications ---------------- */

async function refreshBell() {
  if (!getToken()) return;
  try {
    var data = await api("/api/notifications/unread-count");
    var b = $("#bell-badge");
    b.textContent = data.unread;
    b.classList.toggle("hidden", !data.unread);
  } catch (e) { /* ignore */ }
}

async function openNotifs() {
  var drawer = $("#notif-drawer");
  drawer.classList.remove("hidden");
  var list = $("#notif-list");
  list.innerHTML = '<div class="empty">Loading…</div>';
  try {
    var items = await api("/api/notifications/?unread_only=false");
    if (!items.length) { list.innerHTML = '<div class="empty">No notifications yet.</div>'; return; }
    list.innerHTML = items.map(function (n) {
      var read = n.read ? "gray" : "blue";
      return '<div class="item' + (n.read ? "" : " unread") + '" data-id="' + esc(n.id) + '">' +
        '<div class="n-title">' + esc(n.title) + '</div>' +
        '<div class="n-body small">' + esc(n.body) + '</div>' +
        '<div class="n-time"><span class="pill ' + read + '">' + (n.read ? "read" : "new") + "</span> " + dt(n.created_at) + "</div>" +
        "</div>";
    }).join("");
    $$("#notif-list .item").forEach(function (el) {
      el.addEventListener("click", async function () {
        try { await api("/api/notifications/" + el.dataset.id + "/read", { method: "POST" }); refreshBell(); openNotifs(); }
        catch (e) { toast(e.message, "err"); }
      });
    });
  } catch (e) {
    list.innerHTML = '<div class="empty">' + esc(e.message) + "</div>";
  }
}

/* ---------------- shared view chunks ---------------- */

function statusPill(status) {
  var cls = "gray";
  if (status === "active" || status === "paid" || status === "valid") cls = "green";
  else if (status === "suspended" || status === "expired" || status === "overdue" || status === "deregistered") cls = "red";
  else if (status === "unpaid") cls = "amber";
  return '<span class="pill ' + cls + '">' + esc(status) + "</span>";
}

var VIEWS = {};

VIEWS.dashboard = async function () {
  var out;
  if (USER.role === "admin") out = await adminDashboard();
  else if (USER.role === "officer") out = await officerDashboard();
  else out = await citizenDashboard();
  $("#content").innerHTML = out;
};

async function adminDashboard() {
  var s = await api("/api/admin/reports/summary");
  var vehicles = await api("/api/vehicles?limit=6");
  var challans = await api("/api/enforcement/challans?limit=6");
  return (
    '<div class="grid cards">' +
      kpi("Registered vehicles", s.vehicles) +
      kpi("Drivers", s.drivers) +
      kpi("Unpaid challans", s.challans_unpaid, "of " + s.challans_total + " total") +
      kpi("Revenue collected", money(s.revenue_collected)) +
    "</div>" +
    '<div class="row">' +
      cars("Recent vehicles", vehicleRows(vehicles)) +
      cars("Recent challans", challanRows(challans)) +
    "</div>"
  );
}

async function officerDashboard() {
  var violations = await api("/api/enforcement/violations?limit=6");
  var challans = await api("/api/enforcement/challans?limit=6");
  var unpaid = challans.filter(function (c) { return c.status !== "paid"; }).length;
  return (
    '<div class="grid cards">' +
      kpi("Active challans", unpaid) +
      kpi("Recent violations", violations.length) +
      kpi("Tools", "<div><a class='btn gold sm' href='#/violations'>Record violation</a> <a class='btn sm' href='#/toll'>Toll gate</a></div>") +
    "</div>" +
    '<div class="row">' +
      cars("Recent violations", violationRows(violations)) +
      cars("Recent challans", challanRows(challans)) +
    "</div>"
  );
}

async function citizenDashboard() {
  var d = await api("/api/portal/dashboard");
  var licence = d.licence;
  var licBlock = licence
    ? '<div><span class="pill ' + (licence.state === "expired" ? "red" : licence.state === "expiring_soon" ? "amber" : "green") + '">' + esc(licence.state) + '</span></div>' +
      '<table><tr><td>Licence no.</td><td class="mono">' + esc(licence.licence_number) + "</td></tr>" +
      '<tr><td>Class</td><td>' + esc(licence.licence_class) + '</td></tr>' +
      '<tr><td>Expires</td><td>' + dt(licence.expiry_date) + ' (' + Number(licence.days_until_expiry) + " days)</td></tr></table>"
    : '<div class="empty">No driver record linked to this account yet.</div>';
  return (
    '<div class="grid cards">' +
      kpi("Unread notifications", d.unread_notifications) +
      kpi("My vehicles", (d.vehicles || []).length) +
      kpi("Outstanding fines", money(d.total_outstanding)) +
      kpi("Active road alerts", (d.active_alerts || []).length) +
    "</div>" +
    '<div class="row">' +
      cars("My licence", licBlock) +
      cars("My vehicles", (d.vehicles || []).length
        ? '<table>' + (d.vehicles.map(function (v) {
            return "<tr><td><b>" + esc(v.registration_number) + "</b><div class='small muted'>" + esc(v.make) + " " + esc(v.model) + " (" + v.year + ")</div></td><td>" + statusPill(v.status) + "</td></tr>";
          }).join("")) + "</table>"
        : '<div class="empty">No vehicles registered to you.</div>') +
      cars("Road alerts", (d.active_alerts || []).length
        ? d.active_alerts.map(function (a) {
            return '<div class="small" style="padding:6px 0;border-bottom:1px solid var(--line);"><b>' + esc(a.incident_type) + "</b> · " + esc(a.severity) + '<br>' + esc(a.description || "") + (a.road_name ? ' <span class="muted">(' + esc(a.road_name) + ")</span>" : "") + "</div>";
          }).join("")
        : '<div class="empty">No active alerts.</div>') +
    "</div>"
  );
}

function kpi(label, value, sub) {
  return '<div class="kpi"><div class="kpi-label">' + esc(label) + '</div><div class="kpi-value">' + value + '</div>' + (sub ? '<div class="kpi-sub">' + esc(sub) + "</div>" : "") + "</div>";
}
function cars(title, body) {
  return '<div class="card"><h3>' + esc(title) + "</h3>" + body + "</div>";
}

function vehicleRows(vs) {
  return vs.length
    ? '<table><tr><th>Reg</th><th>Owner</th><th>Vehicle</th><th>Status</th></tr>' + vs.map(function (v) {
        return "<tr><td class='mono'><b>" + esc(v.registration_number) + "</b></td><td>" + esc(v.owner_name) + "</td><td>" + esc(v.make) + " " + esc(v.model) + "</td><td>" + statusPill(v.status) + "</td></tr>";
      }).join("") + "</table>"
    : '<div class="empty">No vehicles found.</div>';
}

function violationRows(vs) {
  return vs.length
    ? '<table><tr><th>Type</th><th>Location</th><th>When</th></tr>' + vs.map(function (v) {
        return "<tr><td>" + esc(v.violation_type) + "</td><td>" + esc(v.location) + "</td><td class='small'>" + dt(v.timestamp) + "</td></tr>";
      }).join("") + "</table>"
    : '<div class="empty">No violations recorded.</div>';
}

function challanRows(cs) {
  return cs.length
    ? '<table><tr><th>Ref</th><th>Amount</th><th>Due</th><th>Status</th></tr>' + cs.map(function (c) {
        return "<tr><td class='mono'>" + esc(c.reference) + "</td><td>" + money(c.penalty_amount) + "</td><td class='small'>" + dt(c.due_date) + "</td><td>" + statusPill(c.status) + "</td></tr>";
      }).join("") + "</table>"
    : '<div class="empty">No challans found.</div>';
}

/* ---------------- admin: vehicles ---------------- */

VIEWS.vehicles = async function () {
  var html =
    '<div class="card"><h3>Register vehicle</h3>' +
      vehicleForm() +
    "</div>" +
    '<div class="card"><h3>All vehicles</h3>' +
      '<div class="toolbar"><input type="search" id="search" placeholder="Search registration or owner…">' +
      '<select id="status-filter"><option value="">Any status</option>' + VEHICLE_STATUSES.map(function (s) { return '<option value="' + s + '">' + s + "</option>"; }).join("") + "</select>" +
      '<button class="btn gold sm" id="search-btn">Search</button></div>' +
      '<div id="vehicle-table"></div>' +
    "</div>";
  $("#content").innerHTML = html;

  $("#vehicle-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var btn = $("#vehicle-save");
    btn.disabled = true;
    try {
      var created = await api("/api/vehicles", { method: "POST", body: JSON.stringify(formData(e.target)) });
      toast("Vehicle " + created.registration_number + " registered", "ok");
      e.target.reset();
      loadVehicles();
    } catch (err) {
      toast(err.message, "err");
    } finally { btn.disabled = false; }
  });

  $("#search-btn").addEventListener("click", loadVehicles);
  $("#search").addEventListener("keydown", function (e) { if (e.key === "Enter") loadVehicles(); });
  $("#status-filter").addEventListener("change", loadVehicles);
  await loadVehicles();
};

function vehicleForm() {
  var years = [];
  for (var y = 2026; y >= 1990; y--) years.push(y);
  return (
    '<form id="vehicle-form"><div class="toolbar" style="margin-bottom:4px;">' +
      '<input name="registration_number" placeholder="Registration (e.g. BAK 123)" required>' +
      '<input name="owner_name" placeholder="Owner name" required>' +
      '<input name="owner_id_number" placeholder="Owner NRC" required></div>' +
      '<div class="toolbar" style="margin-bottom:4px;">' +
      '<input name="make" placeholder="Make" required>' +
      '<input name="model" placeholder="Model" required>' +
      '<select name="year">' + years.map(function (y) { return '<option>' + y + "</option>"; }).join("") + "</select>" +
      '<input name="color" placeholder="Colour (optional)">' +
      '<input name="engine_number" placeholder="Engine no. (optional)">' +
      '<input name="chassis_number" placeholder="Chassis no. (optional)">' +
      '</div><button class="btn gold sm" id="vehicle-save" type="submit">Register vehicle</button></form>'
  );
}

async function loadVehicles() {
  var table = $("#vehicle-table");
  table.innerHTML = '<div class="empty">Loading…</div>';
  var search = $("#search").value.trim();
  var status = $("#status-filter").value;
  try {
    var items = await api("/api/vehicles?limit=100" + (search ? "&search=" + encodeURIComponent(search) : "") + (status ? "&status=" + status : ""));
    if (!items.length) { table.innerHTML = '<div class="empty">No vehicles found.</div>'; return; }
    table.innerHTML = '<div class="table-wrap"><table><tr><th>Reg</th><th>Owner</th><th>Vehicle</th><th>Status</th><th>Blacklisted</th><th></th></tr>' +
      items.map(function (v) {
        var b = v.is_blacklisted
          ? '<span class="pill red">yes</span><div class="small muted">' + esc(v.blacklist_reason || "") + "</div>"
          : '<span class="pill gray">no</span>';
        return "<tr><td class='mono'><b>" + esc(v.registration_number) + '</b></td><td>' + esc(v.owner_name) + '</td><td>' + esc(v.make) + " " + esc(v.model) + " (" + v.year + ')</td><td>' + statusPill(v.status) + "</td><td>" + b + "</td><td>" +
          '<button class="btn ghost sm blacklist" data-id="' + esc(v.id) + '" data-blacklisted="' + v.is_blacklisted + '">' + (v.is_blacklisted ? "Unlist" : "Blacklist") + "</button>" +
          "</td></tr>";
      }).join("") + "</table></div>";
    $$("#vehicle-table .blacklist").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var reason = btn.dataset.blacklisted === "true" ? "" : prompt("Blacklist reason:");
        if (reason === null) return;
        var b = btn.dataset.blacklisted === "true" ? false : true;
        api("/api/vehicles/" + btn.dataset.id, {
          method: "PATCH",
          body: JSON.stringify({ is_blacklisted: b, blacklist_reason: reason })
        }).then(function () { toast(b ? "Vehicle blacklisted" : "Blacklist removed", "ok"); loadVehicles(); })
         .catch(function (e) { toast(e.message, "err"); });
      });
    });
  } catch (e) {
    table.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

/* ---------------- admin: drivers ---------------- */

VIEWS.drivers = async function () {
  var html =
    '<div class="card"><h3>All drivers</h3>' +
      '<div class="toolbar"><input type="search" id="d-search" placeholder="Search licence, name or NRC…">' +
      '<button class="btn gold sm" id="d-btn">Search</button></div><div id="driver-table"></div></div>';
  $("#content").innerHTML = html;
  $("#d-btn").addEventListener("click", loadDrivers);
  $("#d-search").addEventListener("keydown", function (e) { if (e.key === "Enter") loadDrivers(); });
  await loadDrivers();
};

async function loadDrivers() {
  var table = $("#driver-table");
  table.innerHTML = '<div class="empty">Loading…</div>';
  var s = $("#d-search").value.trim();
  try {
    var items = await api("/api/drivers?limit=100" + (s ? "&search=" + encodeURIComponent(s) : ""));
    if (!items.length) { table.innerHTML = '<div class="empty">No drivers found.</div>'; return; }
    table.innerHTML = '<div class="table-wrap"><table><tr><th>Licence</th><th>Name</th><th>Class</th><th>Expires</th><th>Status</th><th></th></tr>' +
      items.map(function (dr) {
        return "<tr><td class='mono'><b>" + esc(dr.licence_number) + '</b></td><td>' + esc(dr.first_name) + " " + esc(dr.last_name) + '</td><td>' + esc(dr.licence_class) + '</td><td class="small">' + dt(dr.licence_expiry_date) + "</td><td>" + statusPill(dr.status) + "</td><td>" +
          (dr.status === "active" ? '<button class="btn ghost sm suspend" data-id="' + esc(dr.id) + '">Suspend</button>' : "") +
          "</td></tr>";
      }).join("") + "</table></div>";
    $$("#driver-table .suspend").forEach(function (btn) {
      btn.addEventListener("click", function () {
        api("/api/drivers/" + btn.dataset.id, { method: "DELETE" })
          .then(function () { toast("Driver suspended", "ok"); loadDrivers(); })
          .catch(function (e) { toast(e.message, "err"); });
      });
    });
  } catch (e) {
    table.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

/* ---------------- admin: users ---------------- */

VIEWS.users = async function () {
  $("#content").innerHTML = '<div class="card"><h3>Users</h3><div id="user-table"><div class="empty">Loading…</div></div></div>';
  try {
    var items = await api("/api/admin/users");
    var table = $("#user-table");
    table.innerHTML = '<div class="table-wrap"><table><tr><th>Name</th><th>Email</th><th>Role</th><th>Active</th></tr>' +
      items.map(function (u) {
        return "<tr><td>" + esc(u.full_name) + '</td><td class="mono">' + esc(u.email) + "</td><td>" +
          '<select data-user="' + esc(u.id) + '" class="role-select">' +
          ["admin", "officer", "citizen"].map(function (r) {
            return '<option value="' + r + '"' + (u.role === r ? " selected" : "") + ">" + r + "</option>";
          }).join("") + "</select></td><td>" + (u.is_active ? '<span class="pill green">active</span>' : '<span class="pill red">disabled</span>') + "</td></tr>";
      }).join("") + "</table></div>";
    $$("#user-table .role-select").forEach(function (sel) {
      sel.addEventListener("change", function () {
        api("/api/admin/users/" + sel.dataset.user + "/role?new_role=" + sel.value, { method: "PATCH" })
          .then(function () { toast("Role updated to " + sel.value, "ok"); })
          .catch(function (e) { toast(e.message, "err"); });
      });
    });
  } catch (e) {
    $("#user-table").innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
};

/* ---------------- challans (admin + officer) ---------------- */

VIEWS.challans = async function () {
  $("#content").innerHTML =
    '<div class="card"><h3>Challans</h3><div class="toolbar">' +
      '<select id="c-status"><option value="">All statuses</option>' + CHALLAN_STATUSES.map(function (s) { return '<option value="' + s + '">' + s + "</option>"; }).join("") + "</select>" +
      '<button class="btn gold sm" id="c-btn">Filter</button></div><div id="challan-table"></div></div>';
  $("#c-btn").addEventListener("click", loadChallans);
  $("#c-status").addEventListener("change", loadChallans);
  await loadChallans();
};

async function loadChallans() {
  var table = $("#challan-table");
  table.innerHTML = '<div class="empty">Loading…</div>';
  var status = $("#c-status").value;
  try {
    var items = await api("/api/enforcement/challans?limit=100" + (status ? "&status=" + status : ""));
    if (!items.length) { table.innerHTML = '<div class="empty">No challans found.</div>'; return; }
    table.innerHTML = '<div class="table-wrap"><table><tr><th>Ref</th><th>Amount</th><th>Due</th><th>Status</th><th></th></tr>' +
      items.map(function (c) {
        var pay = c.status !== "paid"
          ? '<button class="btn gold sm pay" data-id="' + esc(c.id) + '" data-ref="' + esc(c.reference) + '">Mark paid</button>'
          : "";
        return "<tr><td class='mono'>" + esc(c.reference) + "</td><td>" + money(c.penalty_amount) + '</td><td class="small">' + dt(c.due_date) + "</td><td>" + statusPill(c.status) + "</td><td>" + pay + "</td></tr>";
      }).join("") + "</table></div>";
    $$("#challan-table .pay").forEach(function (btn) {
      btn.addEventListener("click", function () {
        api("/api/enforcement/challans/" + btn.dataset.id + "/pay", { method: "POST" })
          .then(function () { toast(btn.dataset.ref + " marked as paid", "ok"); loadChallans(); })
          .catch(function (e) { toast(e.message, "err"); });
      });
    });
  } catch (e) {
    table.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

/* ---------------- admin: audit log + rules ---------------- */

VIEWS.audits = async function () {
  $("#content").innerHTML = '<div class="card"><h3>Audit log</h3><div id="audit-table"><div class="empty">Loading…</div></div></div>';
  try {
    var logs = await api("/api/admin/audit-logs?limit=100");
    var table = $("#audit-table");
    if (!logs.length) { table.innerHTML = '<div class="empty">No audit entries.</div>'; return; }
    table.innerHTML = '<div class="table-wrap"><table><tr><th>When</th><th>Action</th><th>Entity</th><th>Details</th></tr>' +
      logs.map(function (l) {
        return "<tr><td class='small'>" + dt(l.timestamp) + '</td><td><span class="pill gray">' + esc(l.action) + '</span></td><td>' + esc(l.entity_type) + "</td><td class='small'>" + esc(l.details) + "</td></tr>";
      }).join("") + "</table></div>";
  } catch (e) {
    $("#audit-table").innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
};

VIEWS.rules = async function () {
  $("#content").innerHTML = '<div class="card"><h3>Notification rules</h3><div id="rules-table"><div class="empty">Loading…</div></div></div>';
  try {
    var rules = await api("/api/admin/notification-rules");
    var table = $("#rules-table");
    if (!rules.length) { table.innerHTML = '<div class="empty">No rules configured.</div>'; return; }
    table.innerHTML = '<div class="table-wrap"><table><tr><th>Event</th><th>Channels</th><th>Active</th></tr>' +
      rules.map(function (r) {
        return "<tr><td class='mono'>" + esc(r.trigger_event) + '</td><td>' + esc(r.channels) + "</td><td>" + (r.is_active ? '<span class="pill green">active</span>' : '<span class="pill gray">off</span>') + "</td></tr>";
      }).join("") + "</table></div>";
  } catch (e) {
    $("#rules-table").innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
};

/* ---------------- officer: violations ---------------- */

VIEWS.violations = async function () {
  $("#content").innerHTML =
    '<div class="row">' +
      '<div class="card"><h3>Record a violation</h3>' +
        '<form id="vio-form">' +
          '<div class="field"><label>Vehicle plate (lookup)</label>' +
            '<div class="toolbar" style="margin-bottom:0;"><input type="text" id="plate-lookup" placeholder="e.g. BAK 123">' +
            '<button type="button" class="btn ghost sm" id="plate-go">Look up</button></div>' +
            '<div class="small muted" id="plate-result" style="margin-top:6px;"></div></div>' +
          '<div class="field"><label>Vehicle ID (auto-filled by lookup)</label><input name="vehicle_id" id="vio-vehicle" placeholder="UUID or leave empty"></div>' +
          '<div class="field"><label>Driver ID (optional)</label><input name="driver_id" placeholder="UUID"></div>' +
          '<div class="field"><label>Violation type</label><select name="violation_type">' +
            VO_TYPES.map(function (t) { return '<option value="' + t[0] + '">' + t[1] + "</option>"; }).join("") + "</select></div>" +
          '<div class="field"><label>Location</label><input name="location" placeholder="e.g. Great East Road, toll gate 3" required></div>' +
          '<div class="field"><label>Date &amp; time (optional)</label><input name="timestamp" type="datetime-local"></div>' +
          '<div class="field"><label>Description (optional)</label><textarea name="description" rows="2"></textarea></div>' +
          '<button class="btn gold" type="submit" id="vio-save">Record &amp; generate e-challan</button>' +
        "</form></div>" +
      '<div class="card"><h3>Recent violations</h3><div id="vio-list"><div class="empty">Loading…</div></div></div>' +
    "</div>";

  $("#plate-go").addEventListener("click", async function () {
    var plate = $("#plate-lookup").value.trim();
    var out = $("#plate-result");
    if (!plate) return;
    try {
      var v = await api("/api/vehicles/by-registration/" + encodeURIComponent(plate));
      $("#vio-vehicle").value = v.id;
      out.innerHTML = 'Found <b>' + esc(v.registration_number) + "</b> (" + esc(v.make) + " " + esc(v.model) + ") · " + statusPill(v.status);
    } catch (e) {
      out.innerHTML = '<span class="err" style="color:var(--err);">' + esc(e.message) + "</span>";
    }
  });

  $("#vio-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var btn = $("#vio-save");
    btn.disabled = true;
    try {
      var created = await api("/api/enforcement/violations", { method: "POST", body: JSON.stringify(formData(e.target)) });
      toast("Violation recorded — challan generated", "ok");
      e.target.reset();
      loadViolations();
    } catch (err) { toast(err.message, "err"); }
    finally { btn.disabled = false; }
  });

  await loadViolations();
};

async function loadViolations() {
  var list = $("#vio-list");
  list.innerHTML = '<div class="empty">Loading…</div>';
  try {
    var items = await api("/api/enforcement/violations?limit=50");
    list.innerHTML = items.length ? violationRows(items) : '<div class="empty">No violations yet.</div>';
  } catch (e) {
    list.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

/* ---------------- officer: toll gates ---------------- */

VIEWS.toll = async function () {
  $("#content").innerHTML =
    '<div class="row">' +
      '<div class="card"><h3>Process toll gate event</h3>' +
        '<form id="toll-form">' +
          '<div class="field"><label>Plate number</label><input name="plate_number" placeholder="e.g. BAK 123" required></div>' +
          '<div class="field"><label>Gate ID</label><input name="gate_id" placeholder="e.g. GATE-01" required></div>' +
          '<div class="field"><label>Toll amount (optional)</label><input name="toll_amount" type="number" placeholder="e.g. 30000"></div>' +
          '<button class="btn gold" type="submit">Process event</button></form>' +
        '<div id="toll-result" class="small" style="margin-top:12px;"></div></div>' +
      '<div class="card"><h3>Recent toll events</h3><div id="toll-list"><div class="empty">Loading…</div></div></div>' +
    "</div>";

  $("#toll-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var btn = e.target.querySelector("button");
    btn.disabled = true;
    var out = $("#toll-result");
    out.innerHTML = '<span class="muted">Checking compliance…</span>';
    try {
      var r = await api("/api/toll/events", { method: "POST", body: JSON.stringify(formData(e.target)) });
      out.innerHTML = '<div class="card" style="box-shadow:none;padding:10px;margin:0;">' +
        '<div><b>' + esc(r.plate_number) + "</b> → " + (r.compliance_result === "compliant" ? '<span class="pill green">COMPLIANT</span>' : '<span class="pill red">FLAGGED</span>') + "</div>" +
        '<div class="small muted" style="margin-top:6px;">' + (r.checks || []).map(function (c) {
          return esc(c.check) + ": <b>" + esc(c.status) + "</b>" + (c.detail ? " — " + esc(c.detail) : "");
        }).join("<br>") + "</div>" +
        (r.flagged_issues && r.flagged_issues.length ? '<div style="margin-top:6px;"><span class="pill amber">' + r.flagged_issues.join("; ") + "</span></div>" : "") +
        (r.challan_created ? '<div style="margin-top:6px;"><span class="pill red">e-challan auto-generated</span></div>' : "") +
        "</div>";
      e.target.reset();
      loadTollEvents();
    } catch (err) {
      out.innerHTML = '<div class="error-box">' + esc(err.message) + "</div>";
    } finally { btn.disabled = false; }
  });

  await loadTollEvents();
};

async function loadTollEvents() {
  var list = $("#toll-list");
  list.innerHTML = '<div class="empty">Loading…</div>';
  try {
    var items = await api("/api/toll/transactions");
    list.innerHTML = items.length
      ? '<table><tr><th>Plate</th><th>Gate</th><th>Result</th><th>When</th></tr>' + items.map(function (t) {
          return "<tr><td class='mono'><b>" + esc(t.plate_number) + "</b></td><td>" + esc(t.gate_id) + "</td><td>" + (t.compliance_result === "compliant" ? '<span class="pill green">compliant</span>' : '<span class="pill red">flagged</span>') + '</td><td class="small">' + dt(t.timestamp) + "</td></tr>";
        }).join("") + "</table>"
      : '<div class="empty">No toll events yet.</div>';
  } catch (e) {
    list.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

/* ---------------- citizen: fines ---------------- */

VIEWS.fines = async function () {
  $("#content").innerHTML = '<div class="card"><h3>My fines &amp; payments</h3><div id="fine-list"><div class="empty">Loading…</div></div></div>';
  try {
    var items = await api("/api/portal/fines?include_paid=true");
    var list = $("#fine-list");
    if (!items.length) { list.innerHTML = '<div class="empty">You have no fines.</div>'; return; }
    list.innerHTML = '<div class="table-wrap"><table><tr><th>Ref</th><th>Type</th><th>Amount</th><th>Due</th><th>Status</th><th></th></tr>' +
      items.map(function (f) {
        var pay = f.status !== "paid"
          ? '<button class="btn gold sm finpay" data-id="' + esc(f.id) + '" data-ref="' + esc(f.reference) + '">Pay now</button>'
          : "";
        return "<tr><td class='mono'>" + esc(f.reference) + "</td><td>" + esc(f.violation_type) + "</td><td>" + money(f.penalty_amount) + '</td><td class="small">' + dt(f.due_date) + "</td><td>" + statusPill(f.status) + "</td><td>" + pay + "</td></tr>";
      }).join("") + "</table></div>";
    $$("#fine-list .finpay").forEach(function (btn) {
      btn.addEventListener("click", function () {
        api("/api/portal/fines/" + btn.dataset.id + "/pay", { method: "POST" })
          .then(function (r) {
            toast("Paid — " + r.payment_reference + ". Receipt in notifications.", "ok");
            refreshBell();
            go("fines");
          })
          .catch(function (e) { toast(e.message, "err"); });
      });
    });
  } catch (e) {
    $("#fine-list").innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
};

/* ---------------- citizen: licence ---------------- */

VIEWS.licence = async function () {
  $("#content").innerHTML = '<div class="card" id="licence-card"><div class="empty">Loading…</div></div>';
  try {
    var l = await api("/api/portal/licence");
    var statePill = l.state === "expired" ? "red" : l.state === "expiring_soon" ? "amber" : "green";
    $("#licence-card").innerHTML =
      '<h3>My licence <span class="pill ' + statePill + '">' + esc(l.state) + "</span></h3>" +
      '<table>' +
        "<tr><td>Licence number</td><td class='mono'><b>" + esc(l.licence_number) + "</b></td></tr>" +
        "<tr><td>Class</td><td>" + esc(l.licence_class) + "</td></tr>" +
        "<tr><td>Status</td><td>" + statusPill(l.status) + "</td></tr>" +
        "<tr><td>Issue date</td><td>" + dt(l.issue_date) + "</td></tr>" +
        "<tr><td>Expiry date</td><td>" + dt(l.expiry_date) + " (" + Number(l.days_until_expiry) + " days left)</td></tr>" +
        "<tr><td>Restrictions</td><td>" + esc(l.restrictions || "None") + "</td></tr>" +
      "</table>" +
      '<div style="margin-top:14px;"><button class="btn gold" id="renew-btn">Renew now (' + money(350000) + ')</button></div>' +
      '<div class="small muted" style="margin-top:8px;">Renewal extends your licence by 5 years (sandbox payment).</div>';
    $("#renew-btn").addEventListener("click", async function () {
      if (!confirm("Renew your licence for 5 years for " + money(350000) + "?")) return;
      try {
        await api("/api/portal/licence/renew", { method: "POST" });
        toast("Licence renewed — receipt sent to your notifications", "ok");
        refreshBell();
        go("licence");
      } catch (e) { toast(e.message, "err"); }
    });
  } catch (e) {
    $("#licence-card").innerHTML = '<div class="error-box">' + esc(e.message) + '</div><div class="card">Your account is not linked to a driver record yet. Use the RTSA registration portal or ask an officer/admin to create one.</div>';
  }
};

/* ---------------- citizen: alerts ---------------- */

VIEWS.alerts = async function () {
  $("#content").innerHTML = '<div class="card"><h3>Active road alerts</h3><div id="alert-list"><div class="empty">Loading…</div></div></div>';
  try {
    var items = await api("/api/portal/alerts");
    var list = $("#alert-list");
    list.innerHTML = items.length
      ? items.map(function (a) {
          var cls = a.severity === "high" ? "red" : a.severity === "moderate" ? "amber" : "blue";
          return '<div style="padding:10px 0;border-bottom:1px solid var(--line);">' +
            "<b>" + esc(a.incident_type) + "</b> · <span class='pill " + cls + "'>" + esc(a.severity) + "</span>" +
            (a.road_name ? ' <span class="muted">on ' + esc(a.road_name) + "</span>" : "") +
            (a.description ? '<div class="small">' + esc(a.description) + "</div>" : "") +
            '<div class="small muted">Since ' + dt(a.starts_at) + "</div></div>";
        }).join("")
      : '<div class="empty">No active alerts.</div>';
  } catch (e) {
    $("#alert-list").innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
};

/* ---------------- route planner (all roles) ---------------- */

VIEWS.planner = async function () {
  $("#content").innerHTML =
    '<div class="row">' +
      '<div class="card"><h3>Plan a route <span class="subtle">(incident-aware)</span></h3>' +
        '<div class="field"><label>From (intersection)</label><select id="from-select"></select></div>' +
        '<div class="field"><label>To (intersection)</label><select id="to-select"></select></div>' +
        '<label><input type="checkbox" id="avoid-incidents" checked> Avoid incidents / road closures</label>' +
        '<div style="margin-top:12px;"><button class="btn gold" id="plan-btn">Find route</button></div>' +
        '<div id="route-result"></div></div>' +
      '<div class="card"><h3>Live road status</h3><div id="status-board"><div class="empty">Loading…</div></div></div>' +
    "</div>";

  var fromSel = $("#from-select"), toSel = $("#to-select");
  try {
    var inters = await api("/api/road-network/intersections");
    var opts = inters.map(function (i) { return '<option value="' + esc(i.name) + '">' + esc(i.name) + "</option>"; }).join("");
    fromSel.innerHTML = opts;
    toSel.innerHTML = opts;
  } catch (e) {
    fromSel.innerHTML = "<option>Network unavailable</option>";
    toSel.innerHTML = "<option>Network unavailable</option>";
    $("#status-board").innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }

  $("#plan-btn").addEventListener("click", planRoute);
  fromSel.addEventListener("change", planRoute);
  toSel.addEventListener("change", planRoute);
  await loadStatusBoard();
};

async function planRoute() {
  var from = $("#from-select").value, to = $("#to-select").value;
  var out = $("#route-result");
  if (!from || !to) return;
  if (from === to) { out.innerHTML = '<div class="error-box">Choose two different intersections.</div>'; return; }
  var avoid = $("#avoid-incidents").checked;
  out.innerHTML = '<span class="muted">Planning…</span>';
  try {
    var r = await api("/api/routing/route?from_=" + encodeURIComponent(from) + "&to=" + encodeURIComponent(to) + "&avoid_incidents=" + avoid + "&include_alternatives=true");
    var blocks = [];
    if (r.primary_route) {
      blocks.push('<div class="card" style="box-shadow:none;margin:10px 0 0;padding:10px;"><b>Primary route</b> · ' + r.primary_route.step_count + " steps · " +
        r.primary_route.total_distance_km + " km · ~" + r.primary_route.total_minutes + ' min' +
        (r.incidents_avoided ? ' <span class="pill amber">avoided ' + r.incidents_avoided + ' incident segment(s)</span>' : "") +
        '<table style="margin-top:8px;"><tr><th>Road</th><th>From → To</th><th>Dist</th><th>Min</th></tr>' +
        r.primary_route.steps.map(function (s) {
          return "<tr><td>" + esc(s.road_name) + "</td><td class='small'>" + esc(s.from_intersection) + " → " + esc(s.to_intersection) + "</td><td>" + s.distance_km + "</td><td>" + s.travel_minutes + "</td></tr>";
        }).join("") + "</table></div>");
    } else {
      blocks.push('<div class="error-box">No route found between these intersections.</div>');
    }
    (r.alternatives || []).forEach(function (alt, idx) {
      blocks.push('<div class="card" style="box-shadow:none;margin:8px 0 0;padding:10px;"><b>Alternative ' + (idx + 1) + "</b> · " + alt.step_count + " steps · " +
        alt.total_distance_km + " km · ~" + alt.total_minutes + " min</div>");
    });
    out.innerHTML = blocks.join("");
  } catch (e) {
    out.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

async function loadStatusBoard() {
  var board = $("#status-board");
  try {
    var rows = await api("/api/routing/status");
    board.innerHTML = rows.length
      ? '<table><tr><th>Road</th><th>Class</th><th>Status</th><th>Incidents</th></tr>' + rows.map(function (r) {
          var cls = r.status === "closed" ? "red" : r.status === "congested" ? "amber" : "green";
          var inc = (r.active_incidents || []).map(function (i) { return i; }).join(", ");
          return "<tr><td>" + esc(r.road) + '</td><td>' + esc(r["class"]) + "</td><td><span class='pill " + cls + "'>" + esc(r.status) + "</span></td><td class='small'>" + esc(inc || "—") + "</td></tr>";
        }).join("") + "</table>"
      : '<div class="empty">No roads in network.</div>';
  } catch (e) {
    board.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

/* ---------------- boot ---------------- */

function wire() {
  $("#login-form").addEventListener("submit", function (e) {
    e.preventDefault();
    doLogin($("#email").value.trim(), $("#password").value, $("#login-msg"), $("#login-btn"));
  });
  $("#logout-btn").addEventListener("click", logout);
  $("#bell").addEventListener("click", openNotifs);
  $("#notif-close").addEventListener("click", function () { $("#notif-drawer").classList.add("hidden"); });
  $("#notif-drawer").addEventListener("click", function (e) {
    if (e.target === this || e.target.id === "notif-drawer") this.classList.add("hidden");
  });
  window.addEventListener("hashchange", function () {
    if (USER) loadRouter();
  });
}

window.addEventListener("DOMContentLoaded", function () {
  wire();
  if (getToken()) {
    bootstrapApp().catch(function () {
      logout();
    });
  } else {
    $("#login-screen").classList.remove("hidden");
  }
});