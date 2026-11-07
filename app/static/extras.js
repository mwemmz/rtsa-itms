"use strict";

/* Officer screens for PSV management (operators, permits, route corridors, route alerts)
   and insurance policies. Loaded after platform.js; reuses its helpers. */

(function extendNav() {
  [["admin"], ["officer"]].forEach(function (r) {
    NAV[r[0]] = (NAV[r[0]] || []).concat([
      { id: "psv", label: "PSV & routes" },
      { id: "insurance", label: "Insurance" },
      { id: "insights", label: "Insights" }
    ]);
  });
  // The administrator sees every module an officer does, plus the admin ones.
  var have = {};
  NAV.admin.forEach(function (x) { have[x.id] = 1; });
  NAV.officer.forEach(function (x) { if (!have[x.id] && x.id !== "account" && x.id !== "reports") NAV.admin.push(x); });
  NAV.citizen.push({ id: "services", label: "Pay for services" });

  var GROUPS = [
    ["Overview", ["dashboard", "insights", "reports"]],
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

/* ---------------- Insights (staff) ---------------- */

VIEWS.insights = async function () {
  $("#content").innerHTML = '<div class="empty">Loading…</div>';
  try {
    var d = await api("/api/insights/");
    var max = Math.max.apply(null, d.accident_hotspots.map(function (h) { return h.weight; }).concat([1]));
    var bars = d.accident_hotspots.length ? d.accident_hotspots.map(function (h) {
      return '<div style="margin-bottom:10px"><div class="small" style="display:flex;justify-content:space-between"><span>' + esc(h.location) + "</span><span class=\"muted\">" + h.accidents + " accidents</span></div>" +
        '<div style="height:8px;background:var(--line)"><div style="height:8px;width:' + Math.round(h.weight / max * 100) + '%;background:var(--gold)"></div></div></div>';
    }).join("") : '<div class="empty">No accidents recorded in the last year.</div>';
    var pipe = Object.keys(d.expiry_pipeline).map(function (k) { var p = d.expiry_pipeline[k]; return [esc(k), p["30"], p["60"], p["90"]]; });
    $("#content").innerHTML =
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
      "</div>";
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
  var timer = null;
  function later() { clearTimeout(timer); timer = setTimeout(settle, 120); }
  var content = $("#content");
  if (content && window.MutationObserver) new MutationObserver(later).observe(content, { childList: true, subtree: true });
  window.addEventListener("resize", later);
})();
