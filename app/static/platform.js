"use strict";

/* Developer 2 modules: reports, payments/revenue, administration, security,
   integrations, operations, and the citizen self-service extras.
   Loaded after app.js; reuses its helpers (api, esc, money, dt, kpi, cars, toast). */

/* ---------------- helpers ---------------- */

function can(perm) { return !!(USER && USER.permissions && USER.permissions.indexOf(perm) !== -1); }

function tbl(headers, rows) {
  if (!rows.length) return '<div class="empty">Nothing to show.</div>';
  return '<div class="table-wrap"><table><tr>' + headers.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("") +
    "</tr>" + rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>"; }).join("") +
    "</table></div>";
}

function pill(text, cls) { return '<span class="pill ' + (cls || "gray") + '">' + esc(text) + "</span>"; }
function fail(id, e) { $(id).innerHTML = '<div class="error-box">' + esc(e.message) + "</div>"; }
function short(id) { return String(id || "").slice(0, 8); }

async function download(path, fallbackName) {
  var res = await fetch(path, { headers: { Authorization: "Bearer " + getToken(), "X-Device-Id": deviceId() } });
  if (!res.ok) {
    var msg = "HTTP " + res.status;
    try { msg = (await res.json()).detail || msg; } catch (e) { /* not json */ }
    throw new Error(msg);
  }
  var cd = res.headers.get("content-disposition") || "";
  var m = /filename="?([^";]+)"?/.exec(cd);
  var blob = await res.blob();
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = m ? m[1] : fallbackName;
  document.body.appendChild(a);
  a.click();
  setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

function mutate(path, method, body, okMsg, reload) {
  return api(path, { method: method, body: body ? JSON.stringify(body) : undefined })
    .then(function (r) { if (okMsg) toast(okMsg, "ok"); if (reload) reload(); return r; })
    .catch(function (e) { toast(e.message, "err"); });
}

/* ---------------- navigation ---------------- */

(function extendNav() {
  function add(role, items) { NAV[role] = (NAV[role] || []).concat(items); }
  add("admin", [
    { id: "reports", label: "Reports & analytics" },
    { id: "payments", label: "Revenue & payments" },
    { id: "integrations", label: "Agency integrations" },
    { id: "settings", label: "Settings & access" },
    { id: "system", label: "System health" },
    { id: "toll", label: "Toll gates" },
    { id: "account", label: "My account" }
  ]);
  add("officer", [{ id: "reports", label: "Reports" }, { id: "account", label: "My account" }]);
  add("toll_operator", [{ id: "reports", label: "Reports" }, { id: "account", label: "My account" }]);
  add("citizen", [
    { id: "vehicles", label: "My vehicles" },
    { id: "applications", label: "My applications" },
    { id: "receipts", label: "Payment history" },
    { id: "account", label: "My account" }
  ]);
  var admin = NAV.admin;
  NAV.admin = admin.filter(function (x) { return x.id !== "planner"; }).concat(admin.filter(function (x) { return x.id === "planner"; }));
})();

var baseDashboard = VIEWS.dashboard;
VIEWS.dashboard = async function () {
  if (USER.role === "toll_operator") {
    $("#content").innerHTML = '<div class="card"><h3>Welcome, ' + esc(USER.full_name) + '</h3>' +
      '<p class="muted">Post vehicle checks at your toll gate and review reports.</p>' +
      '<a class="btn gold" href="#/toll">Open toll gate</a></div>';
    return;
  }
  return baseDashboard();
};

/* ---------------- reports & analytics ---------------- */

VIEWS.reports = async function () {
  $("#content").innerHTML = '<div id="rp-kpis"><div class="empty">Loading analytics…</div></div>' +
    '<div class="card"><h3>Report builder</h3><div class="toolbar">' +
    '<select id="rp-key"></select>' +
    '<input type="date" id="rp-from" aria-label="From"><input type="date" id="rp-to" aria-label="To">' +
    '<button class="btn gold sm" id="rp-run">Run</button>' +
    (can("reports:export")
      ? '<button class="btn sm" data-fmt="csv">CSV</button><button class="btn sm" data-fmt="xlsx">Excel</button><button class="btn sm" data-fmt="pdf">PDF</button>'
      : '<span class="small muted">Exports need the reports:export permission</span>') +
    '</div><div id="rp-out"></div></div>';
  try {
    var cat = await api("/api/reports/");
    $("#rp-key").innerHTML = cat.map(function (r) { return '<option value="' + esc(r.key) + '">' + esc(r.title) + "</option>"; }).join("");
    var d = await api("/api/reports/dashboard");
    var k = d.kpis;
    $("#rp-kpis").innerHTML = '<div class="grid cards">' +
      kpi("New registrations", k.registrations["New registrations"], "vehicles on register: " + k.registrations["Total vehicles on register"]) +
      kpi("Violations", k.violations["Violations"], "collection rate " + k.violations["Collection rate"]) +
      kpi("Accidents", k.accidents["Accidents"], k.accidents["Accident hotspot"] ? "hotspot: " + k.accidents["Accident hotspot"] : "") +
      kpi("Net revenue", money(k.revenue["Net revenue"]), "refunded " + money(k.revenue["Refunded"])) +
      kpi("Toll non-compliance", k.toll["Non-compliance rate"], k.toll["Flagged"] + " of " + k.toll["Vehicles checked"] + " checks") +
      kpi("PSV permits valid", k.psv["Permits currently valid"], k.psv["Permits expired"] + " expired") +
      "</div>";
  } catch (e) { fail("#rp-kpis", e); }

  function params() {
    var q = [];
    if ($("#rp-from").value) q.push("date_from=" + $("#rp-from").value);
    if ($("#rp-to").value) q.push("date_to=" + $("#rp-to").value);
    return q;
  }
  async function run() {
    var out = $("#rp-out");
    out.innerHTML = '<div class="empty">Running…</div>';
    try {
      var r = await api("/api/reports/" + $("#rp-key").value + "?limit=200" + (params().length ? "&" + params().join("&") : ""));
      var sum = Object.keys(r.summary).map(function (key) {
        return '<div class="kv"><span class="muted">' + esc(key) + "</span><b>" + esc(r.summary[key]) + "</b></div>";
      }).join("");
      out.innerHTML = '<div class="kvs">' + sum + "</div>" +
        '<p class="small muted">' + r.count + " row(s) shown · " + esc(r.range.from.slice(0, 10)) + " → " + esc(r.range.to.slice(0, 10)) + "</p>" +
        tbl(r.columns, r.rows.map(function (row) { return row.map(function (c) { return esc(/^\d{4}-\d\d-\d\dT/.test(String(c)) ? dt(c) : c); }); }));
    } catch (e) { fail("#rp-out", e); }
  }
  $("#rp-run").addEventListener("click", run);
  $$("[data-fmt]").forEach(function (b) {
    b.addEventListener("click", function () {
      var q = params().concat(["format=" + b.dataset.fmt]);
      download("/api/reports/" + $("#rp-key").value + "?" + q.join("&"), "report." + b.dataset.fmt)
        .catch(function (e) { toast(e.message, "err"); });
    });
  });
  run();
};

/* ---------------- revenue & payments (admin) ---------------- */

VIEWS.payments = async function () {
  $("#content").innerHTML = '<div id="pay-kpis"></div>' +
    '<div class="card"><h3>Transactions</h3><div class="toolbar">' +
    '<select id="pay-status"><option value="">All statuses</option><option>completed</option><option>pending</option><option>failed</option><option>refunded</option></select>' +
    '<button class="btn gold sm" id="pay-go">Filter</button></div><div id="pay-list"></div></div>' +
    '<div class="card"><h3>Gateway reconciliation</h3>' +
    '<p class="small muted">Paste the gateway settlement statement as JSON: <code>[{"gateway_reference":"SANDBOX-…","amount":1000}]</code></p>' +
    '<div class="toolbar"><select id="rc-gw"><option>sandbox</option><option>mobile_money</option></select></div>' +
    '<div class="field"><textarea id="rc-stmt" rows="4" placeholder="[]"></textarea></div>' +
    '<button class="btn gold sm" id="rc-run">Reconcile</button><div id="rc-out" style="margin-top:12px"></div>' +
    '<h4 style="margin-top:16px">Previous runs</h4><div id="rc-runs"></div></div>';

  async function loadSummary() {
    try {
      var s = await api("/api/payments/summary?days=30");
      $("#pay-kpis").innerHTML = '<div class="grid cards">' + kpi("Gross (30 days)", money(s.gross)) + kpi("Refunded", money(s.refunded)) +
        kpi("Net revenue", money(s.net)) +
        kpi("By type", Object.keys(s.by_type).map(function (t) { return esc(t) + ": " + money(s.by_type[t].total); }).join("<br>") || "—") + "</div>";
    } catch (e) { fail("#pay-kpis", e); }
  }
  async function loadList() {
    var st = $("#pay-status").value;
    try {
      var items = await api("/api/payments/?all=true&limit=100" + (st ? "&status=" + st : ""));
      $("#pay-list").innerHTML = tbl(["Receipt", "Reference", "Type", "Amount", "Gateway", "Status", ""], items.map(function (p) {
        var cls = p.status === "completed" ? "green" : p.status === "failed" ? "red" : p.status === "refunded" ? "amber" : "gray";
        var acts = "";
        if (p.receipt_number) acts += '<button class="btn ghost sm rcpt" data-id="' + esc(p.id) + '">PDF</button> ';
        if (p.status === "completed" && can("payments:refund")) acts += '<button class="btn sm refund" data-id="' + esc(p.id) + '">Refund</button>';
        return [esc(p.receipt_number || "—"), '<span class="mono">' + esc(p.reference) + "</span>", esc(p.payment_type),
          money(p.amount) + (p.refunded_amount ? '<div class="small muted">refunded ' + money(p.refunded_amount) + "</div>" : ""),
          esc(p.gateway), pill(p.status, cls), acts];
      }));
      $$("#pay-list .rcpt").forEach(function (b) {
        b.addEventListener("click", function () { download("/api/payments/" + b.dataset.id + "/receipt.pdf", "receipt.pdf").catch(function (e) { toast(e.message, "err"); }); });
      });
      $$("#pay-list .refund").forEach(function (b) {
        b.addEventListener("click", function () {
          var reason = prompt("Reason for refund (required):");
          if (!reason) return;
          mutate("/api/payments/" + b.dataset.id + "/refund", "POST", { reason: reason }, "Refund issued", function () { loadSummary(); loadList(); });
        });
      });
    } catch (e) { fail("#pay-list", e); }
  }
  async function loadRuns() {
    try {
      var runs = await api("/api/payments/reconciliation/runs");
      $("#rc-runs").innerHTML = tbl(["When", "Gateway", "Matched", "Mismatched", "Not in ledger", "Not in statement"], runs.map(function (r) {
        return [dt(r.created_at), esc(r.gateway), r.matched, r.mismatched, r.missing_in_ledger, r.missing_in_statement];
      }));
    } catch (e) { fail("#rc-runs", e); }
  }
  $("#pay-go").addEventListener("click", loadList);
  $("#rc-run").addEventListener("click", async function () {
    var rows;
    try { rows = JSON.parse($("#rc-stmt").value || "[]"); } catch (e) { toast("Statement is not valid JSON", "err"); return; }
    try {
      var r = await api("/api/payments/reconciliation/run", { method: "POST", body: JSON.stringify({ gateway: $("#rc-gw").value, statement: rows }) });
      $("#rc-out").innerHTML = '<div class="grid cards">' + kpi("Matched", r.matched) + kpi("Amount mismatches", r.mismatched) +
        kpi("Not in our ledger", r.missing_in_ledger) + kpi("Missing from statement", r.missing_in_statement) + "</div>";
      loadRuns();
    } catch (e) { toast(e.message, "err"); }
  });
  loadSummary(); loadList(); loadRuns();
};

/* ---------------- administration: users ---------------- */

VIEWS.users = async function () {
  $("#content").innerHTML = '<div class="card"><h3>Add user</h3><form id="u-form" class="toolbar">' +
    '<input name="full_name" placeholder="Full name" required><input name="email" type="email" placeholder="Email" required>' +
    '<input name="password" type="password" placeholder="Temp password (8+, letters & numbers)" required>' +
    '<select name="role"><option>citizen</option><option>officer</option><option>toll_operator</option><option>admin</option></select>' +
    '<button class="btn gold sm">Create</button></form></div>' +
    '<div class="card"><h3>Users</h3><div class="toolbar"><input type="search" id="u-q" placeholder="Search name or email">' +
    '<button class="btn sm" id="u-go">Search</button></div><div id="user-table"></div></div>';

  async function load() {
    var q = $("#u-q").value.trim();
    try {
      var items = await api("/api/admin/users?limit=200" + (q ? "&search=" + encodeURIComponent(q) : ""));
      $("#user-table").innerHTML = tbl(["Name", "Email", "Role", "Status", "MFA", "Last sign-in", "Actions"], items.map(function (u) {
        var locked = u.locked_until && new Date(u.locked_until) > new Date();
        var opts = ["citizen", "officer", "toll_operator", "admin"].map(function (r) {
          return '<option value="' + r + '"' + (u.role === r ? " selected" : "") + ">" + r + "</option>";
        }).join("");
        return [esc(u.full_name), '<span class="mono">' + esc(u.email) + "</span>",
          '<select class="role-sel" data-id="' + esc(u.id) + '">' + opts + "</select>",
          (u.is_active ? pill("active", "green") : pill("disabled", "red")) + (locked ? " " + pill("locked", "amber") : ""),
          u.mfa_enabled ? pill("on", "green") : pill("off", "gray"), u.last_login_at ? dt(u.last_login_at) : "never",
          '<button class="btn ghost sm act" data-a="toggle" data-id="' + esc(u.id) + '" data-on="' + u.is_active + '">' + (u.is_active ? "Disable" : "Enable") + "</button> " +
          (locked ? '<button class="btn ghost sm act" data-a="unlock" data-id="' + esc(u.id) + '">Unlock</button> ' : "") +
          '<button class="btn ghost sm act" data-a="reset" data-id="' + esc(u.id) + '">Reset pw</button> ' +
          '<button class="btn ghost sm act" data-a="signout" data-id="' + esc(u.id) + '">Sign out</button>' +
          (u.mfa_enabled ? ' <button class="btn ghost sm act" data-a="nomfa" data-id="' + esc(u.id) + '">Clear MFA</button>' : "")];
      }));
      $$("#user-table .role-sel").forEach(function (sel) {
        sel.addEventListener("change", function () {
          mutate("/api/admin/users/" + sel.dataset.id + "/role?new_role=" + sel.value, "PATCH", null, "Role updated (their sessions were signed out)", load);
        });
      });
      $$("#user-table .act").forEach(function (b) {
        b.addEventListener("click", function () {
          var id = b.dataset.id, a = b.dataset.a;
          if (a === "toggle") mutate("/api/admin/users/" + id, "PATCH", { is_active: b.dataset.on !== "true" }, "Updated", load);
          else if (a === "unlock") mutate("/api/admin/users/" + id + "/unlock", "POST", null, "Account unlocked", load);
          else if (a === "signout") mutate("/api/admin/users/" + id + "/sessions/revoke", "POST", null, "Signed out everywhere", load);
          else if (a === "nomfa") { if (confirm("Remove this user's MFA?")) mutate("/api/admin/users/" + id + "/mfa", "DELETE", null, "MFA cleared", load); }
          else if (a === "reset") {
            var pw = prompt("New temporary password (8+ characters, letters and numbers):");
            if (pw) mutate("/api/admin/users/" + id + "/reset-password", "POST", { new_password: pw }, "Password reset", load);
          }
        });
      });
    } catch (e) { fail("#user-table", e); }
  }
  $("#u-go").addEventListener("click", load);
  $("#u-form").addEventListener("submit", function (e) {
    e.preventDefault();
    mutate("/api/admin/users", "POST", formData(e.target), "User created", function () { e.target.reset(); load(); });
  });
  load();
};

/* ---------------- administration: settings, rules and access ---------------- */

VIEWS.settings = async function () {
  $("#content").innerHTML = '<div class="card"><h3>System settings, rules &amp; thresholds</h3><div id="st-list"></div></div>' +
    '<div class="card"><h3>Role permissions</h3><p class="small muted">Tick a box to grant a permission to a role. Admins always keep everything.</p><div id="rb-list"></div></div>' +
    '<div class="card"><h3>Notification rules</h3><div id="nr-list"></div></div>' +
    '<div class="card"><h3>Sign-in activity</h3><div id="la-list"></div></div>';

  async function loadSettings() {
    try {
      var items = await api("/api/admin/settings");
      $("#st-list").innerHTML = tbl(["Setting", "Value", "Description", ""], items.map(function (s) {
        var input = s.type === "bool"
          ? '<select class="st-in" data-key="' + esc(s.key) + '"><option value="true"' + (s.value === "true" ? " selected" : "") + '>true</option><option value="false"' + (s.value === "false" ? " selected" : "") + ">false</option></select>"
          : '<input class="st-in" data-key="' + esc(s.key) + '" value="' + esc(s.value) + '" style="max-width:150px">';
        return ['<span class="mono">' + esc(s.key) + "</span>" + (s.is_default ? "" : " " + pill("changed", "amber")), input,
          '<span class="small">' + esc(s.description) + "</span>",
          '<button class="btn gold sm st-save" data-key="' + esc(s.key) + '">Save</button>' +
          (s.is_default ? "" : ' <button class="btn ghost sm st-reset" data-key="' + esc(s.key) + '">Reset</button>')];
      }));
      $$("#st-list .st-save").forEach(function (b) {
        b.addEventListener("click", function () {
          var v = $('.st-in[data-key="' + b.dataset.key + '"]').value;
          mutate("/api/admin/settings/" + b.dataset.key, "PUT", { value: v }, "Saved", loadSettings);
        });
      });
      $$("#st-list .st-reset").forEach(function (b) {
        b.addEventListener("click", function () { mutate("/api/admin/settings/" + b.dataset.key, "DELETE", null, "Reset to default", loadSettings); });
      });
    } catch (e) { fail("#st-list", e); }
  }
  async function loadRbac() {
    try {
      var m = await api("/api/admin/permissions");
      var roles = ["officer", "toll_operator", "citizen", "admin"];
      $("#rb-list").innerHTML = tbl(["Permission"].concat(roles), Object.keys(m.permissions).map(function (p) {
        return ['<b class="mono">' + esc(p) + '</b><div class="small muted">' + esc(m.permissions[p]) + "</div>"].concat(roles.map(function (r) {
          var on = m.roles[r].indexOf(p) !== -1;
          return '<input type="checkbox" class="rb" data-role="' + r + '" data-perm="' + esc(p) + '"' + (on ? " checked" : "") + (r === "admin" ? " disabled" : "") + ">";
        }));
      }));
      $$("#rb-list .rb").forEach(function (c) {
        c.addEventListener("change", function () {
          mutate("/api/admin/permissions/" + c.dataset.role + "/" + encodeURIComponent(c.dataset.perm) + "?granted=" + c.checked, "PUT", null, "Permission updated", loadRbac);
        });
      });
    } catch (e) { fail("#rb-list", e); }
  }
  async function loadRules() {
    try {
      var rules = await api("/api/admin/notification-rules");
      $("#nr-list").innerHTML = tbl(["Event", "Channels", "Message", "Active"], rules.map(function (r) {
        return ['<span class="mono">' + esc(r.trigger_event) + "</span>", esc(r.channels), '<span class="small">' + esc(r.body_template) + "</span>",
          '<input type="checkbox" class="nr" data-id="' + esc(r.id) + '"' + (r.is_active ? " checked" : "") + ">"];
      }));
      $$("#nr-list .nr").forEach(function (c) {
        c.addEventListener("change", function () { mutate("/api/admin/notification-rules/" + c.dataset.id, "PATCH", { is_active: c.checked }, "Rule updated"); });
      });
    } catch (e) { fail("#nr-list", e); }
  }
  async function loadAttempts() {
    try {
      var rows = await api("/api/admin/login-attempts?limit=25");
      $("#la-list").innerHTML = tbl(["When", "Email", "IP", "Result"], rows.map(function (r) {
        return [dt(r.created_at), '<span class="mono">' + esc(r.email) + "</span>", esc(r.ip_address || ""),
          r.success ? pill("success", "green") : pill(r.reason || "failed", "red")];
      }));
    } catch (e) { fail("#la-list", e); }
  }
  loadSettings(); loadRbac(); loadRules(); loadAttempts();
};

/* ---------------- inter-agency integrations (admin) ---------------- */

VIEWS.integrations = async function () {
  $("#content").innerHTML = '<div id="ag-key"></div>' +
    '<div class="card"><h3>Register an agency</h3><form id="ag-form"><div class="toolbar">' +
    '<input name="name" placeholder="Agency name" required>' +
    '<select name="agency_type"><option value="police">police</option><option value="insurance">insurance</option><option value="hospital">hospital</option><option value="toll_authority">toll_authority</option><option value="national_id">national_id</option></select>' +
    '<input name="contact_email" type="email" placeholder="Contact email">' +
    '<input name="contract_expires_at" type="date" aria-label="Contract expiry">' +
    '<input name="rate_limit_per_minute" type="number" min="1" placeholder="Calls/min (120)">' +
    '<button class="btn gold sm">Register</button></div>' +
    '<p class="small muted">Scopes default to everything that agency type is allowed. Narrow them after creating.</p></form></div>' +
    '<div class="card"><h3>Agencies &amp; data-sharing contracts</h3><div id="ag-list"></div></div>' +
    '<div class="card"><h3>Monitoring (last 24h)</h3><div id="ag-mon"></div></div>';

  async function loadAgencies() {
    try {
      var items = await api("/api/integration/agencies");
      $("#ag-list").innerHTML = tbl(["Agency", "Type", "Scopes", "Contract", "Last used", ""], items.map(function (a) {
        var exp = a.contract_expires_at ? new Date(a.contract_expires_at) : null;
        var expired = exp && exp < new Date();
        return ["<b>" + esc(a.name) + '</b><div class="small mono muted">' + esc(a.api_key_prefix) + "</div>", esc(a.agency_type),
          '<span class="small">' + esc(a.scopes.join(", ")) + "</span>",
          exp ? (expired ? pill("expired", "red") : pill("until " + exp.toISOString().slice(0, 10), "green")) : pill("open-ended", "gray"),
          a.last_used_at ? dt(a.last_used_at) : "never",
          '<button class="btn ghost sm ag" data-a="toggle" data-id="' + esc(a.id) + '" data-on="' + a.is_active + '">' + (a.is_active ? "Disable" : "Enable") + "</button> " +
          '<button class="btn ghost sm ag" data-a="rotate" data-id="' + esc(a.id) + '">Rotate key</button>' + (a.is_active ? "" : " " + pill("disabled", "red"))];
      }));
      $$("#ag-list .ag").forEach(function (b) {
        b.addEventListener("click", async function () {
          if (b.dataset.a === "toggle") mutate("/api/integration/agencies/" + b.dataset.id, "PATCH", { is_active: b.dataset.on !== "true" }, "Updated", loadAgencies);
          else if (confirm("Rotating invalidates the current key immediately. Continue?")) {
            var r = await mutate("/api/integration/agencies/" + b.dataset.id + "/rotate-key", "POST", null, "Key rotated");
            if (r) showKey(r.api_key);
          }
        });
      });
    } catch (e) { fail("#ag-list", e); }
  }
  function showKey(key) {
    $("#ag-key").innerHTML = '<div class="card" style="border-color:var(--gold,#c8a13a)"><h3>API key — shown once</h3>' +
      '<p class="small">Copy it now; only its hash is stored.</p><pre class="mono" style="white-space:pre-wrap;word-break:break-all">' + esc(key) + "</pre></div>";
  }
  async function loadMon() {
    try {
      var m = await api("/api/integration/monitoring?hours=24");
      $("#ag-mon").innerHTML = tbl(["Agency", "Direction", "Calls", "Success", "Avg ms", "Max ms", "Last call"], m.agencies.map(function (a) {
        return [esc(a.agency), esc(a.direction), a.calls, pill((a.success_rate == null ? "—" : a.success_rate + "%"), a.success_rate >= 95 ? "green" : a.success_rate >= 70 ? "amber" : "red"),
          a.avg_latency_ms, a.max_latency_ms, dt(a.last_call_at)];
      })) + (m.recent_errors.length ? '<h4 style="margin-top:14px">Recent errors</h4>' + tbl(["When", "Agency", "Endpoint", "Status", "Detail"], m.recent_errors.map(function (e) {
        return [dt(e.at), esc(e.agency), '<span class="mono">' + esc(e.endpoint) + "</span>", pill(e.status, "red"), esc(e.detail || "")];
      })) : "");
    } catch (e) { fail("#ag-mon", e); }
  }
  $("#ag-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var body = formData(e.target);
    if (body.contract_expires_at) body.contract_expires_at = new Date(body.contract_expires_at).toISOString();
    if (body.rate_limit_per_minute) body.rate_limit_per_minute = parseInt(body.rate_limit_per_minute, 10);
    var r = await mutate("/api/integration/agencies", "POST", body, "Agency registered");
    if (r) { showKey(r.api_key); e.target.reset(); loadAgencies(); }
  });
  loadAgencies(); loadMon();
};

/* ---------------- system health (admin) ---------------- */

VIEWS.system = async function () {
  $("#content").innerHTML = '<div id="sy-kpis"></div><div class="card"><h3>Slowest routes (p95)</h3><div id="sy-routes"></div></div>' +
    '<div class="card"><h3>Backups</h3><div class="toolbar"><button class="btn gold sm" id="bk-now">Back up now</button>' +
    '<button class="btn sm" id="sy-scan">Run expiry reminders</button><button class="btn sm" id="sy-disp">Deliver queued SMS/email</button></div><div id="bk-list"></div></div>' +
    '<div class="card"><h3>Notification delivery</h3><div id="sy-notif"></div></div>';
  try {
    var m = await api("/api/system/metrics");
    var t = m.toll_compliance_target, c = await api("/api/system/capacity");
    $("#sy-kpis").innerHTML = '<div class="grid cards">' + kpi("Requests served", m.total_requests, m.total_5xx + " server errors") +
      kpi("Median / p95", m.overall.p50_ms + " / " + m.overall.p95_ms + " ms") +
      kpi("Toll decision target", t.target_ms + " ms", t.samples ? (t.within_target ? "p95 " + t.p95_ms + " ms — within target" : "p95 " + t.p95_ms + " ms — OVER target") : "no toll checks yet") +
      kpi("Records", Object.keys(c.row_counts).map(function (k) { return esc(k) + ": " + c.row_counts[k]; }).join("<br>")) + "</div>";
    $("#sy-routes").innerHTML = tbl(["Route", "Requests", "p50", "p95", "p99", "5xx"], Object.keys(m.routes).slice(0, 12).map(function (r) {
      var x = m.routes[r];
      return ['<span class="mono">' + esc(r) + "</span>", x.requests, x.p50_ms + " ms", x.p95_ms + " ms", x.p99_ms + " ms", x.errors_5xx];
    }));
  } catch (e) { fail("#sy-kpis", e); }
  async function loadBackups() {
    try {
      var b = await api("/api/system/backups");
      $("#bk-list").innerHTML = '<p class="small muted">Directory <code>' + esc(b.directory) + "</code> · keeping the last " + b.retention + "</p>" +
        tbl(["File", "Size", "Created"], b.backups.map(function (x) { return ['<span class="mono">' + esc(x.file) + "</span>", (x.size_bytes / 1024).toFixed(1) + " KB", dt(x.created_at)]; }));
    } catch (e) { fail("#bk-list", e); }
  }
  async function loadNotif() {
    try {
      var s = await api("/api/admin/notifications/stats");
      var rows = Object.keys(s.by_channel).map(function (ch) {
        var x = s.by_channel[ch];
        return [esc(ch), x.sent || 0, x.pending || 0, x.failed ? pill(x.failed, "red") : 0];
      });
      $("#sy-notif").innerHTML = tbl(["Channel", "Sent", "Queued", "Failed"], rows);
    } catch (e) { fail("#sy-notif", e); }
  }
  $("#bk-now").addEventListener("click", function () { mutate("/api/system/backups", "POST", null, "Backup created and verified", loadBackups); });
  $("#sy-scan").addEventListener("click", function () { mutate("/api/system/expiry-scan", "POST", null, "Expiry scan complete", loadNotif); });
  $("#sy-disp").addEventListener("click", function () { mutate("/api/system/dispatch-notifications", "POST", null, "Queue processed", loadNotif); });
  loadBackups(); loadNotif();
};

/* ---------------- my account: profile, security, notifications ---------------- */

VIEWS.account = async function () {
  $("#content").innerHTML = '<div class="row">' +
    '<div class="card" style="flex:1;min-width:280px"><h3>Profile</h3><form id="pf-form"><div class="field"><label>Full name</label><input name="full_name"></div>' +
    '<div class="field"><label>Mobile (for SMS alerts)</label><input name="phone_number" placeholder="+260971234567"></div><button class="btn gold sm">Save</button></form>' +
    '<h4 style="margin-top:18px">Notify me by</h4><div id="np-list"></div></div>' +
    '<div class="card" style="flex:1;min-width:280px"><h3>Password</h3><form id="pw-form"><div class="field"><label>Current password</label><input type="password" name="current_password" autocomplete="current-password" required></div>' +
    '<div class="field"><label>New password</label><input type="password" name="new_password" autocomplete="new-password" required></div>' +
    '<button class="btn gold sm">Change password</button></form><p class="small muted">Changing it signs out your other sessions.</p>' +
    '<h4 style="margin-top:18px">Two-factor authentication</h4><div id="mfa-box"></div></div></div>' +
    '<div class="card"><h3>Active sessions</h3><div id="ss-list"></div></div>' +
    '<div class="card"><h3>Devices</h3><div id="dv-list"></div></div>';

  async function loadProfile() {
    try {
      var p = await api("/api/citizen/profile");
      $("#pf-form").elements.full_name.value = p.full_name || "";
      $("#pf-form").elements.phone_number.value = p.phone_number || "";
      renderMfa(p.mfa_enabled);
    } catch (e) { toast(e.message, "err"); }
  }
  function renderMfa(on) {
    var box = $("#mfa-box");
    if (on) {
      box.innerHTML = '<p>' + pill("enabled", "green") + '</p><form id="mfa-off"><div class="field"><input type="password" name="password" placeholder="Password" required></div>' +
        '<div class="field"><input name="code" placeholder="Authenticator or recovery code" required></div><button class="btn sm">Turn off</button></form>';
      $("#mfa-off").addEventListener("submit", function (e) {
        e.preventDefault();
        mutate("/api/auth/mfa/disable", "POST", formData(e.target), "Two-factor authentication turned off", loadProfile);
      });
    } else {
      box.innerHTML = '<p>' + pill("off", "gray") + ' Add a second step to sign-in using an authenticator app.</p><button class="btn gold sm" id="mfa-start">Set up</button><div id="mfa-setup"></div>';
      $("#mfa-start").addEventListener("click", async function () {
        try {
          var s = await api("/api/auth/mfa/setup", { method: "POST" });
          $("#mfa-setup").innerHTML = '<p class="small">In your authenticator app choose "enter a setup key" and paste:</p>' +
            '<pre class="mono" style="white-space:pre-wrap;word-break:break-all">' + esc(s.secret) + '</pre><p class="small muted">Or open this link on your phone:<br><span class="mono" style="word-break:break-all">' + esc(s.otpauth_uri) + "</span></p>" +
            '<form id="mfa-on"><div class="field"><input name="code" inputmode="numeric" placeholder="6-digit code" required></div><button class="btn gold sm">Confirm &amp; enable</button></form>';
          $("#mfa-on").addEventListener("submit", async function (e) {
            e.preventDefault();
            var r = await mutate("/api/auth/mfa/enable", "POST", formData(e.target), "Two-factor authentication enabled");
            if (r) {
              $("#mfa-box").innerHTML = "<p>" + pill("enabled", "green") + '</p><p class="small"><b>Save these recovery codes</b> — each works once if you lose your phone. They will not be shown again.</p>' +
                '<pre class="mono">' + r.recovery_codes.map(esc).join("\n") + "</pre>";
            }
          });
        } catch (e) { toast(e.message, "err"); }
      });
    }
  }
  async function loadPrefs() {
    try {
      var p = await api("/api/notifications/preferences");
      $("#np-list").innerHTML = Object.keys(p).map(function (ch) {
        return '<label style="display:block;margin:6px 0"><input type="checkbox" class="np" data-ch="' + ch + '"' + (p[ch] ? " checked" : "") + (ch === "in_app" ? " disabled" : "") + "> " +
          esc(ch === "in_app" ? "In-app (always on)" : ch.toUpperCase()) + "</label>";
      }).join("");
      $$("#np-list .np").forEach(function (c) {
        c.addEventListener("change", function () { var b = {}; b[c.dataset.ch] = c.checked; mutate("/api/notifications/preferences", "PUT", b, "Preference saved"); });
      });
    } catch (e) { fail("#np-list", e); }
  }
  async function loadSessions() {
    try {
      var s = await api("/api/auth/sessions");
      $("#ss-list").innerHTML = tbl(["Device / browser", "IP", "Last active", ""], s.map(function (x) {
        return ['<span class="small">' + esc((x.user_agent || "unknown").slice(0, 70)) + "</span>" + (x.is_current ? " " + pill("this session", "green") : ""),
          esc(x.ip_address || ""), dt(x.last_seen_at),
          x.is_current ? "" : '<button class="btn ghost sm rv" data-id="' + esc(x.id) + '">Sign out</button>'];
      })) + (s.length > 1 ? '<button class="btn sm" id="rv-all" style="margin-top:10px">Sign out all other sessions</button>' : "");
      $$("#ss-list .rv").forEach(function (b) { b.addEventListener("click", function () { mutate("/api/auth/sessions/" + b.dataset.id, "DELETE", null, "Session ended", loadSessions); }); });
      var all = $("#rv-all");
      if (all) all.addEventListener("click", function () { mutate("/api/auth/sessions/revoke-others", "POST", null, "Other sessions signed out", loadSessions); });
    } catch (e) { fail("#ss-list", e); }
  }
  async function loadDevices() {
    try {
      var d = await api("/api/auth/devices");
      $("#dv-list").innerHTML = tbl(["Device", "Last IP", "Sign-ins", "Last seen", "Status", ""], d.map(function (x) {
        return [esc(x.label || "Unknown"), esc(x.last_ip || ""), x.login_count, dt(x.last_seen_at),
          x.is_blocked ? pill("blocked", "red") : x.is_trusted ? pill("trusted", "green") : pill("known", "gray"),
          '<button class="btn ghost sm dv" data-a="trust" data-id="' + esc(x.id) + '" data-v="' + !x.is_trusted + '">' + (x.is_trusted ? "Untrust" : "Trust") + "</button> " +
          '<button class="btn ghost sm dv" data-a="block" data-id="' + esc(x.id) + '" data-v="' + !x.is_blocked + '">' + (x.is_blocked ? "Unblock" : "Block") + "</button>"];
      }));
      $$("#dv-list .dv").forEach(function (b) {
        b.addEventListener("click", function () {
          mutate("/api/auth/devices/" + b.dataset.id + "?" + (b.dataset.a === "trust" ? "trusted=" : "blocked=") + b.dataset.v, "PATCH", null, "Device updated", loadDevices);
        });
      });
    } catch (e) { fail("#dv-list", e); }
  }
  $("#pf-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var b = {};
    $$("input", e.target).forEach(function (i) { b[i.name] = i.value; });
    mutate("/api/citizen/profile", "PATCH", b, "Profile saved", function () { USER.full_name = b.full_name; renderNav(); });
  });
  $("#pw-form").addEventListener("submit", function (e) {
    e.preventDefault();
    mutate("/api/auth/change-password", "POST", formData(e.target), "Password changed", function () { e.target.reset(); loadSessions(); });
  });
  loadProfile(); loadPrefs(); loadSessions(); loadDevices();
};

/* ---------------- citizen: vehicles, applications, receipts ---------------- */

var baseVehicles = VIEWS.vehicles;
VIEWS.vehicles = async function () {
  if (USER.role !== "citizen") return baseVehicles(); // staff keep the register view from app.js
  $("#content").innerHTML = '<div id="cv-sum"></div><div id="cv-list"><div class="empty">Loading…</div></div>';
  try {
    var s = await api("/api/citizen/summary");
    $("#cv-sum").innerHTML = '<div class="grid cards">' + kpi("Vehicles", s.vehicles) + kpi("Amount due", money(s.amount_due), s.unpaid_fines + " unpaid" + (s.overdue_fines ? ", " + s.overdue_fines + " overdue" : "")) +
      kpi("Open applications", s.open_applications) +
      kpi("Expiring within 60 days", s.expiring_soon.length ? s.expiring_soon.map(function (x) { return esc(x.type + " " + x.ref) + " — " + (x.days_left < 0 ? "expired" : x.days_left + "d"); }).join("<br>") : "Nothing") + "</div>";
    var vs = await api("/api/citizen/my-vehicles");
    if (!vs.length) { $("#cv-list").innerHTML = '<div class="card"><div class="empty">No vehicles are linked to your account yet.</div></div>'; return; }
    var details = await Promise.all(vs.map(function (v) { return api("/api/citizen/vehicles/" + v.id); }));
    $("#cv-list").innerHTML = details.map(function (d) {
      var v = d.vehicle;
      return '<div class="card"><h3>' + esc(v.registration_number) + " " + pill(d.compliant ? "compliant" : "action needed", d.compliant ? "green" : "amber") + "</h3>" +
        '<p class="muted">' + esc(v.year + " " + v.make + " " + v.model) + "</p>" +
        tbl(["Check", "Result", "Detail"], d.checks.map(function (c) {
          return [esc(c.check.replace(/_/g, " ")), pill(c.status, c.status === "pass" ? "green" : "red"), '<span class="small">' + esc(c.detail || "") + "</span>"];
        })) + "</div>";
    }).join("");
  } catch (e) { fail("#cv-list", e); }
};

VIEWS.applications = async function () {
  $("#content").innerHTML = '<div id="ap-list"><div class="empty">Loading…</div></div>';
  try {
    var apps = await api("/api/citizen/applications");
    if (!apps.length) { $("#ap-list").innerHTML = '<div class="card"><div class="empty">You have not applied for anything yet.</div></div>'; return; }
    $("#ap-list").innerHTML = apps.map(function (a) {
      return '<div class="card"><h3>Class ' + esc(a.requested_class) + " licence · " + esc(a.reference) + "</h3>" +
        '<div class="steps">' + a.steps.map(function (st) {
          return '<div class="step ' + st.state + '"><span class="dot"></span>' + esc(st.label) + "</div>";
        }).join("") + "</div>" +
        (a.next_step ? '<p><b>Next:</b> ' + esc(a.next_step) + "</p>" : "") +
        '<p class="small muted">Submitted ' + dt(a.submitted_at) + " · updated " + dt(a.updated_at) + "</p>" +
        (a.theory_score != null ? '<p class="small">Theory score: ' + a.theory_score + (a.practical_score != null ? " · Practical: " + a.practical_score : "") + "</p>" : "") +
        (a.issued_licence_number ? '<p>Licence number: <b class="mono">' + esc(a.issued_licence_number) + "</b></p>" : "") + "</div>";
    }).join("");
  } catch (e) { fail("#ap-list", e); }
};

VIEWS.receipts = async function () {
  $("#content").innerHTML = '<div class="card"><h3>Payment history</h3><div id="rc-list"><div class="empty">Loading…</div></div></div>';
  try {
    var items = await api("/api/citizen/payments?limit=100");
    $("#rc-list").innerHTML = tbl(["Date", "Description", "Amount", "Status", "Receipt", ""], items.map(function (p) {
      var cls = p.status === "completed" ? "green" : p.status === "failed" ? "red" : p.status === "refunded" ? "amber" : "gray";
      return [dt(p.paid_at || p.created_at), esc(p.description || p.payment_type), money(p.amount), pill(p.status, cls), esc(p.receipt_number || "—"),
        p.receipt_number ? '<button class="btn ghost sm rc" data-id="' + esc(p.id) + '">Download PDF</button>' : ""];
    }));
    $$("#rc-list .rc").forEach(function (b) {
      b.addEventListener("click", function () { download("/api/payments/" + b.dataset.id + "/receipt.pdf", "receipt.pdf").catch(function (e) { toast(e.message, "err"); }); });
    });
  } catch (e) { fail("#rc-list", e); }
};

/* ---------------- toll gate: read-only lookup (officer / toll operator / admin) ---------------- */

var baseToll = VIEWS.toll;
VIEWS.toll = async function () {
  await baseToll();
  var card = document.createElement("div");
  card.className = "card";
  card.innerHTML = '<h3>Look up a vehicle</h3><p class="small muted">Read-only: shows pending offences without recording a toll event or issuing a fine.</p>' +
    '<form id="lk-form" class="toolbar"><input id="lk-plate" placeholder="Plate, e.g. BAL 5678" autocomplete="off" required>' +
    '<button class="btn gold sm">Look up</button></form><div id="lk-out"></div>';
  $("#content").insertBefore(card, $("#content").firstChild);

  async function lookup(plate) {
    var out = $("#lk-out");
    out.innerHTML = '<div class="empty">Looking up…</div>';
    try {
      var r = await api("/api/toll/lookup/" + encodeURIComponent(plate));
      if (!r.found) { out.innerHTML = '<div class="card" style="box-shadow:none"><h3>' + pill("STOP", "red") + " " + esc(r.plate) + "</h3><p>" + esc(r.reasons[0]) + "</p></div>"; return; }
      var v = r.vehicle, ok = r.decision === "allow";
      var html = '<div class="card" style="box-shadow:none;margin:0"><h3>' + pill(ok ? "CLEAR TO PASS" : "STOP", ok ? "green" : "red") + " " + esc(r.plate) + "</h3>" +
        '<p class="muted">' + esc(v.year + " " + v.make + " " + v.model + (v.colour ? " · " + v.colour : "")) + " — owner: <b>" + esc(r.owner) + "</b></p>";
      if (r.reasons.length) html += "<ul>" + r.reasons.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>";
      if (r.licence) html += '<p class="small">Owner licence ' + esc(r.licence.licence_number) + " (class " + esc(r.licence.class) + "): " + pill(r.licence.valid ? "valid" : r.licence.status, r.licence.valid ? "green" : "red") + " expires " + dt(r.licence.expires) + "</p>";
      if (r.offences.length) {
        html += "<h4>Pending offences — " + r.offence_count + ", total " + money(r.total_owed) + (r.overdue_count ? " (" + r.overdue_count + " overdue)" : "") + "</h4>" +
          tbl(["Reference", "Offence", "Where", "Amount", "Due", ""], r.offences.map(function (o) {
            return ['<span class="mono">' + esc(o.reference) + "</span>", esc(o.offence.replace(/_/g, " ")), esc(o.location || "—"), money(o.amount),
              dt(o.due_date) + (o.overdue ? " " + pill("overdue", "red") : ""),
              '<button class="btn gold sm lk-pay" data-id="' + esc(o.challan_id) + '" data-ref="' + esc(o.reference) + '">Record payment</button>'];
          }));
      } else html += '<p class="muted">No pending offences.</p>';
      out.innerHTML = html + "</div>";
      $$("#lk-out .lk-pay").forEach(function (b) {
        b.addEventListener("click", function () {
          if (!confirm("Record payment received for " + b.dataset.ref + "?")) return;
          mutate("/api/enforcement/challans/" + b.dataset.id + "/pay", "POST", null, b.dataset.ref + " paid", function () { lookup(plate); });
        });
      });
    } catch (e) { fail("#lk-out", e); }
  }
  $("#lk-form").addEventListener("submit", function (e) { e.preventDefault(); lookup($("#lk-plate").value.trim()); });
};
