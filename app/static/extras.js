"use strict";

/* Officer screens for PSV management (operators, permits, route corridors, route alerts)
   and insurance policies. Loaded after platform.js; reuses its helpers. */

(function extendNav() {
  [["admin"], ["officer"]].forEach(function (r) {
    NAV[r[0]] = (NAV[r[0]] || []).concat([
      { id: "psv", label: "PSV & routes" },
      { id: "insurance", label: "Insurance" }
    ]);
  });
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
  $("#content").innerHTML =
    '<div class="row">' +
      '<div class="card"><h3>Insurance policies</h3>' +
        '<div class="field"><label>Vehicle plate</label><div class="toolbar" style="margin-bottom:0;"><input id="ins-plate" placeholder="e.g. BAK 123">' +
        '<button class="btn ghost sm" id="ins-go" type="button">Look up</button></div>' +
        '<div class="small muted" id="ins-vehicle" style="margin-top:6px;"></div></div>' +
        '<form id="ins-form" style="display:none;">' +
          '<div class="field"><label>Insurer</label><input name="provider" placeholder="e.g. ZSIC" required></div>' +
          '<div class="field"><label>Policy number</label><input name="policy_number" required></div>' +
          '<div class="field"><label>Starts</label><input name="start_date" type="datetime-local" required></div>' +
          '<div class="field"><label>Ends</label><input name="end_date" type="datetime-local" required></div>' +
          '<button class="btn gold" type="submit">Add policy</button></form></div>' +
      '<div class="card"><h3>Policies for this vehicle</h3><div id="ins-list"><div class="empty">Look up a vehicle first.</div></div></div>' +
    "</div>";

  var vehicleId = null;
  async function loadPolicies() {
    try {
      var items = await api("/api/insurance/vehicle/" + vehicleId);
      $("#ins-list").innerHTML = tbl(["Insurer", "Policy", "From", "To", "Status"], items.map(function (p) {
        return [esc(p.provider), '<span class="mono">' + esc(p.policy_number) + "</span>", dday(p.start_date), dday(p.end_date), pill(p.is_active ? "active" : "inactive", p.is_active ? "green" : "gray")];
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
    mutate("/api/insurance/", "POST", d, "Policy added", function () { e.target.reset(); loadPolicies(); });
  });
};
