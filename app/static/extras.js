"use strict";

/* Officer screens for PSV management (operators, permits, route corridors, route alerts)
   and insurance policies. Loaded after platform.js; reuses its helpers. */

(function extendNav() {
  [["admin"], ["officer"]].forEach(function (r) {
    NAV[r[0]] = (NAV[r[0]] || []).concat([
      { id: "psv", label: "PSV & routes" },
      { id: "insurance", label: "Insurance" },
      { id: "insights", label: "Insights" },
      { id: "bi", label: "Analytics (BI)" }
    ]);
  });
  // The administrator sees every module an officer does, plus the admin ones.
  var have = {};
  NAV.admin.forEach(function (x) { have[x.id] = 1; });
  NAV.officer.forEach(function (x) { if (!have[x.id] && x.id !== "account" && x.id !== "reports") NAV.admin.push(x); });
  NAV.citizen.push({ id: "services", label: "Pay for services" });

  var GROUPS = [
    ["Overview", ["dashboard", "bi", "insights", "reports"]],
    ["Vehicles and drivers", ["vehicles", "drivers", "licensing", "inspections", "insurance", "psv"]],
    ["Enforcement", ["violations", "challans", "driver_reports", "anpr", "toll", "accidents"]],
    ["Roads", ["alerts", "planner"]],
    ["Money and system", ["payments", "integrations", "users", "rules", "settings", "audits", "system", "account"]]
  ];
  var CITIZEN_GROUPS = [
    ["Overview", ["dashboard"]],
    ["Pay and renew", ["fines", "services", "licence", "vehicles", "applications", "receipts"]],
    ["Report", ["report", "report_driver", "alerts", "planner"]],
    ["Account", ["account"]]
  ];
  function regroup(role, groups) {
    var items = NAV[role], out = [], seen = {};
    groups.forEach(function (g) {
      g[1].forEach(function (id) {
        var it = items.filter(function (x) { return x.id === id; })[0];
        if (it && !seen[id]) { seen[id] = 1; it.group = g[0]; out.push(it); }
      });
    });
    items.forEach(function (it) { if (!seen[it.id]) { it.group = it.group || "More"; out.push(it); } });
    NAV[role] = out;
  }
  regroup("admin", GROUPS);
  regroup("officer", GROUPS);
  regroup("toll_operator", GROUPS);
  regroup("citizen", CITIZEN_GROUPS);
})();

function xPlateLookup(plate) {
  return api("/api/vehicles/by-registration/" + encodeURIComponent(plate.trim()));
}

/* ---------------- PSV ---------------- */

VIEWS.psv = async function () {
  var isStaff = USER.role === "officer" || USER.role === "admin";
  $("#content").innerHTML =
    '<div class="card"><h3>Route alerts</h3><div id="psv-alerts"><div class="empty">Loading…</div></div></div>' +
    '<div class="row">' +
      '<div class="card"><h3>Register PSV operator</h3><form id="psv-op-form">' +
        '<div class="field"><label>Company name</label><input name="name" required></div>' +
        '<div class="field"><label>Operator licence number</label><input name="licence_number" required></div>' +
        '<div class="field"><label>Contact person</label><input name="contact_person" required></div>' +
        '<div class="field"><label>Phone</label><input name="phone" required></div>' +
        '<div class="field"><label>Email (optional)</label><input name="email" type="email"></div>' +
        '<button class="btn gold" type="submit">Register operator</button></form></div>' +
      '<div class="card"><h3>Issue PSV permit</h3><form id="psv-permit-form">' +
        '<div class="field"><label>Operator</label><select name="operator_id" id="psv-op-select" required></select></div>' +
        '<div class="field"><label>Vehicle plate</label><input name="plate" placeholder="e.g. BAK 123" required></div>' +
        '<div class="field"><label>Permit number</label><input name="permit_number" required></div>' +
        '<div class="field"><label>Route</label><input name="route" placeholder="e.g. Lusaka to Kitwe" required></div>' +
        '<div class="field"><label>Issued</label><input name="issued_date" type="datetime-local" required></div>' +
        '<div class="field"><label>Expires</label><input name="expiry_date" type="datetime-local" required></div>' +
        '<button class="btn gold" type="submit">Issue permit</button></form></div>' +
    "</div>" +
    '<div class="card"><h3>Permits and route corridors</h3><div id="psv-permits"><div class="empty">Loading…</div></div>' +
      '<div id="psv-corridor" style="display:none;margin-top:16px;border-top:1px solid #ddd;padding-top:16px;">' +
        '<h4 id="psv-corridor-title" style="margin:0 0 8px;"></h4>' +
        '<div class="field"><label>Route points, one per line as <span class="mono">latitude, longitude</span> (at least 2, in order)</label>' +
        '<textarea id="psv-waypoints" rows="5" placeholder="-15.4167, 28.2833&#10;-12.8000, 28.2000"></textarea></div>' +
        '<div class="field"><label>Allowed distance from the route (metres)</label><input id="psv-buffer" type="number" value="500" min="50" max="20000"></div>' +
        '<button class="btn gold" id="psv-corridor-save" type="button">Save route</button> ' +
        '<button class="btn ghost" id="psv-corridor-cancel" type="button">Close</button></div></div>' +
    '<div class="card"><h3>Report a vehicle position</h3>' +
      '<p class="small muted" style="margin:0 0 8px;">Simulates a tracker or roadside sighting. A position outside the route raises an alert for officers.</p>' +
      '<form id="psv-pos-form" class="toolbar">' +
        '<input name="plate_number" placeholder="Plate" required>' +
        '<input name="latitude" type="number" step="any" placeholder="Latitude" required>' +
        '<input name="longitude" type="number" step="any" placeholder="Longitude" required>' +
        '<button class="btn gold" type="submit">Report position</button></form>' +
      '<div id="psv-pos-result" class="small" style="margin-top:10px;"></div></div>';

  var operators = [], permitId = null;

  async function loadOperators() {
    try {
      operators = await api("/api/psv/operators");
      $("#psv-op-select").innerHTML = operators.length
        ? operators.map(function (o) { return '<option value="' + esc(o.id) + '">' + esc(o.name) + " (" + esc(o.licence_number) + ")</option>"; }).join("")
        : '<option value="">Register an operator first</option>';
    } catch (e) { toast(e.message, "err"); }
  }

  async function loadPermits() {
    var box = $("#psv-permits");
    try {
      var items = await api("/api/psv/permits");
      var vs = {};
      try { (await api("/api/vehicles?limit=200")).forEach(function (v) { vs[v.id] = v.registration_number; }); } catch (e) { /* plates optional */ }
      box.innerHTML = tbl(["Permit", "Vehicle", "Route", "Expires", "Status", ""], items.map(function (p) {
        return ["<b>" + esc(p.permit_number) + "</b>", '<span class="mono">' + esc(vs[p.vehicle_id] || short(p.vehicle_id)) + "</span>", esc(p.route), dday(p.expiry_date),
          pill(p.status, p.status === "active" ? "green" : "gray"),
          isStaff ? '<button class="btn ghost sm psv-route" data-id="' + esc(p.id) + '" data-name="' + esc(p.permit_number) + '">Set route</button>' : ""];
      }));
      $$("#psv-permits .psv-route").forEach(function (b) {
        b.addEventListener("click", function () { openCorridor(b.dataset.id, b.dataset.name); });
      });
    } catch (e) { fail("#psv-permits", e); }
  }

  async function openCorridor(id, name) {
    permitId = id;
    $("#psv-corridor").style.display = "";
    $("#psv-corridor-title").textContent = "Route for permit " + name;
    $("#psv-waypoints").value = ""; $("#psv-buffer").value = 500;
    try {
      var c = await api("/api/psv/permits/" + id + "/corridor");
      $("#psv-waypoints").value = c.waypoints.map(function (w) { return w.lat + ", " + w.lon; }).join("\n");
      $("#psv-buffer").value = c.buffer_m;
    } catch (e) { /* no route yet */ }
    $("#psv-corridor").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function loadAlerts() {
    var box = $("#psv-alerts");
    try {
      var items = await api("/api/psv/alerts");
      var vs = {};
      try { (await api("/api/vehicles?limit=200")).forEach(function (v) { vs[v.id] = v.registration_number; }); } catch (e) { /* optional */ }
      box.innerHTML = tbl(["When", "Vehicle", "Off route by", "Position", "Status", ""], items.map(function (a) {
        return [dt(a.occurred_at), '<span class="mono"><b>' + esc(vs[a.vehicle_id] || short(a.vehicle_id)) + "</b></span>",
          a.distance_m + " m (allowed " + a.buffer_m + " m)", '<span class="small mono">' + a.latitude.toFixed(4) + ", " + a.longitude.toFixed(4) + "</span>",
          a.acknowledged ? pill("seen", "gray") : pill("new", "red"),
          !a.acknowledged && isStaff ? '<button class="btn gold sm psv-ack" data-id="' + esc(a.id) + '">Acknowledge</button>' : ""];
      }));
      $$("#psv-alerts .psv-ack").forEach(function (b) {
        b.addEventListener("click", function () { mutate("/api/psv/alerts/" + b.dataset.id + "/acknowledge", "POST", null, "Alert acknowledged", loadAlerts); });
      });
    } catch (e) { fail("#psv-alerts", e); }
  }

  $("#psv-op-form").addEventListener("submit", function (e) {
    e.preventDefault();
    mutate("/api/psv/operators", "POST", formData(e.target), "Operator registered", function () { e.target.reset(); loadOperators(); });
  });

  $("#psv-permit-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var d = formData(e.target);
    try {
      var v = await xPlateLookup(d.plate);
      delete d.plate; d.vehicle_id = v.id;
      await api("/api/psv/permits", { method: "POST", body: JSON.stringify(d) });
      toast("Permit issued", "ok"); e.target.reset(); loadPermits();
    } catch (err) { toast(err.message, "err"); }
  });

  $("#psv-corridor-save").addEventListener("click", function () {
    var pts = $("#psv-waypoints").value.split("\n").map(function (l) { return l.trim(); }).filter(Boolean).map(function (l) {
      var p = l.split(/[ ,;]+/); return { lat: parseFloat(p[0]), lon: parseFloat(p[1]) };
    });
    if (pts.length < 2 || pts.some(function (p) { return isNaN(p.lat) || isNaN(p.lon); })) { toast("Enter at least two points as latitude, longitude", "err"); return; }
    mutate("/api/psv/permits/" + permitId + "/corridor", "PUT", { waypoints: pts, buffer_m: parseInt($("#psv-buffer").value, 10) || 500 }, "Route saved", function () { $("#psv-corridor").style.display = "none"; });
  });
  $("#psv-corridor-cancel").addEventListener("click", function () { $("#psv-corridor").style.display = "none"; });

  $("#psv-pos-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var d = formData(e.target); d.latitude = parseFloat(d.latitude); d.longitude = parseFloat(d.longitude);
    var out = $("#psv-pos-result");
    try {
      var r = await api("/api/psv/positions", { method: "POST", body: JSON.stringify(d) });
      out.innerHTML = r.inside
        ? '<span class="pill green">On route</span> ' + esc(r.plate_number) + " is " + r.distance_m + " m from the route (allowed " + r.buffer_m + " m)."
        : '<span class="pill red">Off route</span> ' + esc(r.plate_number) + " is " + r.distance_m + " m away (allowed " + r.buffer_m + " m)." + (r.alert_deduplicated ? " An alert was already raised recently." : " Alert raised.");
      loadAlerts();
    } catch (err) { out.innerHTML = '<div class="error-box">' + esc(err.message) + "</div>"; }
  });

  loadOperators(); loadPermits(); loadAlerts();
};

/* ---------------- Insurance ---------------- */

VIEWS.insurance = async function () {
  var types = [];
  try { types = await api("/api/insurance/policy-types"); } catch (e) { /* the form still works with the default */ }
  var byId = {}; types.forEach(function (t) { byId[t.id] = t; });
  $("#content").innerHTML =
    '<div class="card"><h3>What insurance does in this system</h3>' +
      '<p style="margin:0 0 8px">Insurers sell the cover; RTSA does not. Officers record each policy here so the system can check it.</p>' +
      '<ul class="small" style="margin:0;padding-left:18px;line-height:1.7">' +
        '<li>An active policy is one of the checks at the roadside and at toll gates. No active policy means the vehicle is flagged and a "no insurance" fine can follow.</li>' +
        '<li>Owners get reminders before a policy ends (30, 14, 7 and 1 days).</li>' +
        '<li>A PSV permit can only be renewed online while the vehicle has an active passenger-liability policy.</li></ul></div>' +
    '<div class="row">' +
      '<div class="card"><h3>Record a policy</h3>' +
        '<div class="field"><label>Vehicle plate</label><div class="toolbar" style="margin-bottom:0;"><input id="ins-plate" placeholder="e.g. BAK 123">' +
        '<button class="btn ghost sm" id="ins-go" type="button">Look up</button></div>' +
        '<div class="small muted" id="ins-vehicle" style="margin-top:6px;"></div></div>' +
        '<form id="ins-form" style="display:none;">' +
          '<div class="field"><label>Policy type</label><select name="policy_type" id="ins-type">' +
            types.map(function (t) { return '<option value="' + esc(t.id) + '">' + esc(t.label) + "</option>"; }).join("") + "</select>" +
            '<p class="small muted" id="ins-type-help" style="margin:6px 0 0"></p></div>' +
          '<div class="field"><label>Insurer</label><select name="provider" required><option value="">Choose an insurer</option>' +
            ["ZSIC General Insurance", "Madison General Insurance", "Professional Insurance Corporation Zambia", "Hollard Insurance Zambia", "Mayfair Insurance", "Goldman Insurance", "Other"].map(function (n) { return "<option>" + esc(n) + "</option>"; }).join("") + "</select></div>" +
          '<div class="field"><label>Policy number</label><input name="policy_number" required></div>' +
          '<div class="field"><label>Premium paid (ZMW, optional)</label><input name="premium" type="number" min="0" step="0.01"></div>' +
          '<div class="field"><label>Starts</label><input name="start_date" type="datetime-local" required></div>' +
          '<div class="field"><label>Ends</label><input name="end_date" type="datetime-local" required></div>' +
          '<button class="btn gold" type="submit">Add policy</button></form></div>' +
      '<div class="card"><h3>Policies for this vehicle</h3><div id="ins-list"><div class="empty">Look up a vehicle first.</div></div></div>' +
    "</div>";

  function showHelp() {
    var t = byId[$("#ins-type").value];
    $("#ins-type-help").innerHTML = t ? "<b>Covers:</b> " + esc(t.covers) + "<br><b>When:</b> " + esc(t.required_for) : "";
  }
  if ($("#ins-type")) { $("#ins-type").addEventListener("change", showHelp); showHelp(); }

  var vehicleId = null;
  async function loadPolicies() {
    try {
      var items = await api("/api/insurance/vehicle/" + vehicleId);
      $("#ins-list").innerHTML = tbl(["Type", "Insurer", "Policy", "Premium", "From", "To", "Status"], items.map(function (p) {
        var t = byId[p.policy_type];
        return [esc(t ? t.label : p.policy_type), esc(p.provider), '<span class="mono">' + esc(p.policy_number) + "</span>", p.premium != null ? money(p.premium) : "—", dday(p.start_date), dday(p.end_date), pill(p.is_active ? "active" : "inactive", p.is_active ? "green" : "gray")];
      }));
    } catch (e) { fail("#ins-list", e); }
  }
  $("#ins-go").addEventListener("click", async function () {
    var plate = $("#ins-plate").value.trim(); if (!plate) return;
    try {
      var v = await xPlateLookup(plate); vehicleId = v.id;
      $("#ins-vehicle").innerHTML = "Found <b>" + esc(v.registration_number) + "</b> (" + esc(v.make) + " " + esc(v.model) + "), owner " + esc(v.owner_name);
      $("#ins-form").style.display = ""; loadPolicies();
    } catch (e) {
      vehicleId = null; $("#ins-vehicle").innerHTML = '<span style="color:var(--err);">' + esc(e.message) + "</span>";
      $("#ins-form").style.display = "none"; $("#ins-list").innerHTML = '<div class="empty">Look up a vehicle first.</div>';
    }
  });
  $("#ins-form").addEventListener("submit", async function (e) {
    e.preventDefault(); if (!vehicleId) return;
    var d = formData(e.target); d.vehicle_id = vehicleId;
    if (d.premium === "" || d.premium == null) delete d.premium; else d.premium = Math.round(parseFloat(d.premium) * 100);
    if (d.start_date) d.start_date = new Date(d.start_date).toISOString();
    if (d.end_date) d.end_date = new Date(d.end_date).toISOString();
    mutate("/api/insurance/", "POST", d, "Policy added", function () { e.target.reset(); showHelp(); loadPolicies(); });
  });
};


/* ---------------- Pay for services (citizen) ---------------- */

VIEWS.services = async function () {
  $("#content").innerHTML = '<div class="empty">Loading…</div>';
  try {
    var cat = await api("/api/services/catalogue");
    var vehicles = [];
    try { vehicles = await api("/api/citizen/my-vehicles"); } catch (e) { /* none linked */ }
    var vopts = vehicles.map(function (v) { return '<option value="' + esc(v.id) + '">' + esc(v.registration_number) + " · " + esc(v.make + " " + v.model) + "</option>"; }).join("");
    $("#content").innerHTML = '<div class="grid cards" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))">' + cat.services.map(function (sv) {
      var body = '<p class="muted" style="margin:0 0 12px">' + esc(sv.what) + "</p>";
      if (sv.link) body += '<p><b>' + esc(sv.fee_label) + '</b></p><a class="btn gold" href="#/' + esc(sv.link) + '">Go to My licence</a>';
      else {
        if (sv.needs_vehicle) body += '<div class="field"><label>Vehicle</label><select class="sv-vehicle">' + (vopts || '<option value="">No vehicles linked</option>') + "</select></div>";
        if (sv.id === "fitness_test") body += '<div class="field"><label>Inspection centre</label><select class="sv-centre">' + cat.centres.map(function (c) { return "<option>" + esc(c) + "</option>"; }).join("") + "</select></div>" +
          '<div class="field"><label>Preferred date</label><input type="date" class="sv-date" min="' + new Date().toISOString().slice(0, 10) + '"></div>';
        if (sv.needs_application) body += '<div class="field"><label>Application</label><select class="sv-app">' + (cat.unpaid_applications.length ? cat.unpaid_applications.map(function (a) { return '<option value="' + esc(a.id) + '">' + esc(a.label) + "</option>"; }).join("") : '<option value="">No unpaid applications</option>') + "</select></div>";
        body += '<p><b>' + esc(sv.fee_label) + '</b></p><button class="btn gold sv-pay" data-service="' + esc(sv.id) + '">Pay ' + esc(sv.fee_label) + "</button>";
      }
      return '<div class="card" data-svc="' + esc(sv.id) + '"><h3>' + esc(sv.name) + "</h3>" + body + "</div>";
    }).join("") + "</div>" +
      '<div class="card"><h3>Not paid here</h3>' + cat.not_handled_here.map(function (n) { return "<p style=\"margin:0 0 6px\"><b>" + esc(n.name) + "</b> · " + esc(n.agency) + ". " + esc(n.what) + "</p>"; }).join("") +
      '<p class="small muted" style="margin:10px 0 0">Fees are placeholders until RTSA confirms its schedule. Payments here use the sandbox gateway.</p></div>';
    $$(".sv-pay").forEach(function (b) {
      b.addEventListener("click", async function () {
        var card = b.closest(".card");
        var body = { service: b.dataset.service };
        var sel = card.querySelector(".sv-vehicle"); if (sel) body.vehicle_id = sel.value;
        var ap = card.querySelector(".sv-app"); if (ap) body.application_id = ap.value;
        var c = card.querySelector(".sv-centre"); if (c) body.inspection_centre = c.value;
        var d = card.querySelector(".sv-date"); if (d && d.value) body.preferred_date = new Date(d.value + "T09:00:00").toISOString();
        b.disabled = true;
        try {
          var r = await api("/api/services/pay", { method: "POST", body: JSON.stringify(body) });
          toast(r.message + ". Receipt " + r.receipt_number, "ok");
        } catch (e) { toast(e.message, "err"); }
        b.disabled = false;
      });
    });
  } catch (e) { fail("#content", e); }
};

/* ---------------- Passport, driver profile (shared by Insights and Inspections) ---------------- */

function bandPill(b) { return '<span class="pill ' + (b === "high" ? "red" : b === "medium" ? "amber" : "green") + '">' + esc(b) + " risk</span>"; }

function passportHtml(p) {
  return '<div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center"><div><b class="mono">' + esc(p.plate) + "</b> · " + esc(p.vehicle) +
    '<div class="small muted">Owner: ' + esc(p.owner) + "</div></div><div>" + bandPill(p.risk_band) + ' <b>' + p.risk_score + "</b> pts · " +
    (p.can_operate ? '<span class="pill green">can operate</span>' : '<span class="pill red">not road-legal</span>') + "</div></div>" +
    '<div class="table-wrap" style="margin-top:8px"><table><tbody>' + p.checks.map(function (c) {
      return "<tr><td>" + esc(c.name) + '</td><td><span class="pill ' + (c.ok ? "green" : "red") + '">' + (c.ok ? "OK" : "Problem") + '</span></td><td class="small muted">' + esc(c.detail) + "</td></tr>";
    }).join("") + "</tbody></table></div>" +
    (p.factors.length ? '<div class="small" style="margin-top:8px"><b>Why this score:</b> ' + p.factors.map(function (f) { return esc(f.why) + " (+" + f.points + ")"; }).join(", ") + "</div>" : "") +
    '<div class="small" style="margin-top:8px"><b>Next inspection:</b> every ' + p.recommended_interval_days + " days for this risk level, due " + new Date(p.recommended_next_inspection).toLocaleDateString() +
    '<div class="muted">' + esc(p.interval_rule) + "</div></div>" +
    '<div style="margin-top:8px"><b class="small">What to do:</b><ul style="margin:4px 0 0 18px">' + p.next_actions.map(function (a) { return "<li>" + esc(a) + "</li>"; }).join("") + "</ul></div>";
}

function driverHtml(d) {
  return '<div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap"><div><b>' + esc(d.name) + '</b> · <span class="mono">' + esc(d.licence) + '</span><div class="small muted">Licence ' + esc(d.status) + "</div></div><div>" + bandPill(d.risk_band) + " · offences " + esc(d.trend) + "</div></div>" +
    '<div class="small" style="margin-top:10px">Demerit points (suspension at ' + d.limit + ')</div>' + meterHtml(d.points, d.limit, { what: "points" }) +
    '<div class="small muted">Offences: ' + d.offences_90d + " in the last 90 days, " + d.offences_prior_90d + " in the 90 before.</div>" +
    (d.by_type.length ? '<div class="small" style="margin-top:6px">' + d.by_type.map(function (t) { return esc(t.type.replace(/_/g, " ")) + " ×" + t.count; }).join(" · ") + "</div>" : "") +
    '<div style="margin-top:8px"><b class="small">What to do:</b><ul style="margin:4px 0 0 18px">' + d.advice.map(function (a) { return "<li>" + esc(a) + "</li>"; }).join("") + "</ul></div>";
}

/* ---------------- Plate scanner widget (ANPR and Insights) ---------------- */

var SCAN_N = 0;
function plateScanHtml() {
  var id = "scan" + (++SCAN_N);
  var cam = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>';
  return '<div class="scan-box" data-scan="' + id + '"><label class="btn ghost scan-btn" for="' + id + '-file">' + cam + ' Scan plate with camera</label>' +
    '<input id="' + id + '-file" type="file" accept="image/*" capture="environment" style="display:none">' +
    '<div class="small muted scan-status" hidden></div><div class="scan-out"></div><div class="scan-info"></div></div>';
}

function wirePlateScan(root, onPick) {
  var box = root.querySelector(".scan-box"); if (!box) return;
  var input = box.querySelector("input[type=file]"), status = box.querySelector(".scan-status"), out = box.querySelector(".scan-out"), info = box.querySelector(".scan-info");
  async function lookup(plate, conf) {
    info.innerHTML = '<div class="small muted">Looking up ' + esc(plate) + "…</div>";
    try {
      var v = await api("/api/vehicles/by-registration/" + encodeURIComponent(plate));
      var html;
      try { html = passportHtml(await api("/api/insights/vehicle/" + v.id)); }
      catch (e2) { html = '<b class="mono">' + esc(v.registration_number || plate) + "</b> · " + esc((v.make || "") + " " + (v.model || "") + " " + (v.year || "")) + '<div class="small muted">Owner: ' + esc(v.owner_name || "-") + "</div>"; }
      info.innerHTML = '<div class="scan-found">' + html + "</div>";
      onPick(plate, conf);
      info.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (e) {
      info.innerHTML = '<div class="scan-found"><b class="mono">' + esc(plate) + '</b><div class="small" style="color:var(--err)">Not found in the register. Check the letters, or pick another reading above.</div></div>';
      onPick(plate, conf);
    }
  }
  input.addEventListener("change", async function () {
    var file = input.files && input.files[0]; if (!file) return;
    out.innerHTML = ""; info.innerHTML = ""; status.hidden = false; status.textContent = "Loading the scanner…";
    try {
      var res = await PlateScan.scan(file, function (s, pr) { status.textContent = "Reading the plate: " + s + (pr ? " " + Math.round(pr * 100) + "%" : ""); });
      status.textContent = res.candidates.length ? "Is this the plate? Check the letters against the real plate, then pick it." : "Could not read a plate. Retake it closer and straight-on, or type it in.";
      out.innerHTML = '<div class="scan-result"><img alt="Photo you took" src="' + res.preview + '">' + '<div class="scan-chips">' +
        res.candidates.slice(0, 4).map(function (c) { return '<button type="button" class="btn gold sm scan-pick" data-p="' + esc(c.plate) + '" data-c="' + c.confidence + '">' + esc(c.plate) + ' <span class="small">(' + c.confidence + '%)</span></button>'; }).join("") + "</div></div>";
      $$(".scan-pick", out).forEach(function (b) { b.addEventListener("click", function () { status.textContent = "Showing " + b.dataset.p + ". Pick another reading if the letters are wrong."; lookup(b.dataset.p, Number(b.dataset.c)); }); });
      if (res.candidates.length) { status.textContent = "Showing the best reading. Pick another below if the letters are wrong."; lookup(res.candidates[0].plate, res.candidates[0].confidence); }
    } catch (e) { status.textContent = "Scanner problem: " + e.message; }
    input.value = "";
  });
}

/* ---------------- Insights (staff) ---------------- */

VIEWS.insights = async function () {
  $("#content").innerHTML = '<div class="empty">Loading…</div>';
  try {
    var d = await api("/api/insights/");
    var gaps = await api("/api/insights/insurance-gaps");
    var fac = await api("/api/insights/accident-factors");
    var bars = hbars(d.accident_hotspots.map(function (h) { return { name: h.location, weight: h.weight, accidents: h.accidents }; }), "weight", function (i) { return i.accidents + " accidents"; }, "", "No accidents recorded in the last year.");
    var pipe = Object.keys(d.expiry_pipeline).map(function (k) { var p = d.expiry_pipeline[k]; return [esc(k), p["30"], p["60"], p["90"]]; });
    $("#content").innerHTML =
      '<div class="card"><h3>Look up a vehicle or a driver</h3><div class="toolbar" style="margin-bottom:0"><input id="pp-q" placeholder="Plate (BAK 123) or licence no."><button class="btn gold" id="pp-go">Open passport</button></div>' + plateScanHtml() + '<div id="pp-out" style="margin-top:12px" class="small muted">Shows what is wrong, how risky it is, how often to inspect it, and what to do next.</div></div>' +
      '<div class="grid cards"><div class="kpi"><div class="kpi-label">Fitness fees expected</div><div class="kpi-value">' + money(d.fitness_fees_expected_90d) + '</div><div class="kpi-sub">from certificates ending in 90 days</div></div></div>' +
      '<div class="row">' +
        '<div class="card"><h3>Vehicles to look at first</h3>' +
          tbl(["Plate", "Score", "Why"], d.risky_vehicles.map(function (r) { return ['<span class="mono">' + esc(r.plate) + "</span><div class=\"small muted\">" + esc(r.vehicle) + "</div>", "<b>" + r.score + "</b>", '<span class="small">' + esc(r.why) + "</span>"]; })) +
          '<p class="small muted" style="margin:10px 0 0">How the score works: ' + esc(d.risk_rules) + "</p></div>" +
        '<div class="card"><h3>Accident hotspots, last 12 months</h3>' + bars +
          '<p class="small muted" style="margin:6px 0 0">Fatal counts 10, serious 4, minor 1.</p></div>' +
      "</div>" +
      '<div class="row">' +
        '<div class="card"><h3>What expires soon</h3>' + tbl(["Document", "30 days", "60 days", "90 days"], pipe) +
          '<p class="small muted" style="margin:10px 0 0">Counts are cumulative: the 90-day column includes the first two.</p></div>' +
        '<div class="card"><h3>Where offences happen</h3>' + tbl(["Location", "Offences"], d.offence_hotspots.map(function (h) { return [esc(h.location), h.offences]; })) + "</div>" +
      "</div>" +
      '<div class="row">' +
        '<div class="card"><h3>Insurance: who is on the road without cover</h3><p class="small" style="margin:0 0 8px"><b>' + gaps.uninsured + "</b> of " + gaps.fleet + " active vehicles (" + gaps.uninsured_pct + "%) have no valid policy. " + gaps.uninsured_with_offences + " of them had offences and " + gaps.uninsured_in_crashes + " were in crashes.</p>" +
          tbl(["Plate", "Owner", "Priority"], gaps.worklist.map(function (w) { return ['<span class="mono">' + esc(w.plate) + "</span>", esc(w.owner), '<span class="pill ' + (w.priority === "high" ? "red" : "amber") + '">' + w.priority + "</span>"]; })) +
          (gaps.expiring_30d.length ? '<p class="small" style="margin:10px 0 0"><b>Cover ending within 30 days:</b> ' + gaps.expiring_30d.map(function (x) { return esc(x.plate); }).join(", ") + "</p>" : "") +
          '<p class="small muted" style="margin:8px 0 0">' + esc(gaps.role) + "</p></div>" +
        '<div class="card"><h3>What crashed vehicles have in common</h3>' +
          (fac.crashed_vehicles ? tbl(["Factor", "Crashed", "Whole fleet", "Lift"], fac.factors.map(function (f) { return [esc(f.factor), f.crashed_pct + "%", f.fleet_pct + "%", "<b>" + (f.lift == null ? "-" : f.lift + "×") + "</b>"]; })) : '<div class="empty">No crashes with vehicles recorded yet.</div>') +
          '<p class="small muted" style="margin:8px 0 0">' + esc(fac.reading) + "</p></div>" +
      "</div>";
    wirePlateScan($("#content"), function (plate) { $("#pp-q").value = plate; });
    $("#pp-go").addEventListener("click", async function () {
      var q = $("#pp-q").value.trim(), out = $("#pp-out");
      if (!q) return;
      out.innerHTML = "Looking…";
      try {
        try {
          var v = await api("/api/vehicles/by-registration/" + encodeURIComponent(q));
          out.innerHTML = passportHtml(await api("/api/insights/vehicle/" + v.id));
        } catch (e1) {
          var dr = await api("/api/drivers/by-licence/" + encodeURIComponent(q));
          out.innerHTML = driverHtml(await api("/api/insights/driver/" + dr.id));
        }
      } catch (e) { out.innerHTML = '<span style="color:var(--err)">No vehicle or driver found for "' + esc(q) + '".</span>'; }
    });
  } catch (e) { fail("#content", e); }
};

/* ---------------- theme menu and row layout ---------------- */

(function themeAndLayout() {
  function apply(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("itms-theme", t); } catch (e) { /* private window */ }
    var meta = document.querySelector('meta[name="theme-color"]');
    var bg = { navy: "#0F2238", asphalt: "#16191C", forest: "#0E3B2A", concrete: "#E9ECEA" }[t];
    if (meta && bg) meta.setAttribute("content", bg);
    $$(".theme-picker select").forEach(function (s) { s.value = t; });
  }
  function current() { return document.documentElement.getAttribute("data-theme") || "navy"; }
  var shellSel = $("#theme-select");
  if (shellSel) shellSel.addEventListener("change", function () { apply(shellSel.value); });
  // the sign-in page gets the same picker, top right
  var login = $("#login-screen");
  if (login && shellSel) {
    var lab = document.createElement("label");
    lab.className = "theme-picker floating";
    lab.innerHTML = shellSel.parentNode.innerHTML.replace('id="theme-select"', 'id="theme-select-login"');
    login.appendChild(lab);
    lab.querySelector("select").addEventListener("change", function (e) { apply(e.target.value); });
  }
  // phones: the header picker is hidden, so the menu drawer gets one
  var drawer = $("#mnav");
  if (drawer && shellSel) {
    var dl = document.createElement("label");
    dl.className = "theme-picker";
    dl.innerHTML = shellSel.parentNode.innerHTML.replace('id="theme-select"', 'id="theme-select-menu"');
    drawer.appendChild(dl);
    dl.querySelector("select").addEventListener("change", function (e) { apply(e.target.value); });
  }
  apply(current());

  // Cards in a row sit side by side only when they are close in height.
  // A tall table beside a short chart leaves a hole, so stack those full width.
  function settle() {
    $$("#content .row").forEach(function (row) {
      row.classList.remove("stacked");
      row.style.alignItems = "flex-start"; // measure each card at its own height
      var kids = Array.prototype.filter.call(row.children, function (k) { return k.offsetHeight > 0; });
      var done = function () { row.style.alignItems = ""; };
      if (kids.length < 2) return done();
      var side = kids.some(function (k) { return k.offsetTop !== kids[0].offsetTop; }) === false;
      if (!side) return done(); // already wrapped by width
      var hs = kids.map(function (k) { return k.offsetHeight; });
      var hi = Math.max.apply(null, hs), lo = Math.min.apply(null, hs);
      if (hi - lo > 120 && hi / lo > 1.3) row.classList.add("stacked");
      done();
    });
  }
  // A screen with several forms shows one at a time: pick the action from a list.
  var picked = {};
  function cardTitle(k) { var h = k.querySelector("h3,h4"); return h ? h.textContent.trim() : ""; }
  function pickForms() {
    var c = $("#content"); if (!c) return;
    var cards = $$("#content .card").filter(function (k) {
      return k.querySelector("form") && cardTitle(k) && !k.parentElement.closest(".card") && !k.closest(".modal");
    });
    var old = c.querySelector(".form-picker");
    var sig = cards.map(cardTitle).join("|");
    if (cards.length < 2) { if (old) old.remove(); $$("#content .pk-hide").forEach(function (k) { k.classList.remove("pk-hide"); }); return; }
    if (old && old.dataset.sig === sig) return;
    if (old) old.remove();
    var key = location.hash, titles = cards.map(cardTitle);
    var sel = titles.indexOf(picked[key]) >= 0 ? picked[key] : titles[0];
    var box = document.createElement("div");
    box.className = "form-picker"; box.dataset.sig = sig;
    box.innerHTML = '<label for="form-pick">What do you want to do?</label><select id="form-pick">' +
      titles.map(function (t) { return '<option value="' + esc(t) + '">' + esc(t) + "</option>"; }).join("") + "</select>";
    var anchor = cards[0].closest(".row") || cards[0];
    anchor.parentNode.insertBefore(box, anchor);
    function show(t) {
      picked[key] = t;
      cards.forEach(function (k) { k.classList.toggle("pk-hide", cardTitle(k) !== t); });
      later();
    }
    var s = box.querySelector("select"); s.value = sel; s.addEventListener("change", function () { show(s.value); });
    show(sel);
  }
  var timer = null;
  function later() { clearTimeout(timer); timer = setTimeout(function () { pickForms(); settle(); }, 120); }
  var content = $("#content");
  if (content && window.MutationObserver) new MutationObserver(later).observe(content, { childList: true, subtree: true });
  window.addEventListener("resize", later);
})();



/* ---------------- Money charts: monthly columns, the fines split, fines by offence ---------------- */

function shortMoney(n) { n = n / 100; return n >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(Math.round(n)); }
function niceMax(n) { var p = Math.pow(10, Math.floor(Math.log10(Math.max(n, 1)))), m = n / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p; }
var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Columns for money by month: gridlines, a value axis, the biggest month in the accent, the unfinished month shown as such.
function monthColumns(byMonth) {
  var keys = Object.keys(byMonth).sort();
  if (!keys.length) return '<div class="empty">No fines paid yet</div>';
  var vals = keys.map(function (k) { return byMonth[k]; }), top = niceMax(Math.max.apply(null, vals)), best = vals.indexOf(Math.max.apply(null, vals));
  var now = new Date(), cur = now.getFullYear() + "-" + ("0" + (now.getMonth() + 1)).slice(-2), last = keys.length - 1;
  var cols = keys.map(function (k, n) {
    var v = byMonth[k], part = k === cur, mm = MONTHS[parseInt(k.slice(5), 10) - 1] || k;
    return '<div class="mc-col' + (n === best ? " top" : "") + (part ? " part" : "") + '" tabindex="0" data-tip="' + esc(mm + " " + k.slice(0, 4) + ": " + money(v) + (part ? " so far (month not over)" : "")) + '">' +
      ((n === best || n === last) ? '<span class="mc-v">' + shortMoney(v) + "</span>" : "") +
      '<div class="mc-bar" style="--h:' + Math.max(v > 0 ? 2 : 0, v / top * 100).toFixed(1) + '"></div><span class="mc-n">' + mm + (mm === "Jan" || n === 0 ? "<small>" + k.slice(2, 4) + "</small>" : "") + "</span></div>";
  }).join("");
  return '<div class="mc"><div class="mc-y"><span>' + shortMoney(top) + "</span><span>" + shortMoney(top / 2) + "</span><span>0</span></div><div class=\"mc-plot\">" + cols + "</div></div>" +
    '<div class="small muted" style="margin-top:6px">ZMW paid each month. The dimmer column is a month still in progress.</div>';
}

// One whole split into a few parts: a segmented bar with the amounts in its legend.
function splitBar(parts) {
  var total = parts.reduce(function (a, p) { return a + p.value; }, 0) || 1;
  return '<div class="sp">' + parts.map(function (p) {
    var pct = p.value / total * 100;
    return '<div class="sp-seg ' + p.cls + '" style="flex:' + Math.max(pct, 0.5) + '" tabindex="0" data-tip="' + esc(p.name + ": " + money(p.value) + " (" + Math.round(pct) + "%)") + '">' + (pct >= 12 ? Math.round(pct) + "%" : "") + "</div>";
  }).join("") + '</div><div class="sp-key small">' + parts.map(function (p) {
    return '<span><i class="sp-dot ' + p.cls + '"></i>' + esc(p.name) + ' <b>' + money(p.value) + "</b></span>";
  }).join("") + "</div>";
}

// Fines by offence: bar length is the value issued; the filled part is what came in, the red part is still owed.
function offenceBars(byOffence) {
  var rows = Object.keys(byOffence).map(function (k) { var o = byOffence[k]; return { name: k.replace(/_/g, " "), o: o }; })
    .sort(function (a, b) { return b.o.issued - a.o.issued; });
  var max = Math.max.apply(null, rows.map(function (r) { return r.o.issued; }).concat([1]));
  return '<div class="ob-key small"><span><i class="sp-dot got"></i>collected</span><span><i class="sp-dot owed"></i>still owed</span></div><div class="hb">' + rows.map(function (r) {
    var o = r.o, w = o.issued / max * 100, got = o.issued ? o.collected / o.issued * 100 : 0, owedPct = 100 - got;
    var tip = r.name + ": " + o.count + (o.count === 1 ? " fine" : " fines") + ", issued " + money(o.issued) + ", collected " + money(o.collected) + (o.outstanding ? ", owed " + money(o.outstanding) : "");
    return '<div class="hb-row ob" tabindex="0" data-tip="' + esc(tip) + '"><div class="hb-head"><span class="hb-name">' + esc(r.name) + ' <span class="muted small">· ' + o.count + (o.count === 1 ? ' fine' : ' fines') + '</span></span>' +
      '<span class="hb-val">' + money(o.issued) + (o.outstanding ? ' <b class="owed-t">' + shortMoney(o.outstanding) + " owed</b>" : "") + "</span></div>" +
      '<div class="hb-track"><div class="ob-bar" style="width:' + w.toFixed(1) + '%"><span class="ob-got" style="width:' + got.toFixed(1) + '%"></span><span class="ob-owed" style="width:' + owedPct.toFixed(1) + '%"></span></div></div></div>';
  }).join("") + "</div>";
}

// The admin's fines panel, rebuilt on these.
function finesPanel(f) {
  var otherOwed = Math.max(0, f.outstanding - f.overdue);
  return '<div class="grid cards">' +
      kpi("Fines issued", money(f.issued)) +
      kpi("Fines collected", money(f.collected), f.collection_rate == null ? "" : f.collection_rate + "% of issued") +
      kpi("Still owed", money(f.outstanding), "includes disputed") +
      kpi("Overdue", money(f.overdue), "with late surcharge") +
    "</div>" +
    cars("Where the money issued has gone", splitBar([
      { name: "Collected", value: f.collected, cls: "got" }, { name: "Owed, not yet due", value: otherOwed, cls: "wait" }, { name: "Overdue", value: f.overdue, cls: "owed" }])) +
    '<div class="row">' + cars("Fines paid by month", monthColumns(f.by_month)) + cars("Fines by offence", offenceBars(f.by_offence)) + "</div>";
}

/* ---------------- Analytics (BI): instrument cluster and where/when offences happen ---------------- */

var ZONE_COLOUR = { green: "var(--ok)", amber: "var(--warn)", red: "var(--err)" };
var ZONE_WORD = { green: "On target", amber: "Watch", red: "Critical" };
var ZONE_GLYPH = { green: "●", amber: "▲", red: "■" };

/* ---------------- Shared chart pieces: bars, columns, meter, tooltip ---------------- */

// Horizontal ranked bars. The biggest is drawn in the accent, the rest in a quieter tint, so the eye lands on the top item.
function hbars(items, valueKey, fmt, unit, empty) {
  if (!items.length) return '<div class="empty">' + (empty || "Nothing recorded in this period.") + "</div>";
  var max = Math.max.apply(null, items.map(function (i) { return i[valueKey]; }).concat([1]));
  var lead = items.length < 2 || items[0][valueKey] > items[1][valueKey];  // accent only a clear leader, not a tie
  return '<div class="hb">' + items.map(function (i, n) {
    var v = i[valueKey], pct = Math.max(v > 0 ? 2 : 0, Math.round(v / max * 100)), shown = (fmt ? fmt(i) : v) + (unit || "");
    return '<div class="hb-row' + (n === 0 && lead ? " top" : "") + '" tabindex="0" data-tip="' + esc(i.name + ": " + shown) + '">' +
      '<div class="hb-head"><span class="hb-name">' + esc(i.name) + '</span><span class="hb-val">' + esc(String(shown)) + "</span></div>" +
      '<div class="hb-track"><div class="hb-fill" style="--w:' + pct + '%"></div></div></div>';
  }).join("") + "</div>";
}

// Vertical columns with a baseline; the busiest one is the accent.
function vcols(items, valueKey) {
  var max = Math.max.apply(null, items.map(function (i) { return i[valueKey]; }).concat([1]));
  var best = items.reduce(function (m, i, n) { return i[valueKey] > items[m][valueKey] ? n : m; }, 0);
  var ties = items.filter(function (i) { return i[valueKey] === items[best][valueKey]; }).length > 1;
  if (ties) best = -1;
  return '<div class="vc">' + items.map(function (i, n) {
    var v = i[valueKey], h = Math.max(v > 0 ? 3 : 0, Math.round(v / max * 100));
    return '<div class="vc-col' + (n === best && v > 0 ? " top" : "") + '" tabindex="0" data-tip="' + esc(i.name + ": " + v) + '">' +
      '<span class="vc-v">' + v + '</span><div class="vc-bar" style="--h:' + h + '"></div><span class="vc-n">' + esc(i.name) + "</span></div>";
  }).join("") + "</div>";
}

// A thin meter against a limit, with the limit marked and the state written out (not colour alone).
function meterHtml(value, limit, opts) {
  opts = opts || {};
  var pct = Math.min(100, Math.round(value / Math.max(1, limit) * 100)), lvl = pct >= 75 ? "red" : pct >= 40 ? "amber" : "green";
  return '<div class="mt" data-tip="' + esc(value + " of " + limit + (opts.what ? " " + opts.what : "")) + '"><div class="mt-track"><div class="mt-fill ' + lvl + '" style="--w:' + pct + '%"></div>' +
    '<span class="mt-mark" style="left:75%" title="75% of the limit"></span></div>' +
    '<div class="mt-foot small"><span class="mt-state ' + lvl + '">' + ZONE_GLYPH[lvl] + " " + ZONE_WORD[lvl] + "</span><span class=\"muted\">" + value + " of " + limit + (opts.what ? " " + esc(opts.what) : "") + "</span></div></div>";
}

// One tooltip for every chart mark: hover, or keyboard focus.
(function chartTips() {
  var tip = null;
  function show(el) {
    if (!tip) { tip = document.createElement("div"); tip.className = "chart-tip"; tip.setAttribute("role", "tooltip"); document.body.appendChild(tip); }
    tip.textContent = el.getAttribute("data-tip"); tip.style.display = "block";
    var r = el.getBoundingClientRect(), w = tip.offsetWidth, x = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8);
    tip.style.left = x + "px"; tip.style.top = Math.max(8, r.top - tip.offsetHeight - 8) + "px";
  }
  function hide() { if (tip) tip.style.display = "none"; }
  document.addEventListener("mouseover", function (e) { var el = e.target.closest && e.target.closest("[data-tip]"); if (el) show(el); else hide(); });
  document.addEventListener("focusin", function (e) { var el = e.target.closest && e.target.closest("[data-tip]"); if (el) show(el); });
  document.addEventListener("focusout", hide);
  window.addEventListener("scroll", hide, true);
})();

// A gauge drawn as a ring of dots: the lit dots climb to the value, the dim ones are what is left. State is written out too.
function dialSvg(d) {
  var N = 54, cx = 100, cy = 92, r = 70, start = 135, span = 270;
  var tv = Math.max(0, Math.min(1, d.value / d.max)), lit = Math.round(tv * N), col = ZONE_COLOUR[d.zone];
  var out = '<svg viewBox="0 0 200 172" role="img" aria-label="' + esc(d.label + ": " + d.value + d.unit + ", " + ZONE_WORD[d.zone]) + '" style="width:100%;max-width:240px;display:block;margin:0 auto">';
  for (var i = 0; i < N; i++) {
    var a = (start + span * i / (N - 1)) * Math.PI / 180, x = cx + r * Math.cos(a), y = cy + r * Math.sin(a), on = i < lit;
    out += '<circle class="dot' + (on ? " on" : "") + '" cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="' + (on ? 2.9 : 2.1) + '" style="--i:' + i + '"' + (on ? ' fill="' + col + '"' : ' fill="var(--line-strong)"') + "/>";
  }
  d.zones.forEach(function (z) {  // a small tick on the ring where each zone starts, so the thresholds are visible
    if (z[0] <= 0) return;
    var a = (start + span * (z[0] / d.max)) * Math.PI / 180, p1 = [cx + (r + 8) * Math.cos(a), cy + (r + 8) * Math.sin(a)], p2 = [cx + (r + 13) * Math.cos(a), cy + (r + 13) * Math.sin(a)];
    out += '<line x1="' + p1[0].toFixed(1) + '" y1="' + p1[1].toFixed(1) + '" x2="' + p2[0].toFixed(1) + '" y2="' + p2[1].toFixed(1) + '" stroke="' + ZONE_COLOUR[z[2]] + '" stroke-width="1.6" opacity=".8"/>';
  });
  out += '<text x="' + cx + '" y="' + (cy + 4) + '" fill="var(--ink)" font-size="30" font-weight="700" text-anchor="middle" class="led">' + esc(String(d.value)) + "</text>";
  out += '<text x="' + cx + '" y="' + (cy + 22) + '" fill="var(--ink-2)" font-size="10" text-anchor="middle" class="led">' + esc(d.unit) + "</text>";
  [0, 1].forEach(function (t) { var a = (start + span * t) * Math.PI / 180; out += '<text x="' + (cx + (r - 2) * Math.cos(a) + (t ? 6 : -6)).toFixed(1) + '" y="' + (cy + (r + 14) * Math.sin(a)).toFixed(1) + '" fill="var(--ink-3)" font-size="8.5" text-anchor="' + (t ? "start" : "end") + '" class="led">' + Math.round(t * d.max) + "</text>"; });
  return out + "</svg>";
}

function warningLight(l) {
  var col = l.on ? ZONE_COLOUR[l.level] : "var(--line-strong)";
  var icon = l.level === "red"
    ? '<path d="M12 3 L22 20 H2 Z" fill="none" stroke="' + col + '" stroke-width="2" stroke-linejoin="round"/><line x1="12" y1="9" x2="12" y2="14" stroke="' + col + '" stroke-width="2"/><circle cx="12" cy="17" r="1.1" fill="' + col + '"/>'
    : '<circle cx="12" cy="12" r="9" fill="none" stroke="' + col + '" stroke-width="2"/><line x1="12" y1="7" x2="12" y2="13" stroke="' + col + '" stroke-width="2"/><circle cx="12" cy="16.5" r="1.1" fill="' + col + '"/>';
  return '<a class="wl' + (l.on ? " on" : "") + '" href="#/' + esc(l.view) + '" style="' + (l.on ? "border-color:" + col + ";" : "") + '">' +
    '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true" style="' + (l.on ? "filter:drop-shadow(0 0 4px " + col + ");" : "") + '">' + icon + "</svg>" +
    '<span class="wl-n">' + l.count + '</span><span class="wl-t">' + esc(l.label) + "</span></a>";
}

function barRows(items, valueKey, fmt, unit) { return hbars(items, valueKey, fmt, unit, "No offences in this period."); }

VIEWS.bi = async function () {
  var days = 365;
  $("#content").innerHTML = '<div class="empty">Loading…</div>';
  async function draw() {
    var d = await api("/api/bi/?days=" + days);
    var ia = await api("/api/bi/inspections?days=" + days);
    var hmax = Math.max.apply(null, d.heat.rows.map(function (r) { return Math.max.apply(null, r.cells); }).concat([1]));
    var heat = '<div class="table-wrap"><table><thead><tr><th>Road</th>' + d.heat.seasons.map(function (s) { return "<th>" + esc(s.replace(/ \(.*/, "")) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      d.heat.rows.map(function (r) {
        return "<tr><td>" + esc(r.road) + "</td>" + r.cells.map(function (c) {
          return '<td style="background:color-mix(in srgb, var(--gold) ' + Math.round(c / hmax * 70) + '%, transparent);font-weight:' + (c / hmax > 0.6 ? 700 : 400) + '">' + c + "</td>";
        }).join("") + "</tr>";
      }).join("") + "</tbody></table></div>";
    var week = vcols(d.weekdays, "count");
    var seasons = d.seasons.map(function (s) {
      var hot = s.name === d.busiest_season;
      return '<div class="kpi"' + (hot ? ' style="border-left-color:#ff6b5b"' : "") + '><div class="kpi-label">' + esc(s.name) + '</div><div class="kpi-value">' + s.per_month + '<span style="font-size:14px;font-weight:500"> a month</span></div><div class="kpi-sub">' + s.count + " offences over " + s.months + " month" + (s.months === 1 ? "" : "s") + " of data" + (hot ? " · busiest" : "") + "</div></div>";
    }).join("");
    $("#content").innerHTML =
      '<div class="toolbar"><label class="small muted">Period</label><select id="bi-days" style="width:auto;flex:0 0 auto"><option value="90">Last 90 days</option><option value="180">Last 6 months</option><option value="365">Last 12 months</option><option value="1825">Last 5 years</option></select>' +
      '<span class="small muted">' + d.offences + " offences analysed" + (d.capped ? " (capped)" : "") + "</span></div>" +
      '<div class="card"><h3>Instrument cluster</h3><div class="cluster">' + d.dials.map(function (g) {
        return '<div class="dial">' + dialSvg(g) + '<div class="dial-l">' + esc(g.label) + '</div><div class="dial-s ' + g.zone + '">' + ZONE_GLYPH[g.zone] + " " + ZONE_WORD[g.zone] + '</div><div class="small muted">' + esc(g.note) + "</div></div>";
      }).join("") + "</div>" +
      '<div class="lights">' + d.lights.map(warningLight).join("") + "</div></div>" +
      '<div class="grid cards" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr))">' + seasons + "</div>" +
      '<div class="row"><div class="card"><h3>Roads with the most offences</h3>' + barRows(d.roads, "count", function (i) { return i.count + " · " + money(i.value); }) + "</div>" +
      '<div class="card"><h3>Provinces</h3>' + barRows(d.regions, "count") + '<p class="small muted" style="margin:8px 0 0">Province comes from the GPS position, or the place name when there is none.</p></div></div>' +
      '<div class="card"><h3>Road by season</h3>' + heat + '<div class="heat-key small muted"><span>fewer</span><i></i><span>more offences</span></div><p class="small muted" style="margin:8px 0 0">Seasons: rainy Nov to Apr, cold dry May to Aug, hot dry Sep to Oct. Darker means more offences. Compare seasons with the per-month figures above, because the data may not cover every month.</p></div>' +
      '<div class="row"><div class="card"><h3>Day of the week</h3>' + week + "</div>" +
      '<div class="card"><h3>Time of day</h3>' + barRows(d.day_parts, "count") + "</div>" +
      '<div class="card"><h3>Offence types</h3>' + barRows(d.types.map(function (t) { return { name: t.name.replace(/_/g, " "), count: t.count }; }), "count") + "</div></div>" +
      '<div class="row"><div class="card"><h3>Why vehicles fail inspection</h3><p class="small" style="margin:0 0 8px"><b>' + ia.failed + "</b> of " + ia.tested + " tests failed (" + ia.fail_rate + "%).</p>" +
        barRows(ia.top_defects.map(function (x) { return { name: x.defect, count: x.count }; }), "count") + "</div>" +
      '<div class="card"><h3>Failure rate by make</h3>' + barRows(ia.by_make.map(function (m) { return { name: m.name + " (" + m.tested + " tested)", count: m.fail_rate }; }), "count", null, "%") +
        '<h3 style="margin-top:14px">By centre</h3>' + barRows(ia.by_centre.map(function (m) { return { name: m.name + " (" + m.tested + ")", count: m.fail_rate }; }), "count", null, "%") + "</div></div>";
    $("#bi-days").value = String(days);
    $("#bi-days").addEventListener("change", function (e) { days = parseInt(e.target.value, 10); draw().catch(function (x) { fail("#content", x); }); });
  }
  try { await draw(); } catch (e) { fail("#content", e); }
};


/* The ANPR screen gets the scanner above its plate field: scan, confirm, then record the sighting. */
(function () {
  var original = VIEWS.anpr;
  VIEWS.anpr = async function () {
    await original();
    var form = $("#anpr-form"); if (!form) return;
    var holder = document.createElement("div");
    holder.innerHTML = plateScanHtml();
    form.insertBefore(holder.firstChild, form.firstChild);
    wirePlateScan(form, function (plate, conf) {
      form.elements.plate_number.value = plate;
      form.elements.confidence.value = (conf / 100).toFixed(2);
      form.elements.camera_id.value = form.elements.camera_id.value || "OFFICER-PHONE";
    });
  };
})();
