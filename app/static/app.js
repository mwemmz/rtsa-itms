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
  // The API stores naive UTC; without a zone marker the browser would read it
  // as local time and show it hours off.
  if (typeof v === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(v)) v += "Z";
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

// Grouped by offence category so the officer records the offence against the
// party that actually carries the penalty. Mirrors DRIVER_OFFENCES /
// VEHICLE_OFFENCES / BOTH_OFFENCES in app/models/enforcement.py.
var VO_DRIVER_TYPES = [
  ["speeding", "Speeding"],
  ["running_red_light", "Running red light"],
  ["drunk_driving", "Drunk driving"],
  ["reckless_driving", "Reckless / dangerous driving"],
  ["using_phone", "Using phone"],
  ["rear_seat_belt", "Rear seat belt"],
  ["driving_without_licence", "Driving without licence"]
];
var VO_VEHICLE_TYPES = [
  ["expired_fitness", "Expired fitness certificate"],
  ["unroadworthy", "Unroadworthy vehicle"],
  ["expired_road_tax", "Expired road tax"],
  ["missing_number_plates", "Missing / improper number plates"],
  ["illegal_modification", "Illegal modification"],
  ["overloading", "Overloading"],
  ["no_psv_permit", "No PSV permit"],
  ["blacklisted_vehicle", "Blacklisted vehicle"]
];
var VO_BOTH_TYPES = [
  ["no_insurance", "No insurance"],
  ["illegal_parking", "Illegal parking"],
  ["other", "Other"]
];
var VO_TYPES = VO_DRIVER_TYPES.concat(VO_VEHICLE_TYPES, VO_BOTH_TYPES);
var VO_LICENCE_TYPES = VO_DRIVER_TYPES.map(function (t) { return t[0]; });
var VO_IMPOUND_TYPES = VO_VEHICLE_TYPES.map(function (t) { return t[0]; });
var VEHICLE_STATUSES = ["active", "suspended", "deregistered", "stolen"];
var CHALLAN_STATUSES = ["unpaid", "paid", "overdue", "disputed"];
var DRIVER_STATUSES = ["active", "suspended", "disqualified", "expired"];
var LICENCE_CLASSES = ["A", "B", "C", "D", "E", "F"];

/* ---------------- API client ---------------- */

function getToken() { return localStorage.getItem("rtsa_token") || ""; }
function deviceId() {
  try {
    var id = localStorage.getItem("rtsa_device");
    if (!id) {
      id = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now();
      localStorage.setItem("rtsa_device", id);
    }
    return id;
  } catch (e) { return ""; }
}
function offlineTollQueue() {
  try { return JSON.parse(localStorage.getItem("rtsa_offline_toll") || "[]"); }
  catch (e) { return []; }
}
function saveOfflineTollQueue(items) {
  localStorage.setItem("rtsa_offline_toll", JSON.stringify(items));
}
async function syncOfflineTollEvents() {
  var items = offlineTollQueue();
  if (!items.length) return 0;
  for (var i = 0; i < items.length; i += 1) {
    await api("/api/toll/offline/events", { method: "POST", body: JSON.stringify(items[i]) });
  }
  var result = await api("/api/toll/offline/sync", { method: "POST", body: JSON.stringify({ limit: 500 }) });
  saveOfflineTollQueue([]);
  return result.synced;
}
function setToken(t) { t ? localStorage.setItem("rtsa_token", t) : localStorage.removeItem("rtsa_token"); }

// Requests made while this is > 0 weren't started by the user (live-update
// reloads, the bell, stream reconnects). They're tagged so the server doesn't
// count them as activity - otherwise an open page never reaches the idle sign-out.
var BACKGROUND = 0;

async function inBackground(fn) {
  BACKGROUND++;
  try {
    return await fn();
  } finally {
    BACKGROUND--;
  }
}

async function api(path, opts) {
  opts = opts || {};
  opts.headers = opts.headers || {};
  if (opts.body && !(opts.body instanceof FormData) && !opts.headers["Content-Type"]) {
    opts.headers["Content-Type"] = "application/json";
  }
  var t = getToken();
  if (t) opts.headers["Authorization"] = "Bearer " + t;
  opts.headers["X-Device-Id"] = deviceId();
  if (BACKGROUND > 0) opts.headers["X-Background-Refresh"] = "1";
  var res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error("Network error — is the server reachable?");
  }
  var ct = res.headers.get("content-type") || "";
  var data = null;
  // 204 No Content still carries a JSON content-type here, so only parse a body that exists.
  if (ct.indexOf("application/json") !== -1 && res.status !== 204) {
    var text = await res.text();
    data = text ? JSON.parse(text) : null;
  }
  if (res.status === 401 && !/\/api\/auth\/(login|mfa)/.test(path)) {
    logout();
    throw new Error((data && data.detail) || "Session expired. Please sign in again.");
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
var plannerMap = null;
var INTERSECTIONS_BY_NAME = {};
var NAV = {
  admin: [
    { id: "dashboard", label: "Dashboard" },
    { id: "vehicles", label: "Vehicles" },
    { id: "drivers", label: "Drivers" },
    { id: "users", label: "Users" },
    { id: "violations", label: "Violations" },
    { id: "challans", label: "Challans" },
    { id: "alerts", label: "Road alerts" },
    { id: "audits", label: "Audit log" },
    { id: "rules", label: "Notification rules" },
    { id: "planner", label: "Route planner" }
  ],
  officer: [
    { id: "dashboard", label: "Dashboard" },
    { id: "vehicles", label: "Vehicles" },
    { id: "drivers", label: "Drivers" },
    { id: "licensing", label: "Licensing" },
    { id: "inspections", label: "Inspections" },
    { id: "anpr", label: "ANPR" },
    { id: "accidents", label: "Accidents" },
    { id: "violations", label: "Record violation" },
    { id: "challans", label: "Challans" },
    { id: "toll", label: "Toll gates" },
    { id: "alerts", label: "Road alerts" },
    { id: "planner", label: "Route planner" }
  ],
  citizen: [
    { id: "dashboard", label: "My dashboard" },
    { id: "report", label: "Report incident" },
    { id: "fines", label: "Fines & payments" },
    { id: "licence", label: "My licence" },
    { id: "alerts", label: "Road alerts" },
    { id: "planner", label: "Route planner" }
  ]
};
NAV.toll_operator = [
  { id: "dashboard", label: "Home" },
  { id: "toll", label: "Toll gates" }
];
var VIEW = { id: "dashboard", title: "Dashboard" };

function ensureViewId(id) {
  if (!USER) return "dashboard";
  var ids = NAV[USER.role] || [];
  if (ids.some(function (x) { return x.id === id; })) return id;
  return "dashboard";
}

function renderNav() {
  var ids = NAV[USER.role] || [];
  [["nav", false], ["mnav-list", true]].forEach(function (slot) {
    var el = $("#" + slot[0]);
    if (!el) return;
    el.innerHTML = "";
    ids.forEach(function (item) {
      var a = document.createElement("a");
      a.href = "#/" + item.id;
      a.innerHTML = '<span class="nav-dot" aria-hidden="true"></span>' + item.label;
      if (item.id === VIEW.id) a.className = "active";
      el.appendChild(a);
    });
  });
  $("#user-chip").textContent = USER.full_name + " · " + USER.role;
  $("#sidebar-foot").textContent = "Signed in as " + USER.email;
  var mu = $("#mnav-user");
  if (mu) mu.textContent = USER.full_name + " · " + USER.role;
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

var MFA_TOKEN = null;
var CAPTCHA = null; // { captcha_id } once a sandbox challenge is pending

function resetLoginForm() {
  MFA_TOKEN = null;
  CAPTCHA = null;
  $("#mfa-field").classList.add("hidden");
  $("#captcha-field").classList.add("hidden");
  $("#password").closest(".field").classList.remove("hidden");
  $("#email").closest(".field").classList.remove("hidden");
  $("#mfa-code").value = "";
  $("#captcha-answer").value = "";
  $("#login-btn").textContent = "Sign in";
}

// Only called after an attempt is rejected for a missing/wrong CAPTCHA - most
// deployments run with it off, so we don't pay this round trip up front.
// `prefix` picks the form: "" for sign-in, "su-" for sign-up.
async function loadCaptcha(prefix) {
  try {
    var c = await api("/api/auth/captcha");
    // A real provider (reCAPTCHA/hCaptcha/Turnstile) configured server-side needs
    // that provider's script + a CSP allowance, which this shell doesn't wire up.
    // See docs/PLATFORM.md.
    if (c.provider !== "sandbox") return null;
    $("#" + prefix + "captcha-question").textContent = c.question;
    $("#" + prefix + "captcha-answer").value = "";
    $("#" + prefix + "captcha-field").classList.remove("hidden");
    $("#" + prefix + "captcha-answer").focus();
    return { captcha_id: c.captcha_id };
  } catch (e) {
    return null; // the attempt itself already surfaced an error
  }
}

async function ensureCaptcha() {
  CAPTCHA = await loadCaptcha("");
}

async function doLogin(email, password, msgEl, btn) {
  msgEl.className = "login-msg";
  msgEl.textContent = MFA_TOKEN ? "Verifying…" : "Signing in…";
  if (btn) { btn.disabled = true; btn.classList.add("loading"); }
  try {
    var data;
    if (MFA_TOKEN) {
      data = await api("/api/auth/mfa/verify", {
        method: "POST",
        body: JSON.stringify({ mfa_token: MFA_TOKEN, code: $("#mfa-code").value.trim() })
      });
    } else {
      var body = { email: email, password: password };
      if (CAPTCHA && CAPTCHA.captcha_id) {
        body.captcha_id = CAPTCHA.captcha_id;
        body.captcha_answer = $("#captcha-answer").value.trim();
      }
      data = await api("/api/auth/login", { method: "POST", body: JSON.stringify(body) });
    }
    if (data.mfa_required) {
      MFA_TOKEN = data.mfa_token;
      $("#mfa-field").classList.remove("hidden");
      $("#password").closest(".field").classList.add("hidden");
      $("#email").closest(".field").classList.add("hidden");
      $("#login-btn").textContent = "Verify";
      msgEl.textContent = "Enter the 6-digit code from your authenticator app (or a recovery code).";
      $("#mfa-code").focus();
      return;
    }
    setToken(data.access_token);
    resetLoginForm();
    msgEl.textContent = "";
    await bootstrapApp();
  } catch (e) {
    if (MFA_TOKEN && /expired|Invalid MFA/i.test(e.message)) resetLoginForm();
    msgEl.className = "login-msg err";
    msgEl.textContent = e.message;
    if (!MFA_TOKEN && /captcha/i.test(e.message)) ensureCaptcha();
    $("#login-resend").classList.toggle("hidden", !/confirm your email/i.test(e.message));
  } finally {
    if (btn) { btn.disabled = false; btn.classList.remove("loading"); }
  }
}

/* ---------------- citizen sign-up ---------------- */

var SIGNUP_CAPTCHA = null;
var EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
var PHONE_RE = /^\+?[0-9][0-9 ]{6,19}$/;
var NRC_RE = /^\d{6}\/\d{2}\/\d$/;

// Puts the slashes in an NRC as it's typed: 123456781 -> 123456/78/1.
function formatNrc(value) {
  var d = String(value || "").replace(/\D/g, "").slice(0, 9);
  return d.length > 8 ? d.slice(0, 6) + "/" + d.slice(6, 8) + "/" + d.slice(8)
    : d.length > 6 ? d.slice(0, 6) + "/" + d.slice(6) : d;
}

function wireNrcInput(input) {
  input.addEventListener("input", function () {
    var atEnd = input.selectionStart === input.value.length;
    input.value = formatNrc(input.value);
    if (atEnd) input.selectionStart = input.selectionEnd = input.value.length;
  });
}

// The sign-in card holds four forms; exactly one is shown.
var AUTH_VIEWS = {
  signin: { form: "#login-form", focus: "#email", hash: "" },
  signup: { form: "#signup-form", focus: "#su-name", hash: "#/signup" },
  forgot: { form: "#forgot-form", focus: "#fg-email", hash: "#/forgot" },
  reset: { form: "#reset-form", focus: "#rs-password", hash: "" } // never put the token back in the URL
};

function showAuthView(view) {
  Object.keys(AUTH_VIEWS).forEach(function (name) {
    $(AUTH_VIEWS[name].form).classList.toggle("hidden", name !== view);
  });
  $("#demo-hint").classList.toggle("hidden", view !== "signin");
  $$("#login-screen .login-msg").forEach(function (el) { el.textContent = ""; el.className = "login-msg"; });
  $("#login-resend").classList.add("hidden");
  var hash = AUTH_VIEWS[view].hash;
  try { history.replaceState(null, "", hash || location.pathname); } catch (e) { /* ignore */ }
  $(AUTH_VIEWS[view].focus).focus();
}

function authMessage(el, text, ok) {
  el.className = "login-msg " + (ok ? "ok" : "err");
  el.textContent = text;
}

function markInvalid(input, msgEl, message) {
  input.setAttribute("aria-invalid", "true");
  input.focus();
  msgEl.className = "login-msg err";
  msgEl.textContent = message;
}

// Mirrors the server's checks for instant feedback; the server stays authoritative.
function signupProblem(body) {
  if (!body.full_name) return ["#su-name", "Enter your full name."];
  if (!NRC_RE.test(body.nrc_number)) return ["#su-nrc", "Enter your NRC number as on your card, e.g. 123456/78/1."];
  if (!EMAIL_RE.test(body.email)) return ["#su-email", "Enter a valid email address."];
  if (body.phone_number && !PHONE_RE.test(body.phone_number)) {
    return ["#su-phone", "Enter a valid mobile number, e.g. +260971234567."];
  }
  if (body.password.length < 8 || !/[a-z]/i.test(body.password) || !/[0-9]/.test(body.password)) {
    return ["#su-password", "Use at least 8 characters, with letters and numbers."];
  }
  if (body.password !== $("#su-confirm").value) return ["#su-confirm", "Passwords don't match."];
  return null;
}

// Which input a server-side rejection is about, so we can point at it.
function signupFieldFor(message) {
  if (/NRC/.test(message)) return "#su-nrc";
  if (/email/i.test(message)) return "#su-email";
  if (/password/i.test(message)) return "#su-password";
  if (/mobile|phone/i.test(message)) return "#su-phone";
  if (/name/i.test(message)) return "#su-name";
  return null;
}

async function doSignup() {
  var msgEl = $("#signup-msg");
  var btn = $("#signup-btn");
  $$("#signup-form [aria-invalid]").forEach(function (el) { el.removeAttribute("aria-invalid"); });
  var body = {
    full_name: $("#su-name").value.trim(),
    nrc_number: formatNrc($("#su-nrc").value),
    email: $("#su-email").value.trim(),
    password: $("#su-password").value
  };
  var phone = $("#su-phone").value.trim();
  if (phone) body.phone_number = phone;
  var problem = signupProblem(body);
  if (problem) { markInvalid($(problem[0]), msgEl, problem[1]); return; }
  if (SIGNUP_CAPTCHA) {
    body.captcha_id = SIGNUP_CAPTCHA.captcha_id;
    body.captcha_answer = $("#su-captcha-answer").value.trim();
  }

  msgEl.className = "login-msg";
  msgEl.textContent = "Creating your account…";
  btn.disabled = true;
  btn.classList.add("loading");
  try {
    await api("/api/auth/register", { method: "POST", body: JSON.stringify(body) });
  } catch (e) {
    var field = signupFieldFor(e.message);
    if (field) markInvalid($(field), msgEl, e.message);
    else { msgEl.className = "login-msg err"; msgEl.textContent = e.message; }
    if (/captcha/i.test(e.message)) SIGNUP_CAPTCHA = await loadCaptcha("su-");
    return;
  } finally {
    btn.disabled = false;
    btn.classList.remove("loading");
  }

  // Created - sign straight in with the same credentials.
  $("#signup-form").reset();
  $("#su-captcha-field").classList.add("hidden");
  SIGNUP_CAPTCHA = null;
  showAuthView("signin");
  $("#email").value = body.email;
  $("#password").value = body.password;
  await doLogin(body.email, body.password, $("#login-msg"), $("#login-btn"));
  if (!USER && /captcha/i.test($("#login-msg").textContent)) {
    // Sign-in also wants a CAPTCHA: the account exists, so say that rather than "failed".
    $("#login-msg").className = "login-msg ok";
    $("#login-msg").textContent = "Account created. Answer the question below to sign in.";
  }
}

/* ---------------- forgotten password & email confirmation ---------------- */

// Emailed links look like /#/reset?token=... - the token sits after "#" so the
// browser never sends it to the server (no access logs). Read it, then drop it
// from the address bar and history straight away.
var RESET_TOKEN = null;

function takeLinkToken() {
  var m = /^#\/(reset|verify)\?token=([^&]+)/.exec(location.hash || "");
  if (!m) return null;
  try { history.replaceState(null, "", location.pathname); } catch (e) { /* ignore */ }
  return { kind: m[1], token: decodeURIComponent(m[2]) };
}

async function withButton(btn, fn) {
  btn.disabled = true;
  btn.classList.add("loading");
  try { return await fn(); } finally { btn.disabled = false; btn.classList.remove("loading"); }
}

async function doForgot() {
  var msgEl = $("#forgot-msg");
  var input = $("#fg-email");
  input.removeAttribute("aria-invalid");
  var email = input.value.trim();
  if (!EMAIL_RE.test(email)) { markInvalid(input, msgEl, "Enter the email address you sign in with."); return; }
  await withButton($("#forgot-btn"), async function () {
    try {
      var r = await api("/api/auth/password-reset/request", { method: "POST", body: JSON.stringify({ email: email }) });
      authMessage(msgEl, r.detail + " Check your inbox (and spam folder); the link works for 30 minutes.", true);
    } catch (e) { authMessage(msgEl, e.message); }
  });
}

async function doReset() {
  var msgEl = $("#reset-msg");
  var pw = $("#rs-password"), confirm = $("#rs-confirm");
  [pw, confirm].forEach(function (el) { el.removeAttribute("aria-invalid"); });
  if (pw.value.length < 8 || !/[a-z]/i.test(pw.value) || !/[0-9]/.test(pw.value)) {
    markInvalid(pw, msgEl, "Use at least 8 characters, with letters and numbers."); return;
  }
  if (pw.value !== confirm.value) { markInvalid(confirm, msgEl, "Passwords don't match."); return; }
  await withButton($("#reset-btn"), async function () {
    try {
      await api("/api/auth/password-reset/confirm", {
        method: "POST", body: JSON.stringify({ token: RESET_TOKEN, new_password: pw.value })
      });
    } catch (e) {
      if (/password/i.test(e.message) && !/link/i.test(e.message)) { markInvalid(pw, msgEl, e.message); return; }
      authMessage(msgEl, e.message); // expired/used link: the form below lets them ask for a new one
      return;
    }
    RESET_TOKEN = null;
    $("#reset-form").reset();
    showAuthView("signin");
    authMessage($("#login-msg"), "Password changed. Sign in with your new password.", true);
  });
}

async function confirmEmail(token) {
  try {
    var r = await api("/api/auth/verify-email", { method: "POST", body: JSON.stringify({ token: token }) });
    if (USER) {
      toast("Email address confirmed", "ok");
      USER.email_verified = true;
      renderVerifyBanner();
    } else {
      showAuthView("signin");
      $("#email").value = r.email;
      authMessage($("#login-msg"), "Email address confirmed. Sign in to continue.", true);
    }
  } catch (e) {
    if (USER) toast(e.message, "err");
    else { showAuthView("signin"); authMessage($("#login-msg"), e.message); }
  }
}

async function resendConfirmation(email) {
  var r = await api("/api/auth/verify-email/resend", { method: "POST", body: JSON.stringify({ email: email }) });
  return r.detail;
}

function renderVerifyBanner() {
  var show = !!USER && USER.email_verified === false;
  $("#verify-banner").classList.toggle("hidden", !show);
  if (show) $("#verify-email").textContent = USER.email;
}

// Deep links into the signed-out card: #/signup, #/forgot, and emailed #/reset / #/verify links.
function routeAuthHash() {
  var link = takeLinkToken();
  if (link && link.kind === "reset") {
    RESET_TOKEN = link.token;
    showAuthView("reset");
  } else if (link && link.kind === "verify") {
    confirmEmail(link.token);
  } else if (location.hash === "#/signup") {
    showAuthView("signup");
  } else if (location.hash === "#/forgot") {
    showAuthView("forgot");
  } else {
    showAuthView("signin");
  }
}

function logout() {
  var t = getToken();
  if (t) {
    try { fetch("/api/auth/logout", { method: "POST", headers: { Authorization: "Bearer " + t } }); } catch (e) { /* ignore */ }
  }
  setToken(null);
  closeLiveStream();
  resetLoginForm();
  USER = null;
  renderVerifyBanner();
  $("#shell").classList.add("hidden");
  $("#mfa-gate").classList.add("hidden");
  $("#login-screen").classList.remove("hidden");
  document.title = "RTSA ITMS — Web App";
  location.hash = "";
  refreshBell();
}

async function bootstrapApp() {
  USER = await api("/api/auth/me");
  renderVerifyBanner();
  $("#login-screen").classList.add("hidden");
  if (USER.mfa_setup_required) {
    $("#shell").classList.add("hidden");
    renderMfaGate();
    return;
  }
  $("#mfa-gate").classList.add("hidden");
  $("#shell").classList.remove("hidden");
  await loadRouter();
  openLiveStream();
}

/* ---------------- live updates (SSE) ---------------- */

// Which views should auto-reload when a given entity changes elsewhere.
var LIVE_VIEWS = {
  vehicle: ["vehicles", "challans", "dashboard", "reports"],
  driver: ["drivers", "dashboard"],
  challan: ["challans", "fines", "dashboard", "reports"],
  payment: ["fines", "receipts", "challans", "payments", "reports", "dashboard"],
  user: ["users", "audits", "dashboard"],
  role: ["users", "audits"],
  setting: ["settings"],
  notification_rule: ["rules"],
  road_incident: ["alerts", "dashboard", "planner", "accidents", "report"],
  accident: ["dashboard", "accidents", "reports"],
  toll_transaction: ["toll", "reports"],
  toll_offline_event: ["toll"],
  anpr_event: ["dashboard", "anpr"],
  inspection: ["vehicles", "inspections"],
  insurance: ["vehicles"],
  licence_application: ["applications", "fines", "licensing", "reports"],
  agency: ["integrations"],
  session: ["users", "account"],
  psv_operator: ["psv"],
  psv_permit: ["psv"]
};
// Views that hold in-progress user input: only toast, never auto-reload.
var LIVE_FORM_VIEWS = { violations: 1, toll: 1, account: 1, licensing: 1, inspections: 1, anpr: 1 };
// Views with a form that can still refresh their data panels in place. Checked
// before LIVE_FORM_VIEWS; the value is the refresh function.
var LIVE_PARTIAL_VIEWS = {
  planner: function () { return refreshPlannerLive(); },
  alerts: function () { return loadAlertList(); },
  report: function () { return Promise.all([loadReportStanding(), loadMyReports()]); },
  accidents: function () { return Promise.all([loadAccidentStats(), loadAccidents()]); },
  reports: function () { return window.loadReportKpis ? window.loadReportKpis() : null; }
};
var LIVE_ES = null;
var LIVE_RELOAD_TIMER = null;
var LIVE_RETRY_TIMER = null;
var LIVE_RETRY_MS = 1000;
var LIVE_GEN = 0; // bumped on every open/close so a stale async open can bail out

// EventSource can't send an Authorization header, so the stream is opened with a
// single-use ticket in the URL rather than the access token (URLs end up in
// server logs). A used ticket can't reopen the stream, so instead of letting
// EventSource auto-reconnect we close it and come back with a fresh ticket.
async function openLiveStream() {
  closeLiveStream();
  var gen = LIVE_GEN;
  if (!getToken()) return;
  var t;
  try {
    t = await inBackground(function () { return api("/api/events/ticket", { method: "POST" }); });
  } catch (e) {
    if (gen === LIVE_GEN) scheduleLiveReconnect();
    return;
  }
  if (gen !== LIVE_GEN || !getToken()) return; // signed out or reopened meanwhile
  var es = new EventSource("/api/events/stream?ticket=" + encodeURIComponent(t.ticket));
  LIVE_ES = es;
  es.onopen = function () { LIVE_RETRY_MS = 1000; };
  es.onmessage = function (e) {
    try { onLiveEvent(JSON.parse(e.data)); } catch (err) { /* ignore malformed */ }
  };
  es.onerror = function () {
    if (LIVE_ES !== es) return;
    closeLiveStream();
    scheduleLiveReconnect();
  };
}

function scheduleLiveReconnect() {
  if (!getToken() || LIVE_RETRY_TIMER) return;
  LIVE_RETRY_TIMER = setTimeout(function () {
    LIVE_RETRY_TIMER = null;
    openLiveStream();
  }, LIVE_RETRY_MS);
  LIVE_RETRY_MS = Math.min(LIVE_RETRY_MS * 2, 30000);
}

function closeLiveStream() {
  LIVE_GEN++;
  if (LIVE_RETRY_TIMER) { clearTimeout(LIVE_RETRY_TIMER); LIVE_RETRY_TIMER = null; }
  if (LIVE_ES) { try { LIVE_ES.close(); } catch (e) { /* ignore */ } LIVE_ES = null; }
}

function onLiveEvent(ev) {
  if (!ev || !ev.entity) return;
  if (ev.entity === "notification") { inBackground(refreshBell); return; }
  var targets = LIVE_VIEWS[ev.entity] || [];
  if (ev.action === "pay" || ev.action === "refund" || ev.action === "broadcast") inBackground(refreshBell);
  if (VIEW.id && targets.indexOf(VIEW.id) !== -1) {
    if (LIVE_PARTIAL_VIEWS[VIEW.id]) {
      var viewId = VIEW.id;
      if (LIVE_RELOAD_TIMER) clearTimeout(LIVE_RELOAD_TIMER);
      LIVE_RELOAD_TIMER = setTimeout(function () {
        LIVE_RELOAD_TIMER = null;
        if (!getToken() || VIEW.id !== viewId) return;
        inBackground(LIVE_PARTIAL_VIEWS[viewId]).catch(function () { /* ignore */ });
      }, 700);
      return;
    }
    if (LIVE_FORM_VIEWS[VIEW.id]) { toast("Live update: " + (ev.action || "data") + " — refresh to see changes"); return; }
    if (LIVE_RELOAD_TIMER) clearTimeout(LIVE_RELOAD_TIMER);
    LIVE_RELOAD_TIMER = setTimeout(function () {
      LIVE_RELOAD_TIMER = null;
      if (!getToken()) return;
      inBackground(function () { return go(VIEW.id); }).catch(function () { /* ignore */ });
    }, 700);
  }
}

function renderMfaGate() {
  $("#shell").classList.add("hidden");
  $("#mfa-gate").classList.remove("hidden");
  var box = $("#mfa-gate-box");
  box.innerHTML = '<button class="btn gold block" id="mfa-gate-start">Set up two-factor authentication</button>';
  $("#mfa-gate-start").addEventListener("click", async function () {
    box.innerHTML = "Loading…";
    try {
      var s = await api("/api/auth/mfa/setup", { method: "POST" });
      box.innerHTML = '<p class="small">In your authenticator app choose "enter a setup key" and paste:</p>' +
        '<pre class="mono" style="white-space:pre-wrap;word-break:break-all">' + esc(s.secret) + '</pre>' +
        '<p class="small muted">Or open this link on your phone:<br><span class="mono" style="word-break:break-all">' + esc(s.otpauth_uri) + "</span></p>" +
        '<form id="mfa-gate-form"><div class="field"><input name="code" inputmode="numeric" placeholder="6-digit code" required></div>' +
        '<button class="btn gold block">Confirm &amp; enable</button></form><div class="login-msg" id="mfa-gate-msg"></div>';
      $("#mfa-gate-form").addEventListener("submit", async function (e) {
        e.preventDefault();
        var msgEl = $("#mfa-gate-msg");
        msgEl.className = "login-msg";
        msgEl.textContent = "";
        try {
          var r = await api("/api/auth/mfa/enable", { method: "POST", body: JSON.stringify(formData(e.target)) });
          box.innerHTML = '<p class="small"><b>Save these recovery codes</b> — each works once if you lose your phone. They will not be shown again.</p>' +
            '<pre class="mono">' + r.recovery_codes.map(esc).join("\n") + '</pre>' +
            '<button class="btn gold block" id="mfa-gate-continue">Continue</button>';
          $("#mfa-gate-continue").addEventListener("click", function () { bootstrapApp().catch(logout); });
        } catch (err) {
          msgEl.className = "login-msg err";
          msgEl.textContent = err.message;
        }
      });
    } catch (e) {
      box.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
    }
  });
}

/* ---------------- notifications & mobile menu ---------------- */

function openMenu() {
  $("#mnav").classList.remove("hidden");
  $("#overlay").classList.remove("hidden");
}

function closeMenu() {
  $("#mnav").classList.add("hidden");
  $("#overlay").classList.add("hidden");
}

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
  if (status === "active" || status === "paid" || status === "valid" || status === "passed" || status === "issued") cls = "green";
  else if (status === "suspended" || status === "expired" || status === "overdue" || status === "deregistered" || status === "failed" || status === "rejected") cls = "red";
  else if (status === "unpaid" || status === "pending") cls = "amber";
  return '<span class="pill ' + cls + '">' + esc(status) + "</span>";
}

var CATEGORY_LABELS = { vehicle: "Vehicle offence", driver: "Driver offence", both: "Both", reporter: "False report" };
var CATEGORY_CLASSES = { vehicle: "blue", driver: "amber", both: "gray", reporter: "red" };
var LIABLE_LABELS = { driver: "Driver", owner: "Owner", both: "Driver + owner", reporter: "Reporter" };

function categoryPill(category) {
  var key = CATEGORY_LABELS[category] ? category : "both";
  return '<span class="pill ' + CATEGORY_CLASSES[key] + '">' + esc(CATEGORY_LABELS[key]) + "</span>";
}

function offenceTypeLabel(violationType) {
  return String(violationType == null ? "" : violationType).replace(/_/g, " ");
}

// Driver offences can add licence points or lead to a ban; vehicle offences
// ground impoundment. Show whichever consequence actually applies.
function consequenceTags(v) {
  var tags = "";
  if (v.carries_licence_consequence) tags += '<span class="pill red">Licence risk</span> ';
  if (v.grounds_impoundment) tags += '<span class="pill red">Impoundable</span> ';
  return tags;
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
  var violations = await api("/api/enforcement/violations?limit=6");
  return (
    '<div class="grid cards">' +
      kpi("Registered vehicles", s.vehicles) +
      kpi("Drivers", s.drivers) +
      kpi("Unpaid challans", s.challans_unpaid, "of " + s.challans_total + " total") +
      kpi("Revenue collected", money(s.revenue_collected)) +
    "</div>" +
    '<div class="row">' +
      cars("Recent vehicles", vehicleRows(vehicles)) +
      cars("Recent violations", violationRows(violations) +
        '<p class="small muted" style="margin:10px 0 0;"><a href="#/violations">View all violations</a></p>') +
    "</div>" +
    '<div class="row">' +
      cars("Recent challans", challanRows(challans)) +
    "</div>"
  );
}

async function officerDashboard() {
  var violations = await api("/api/enforcement/violations?limit=6");
  var challans = await api("/api/enforcement/challans?limit=6");
  // challans_by_status is an all-time, unfiltered count (see app/services/reports.py
  // dashboard()) -- unlike the 6-row lists above, it isn't capped, so it's the only
  // accurate source for "how many challans are actually outstanding".
  var unpaid = 0;
  try {
    var stats = await api("/api/reports/dashboard");
    var byStatus = (stats.breakdowns && stats.breakdowns.challans_by_status) || {};
    Object.keys(byStatus).forEach(function (s) { if (s !== "paid") unpaid += byStatus[s]; });
  } catch (e) {
    unpaid = challans.filter(function (c) { return c.status !== "paid"; }).length;
  }
  var incidents = [];
  try { incidents = await api("/api/incidents/alerts"); } catch (e) { /* board still renders */ }
  var blocking = incidents.filter(function (a) { return a.blocking; }).length;
  var toReview = incidents.filter(function (a) { return a.verification === "unverified"; }).length;
  return (
    '<div class="grid cards">' +
      kpi("Active challans", unpaid) +
      kpi("Recent violations", violations.length) +
      kpi("Active road incidents", "<a href='#/alerts'>" + incidents.length + "</a>", blocking + " blocking a road") +
      kpi("Citizen reports to review", "<a href='#/alerts'>" + toReview + "</a>", toReview ? "Live on the planner until reviewed" : "All reviewed") +
      kpi("Tools", "<div><a class='btn gold sm' href='#/violations'>Record violation</a> <a class='btn sm' href='#/accidents'>Report accident</a> <a class='btn sm' href='#/toll'>Toll gate</a></div>") +
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
      '<div class="table-wrap"><table><tr><td>Licence no.</td><td class="mono">' + esc(licence.licence_number) + "</td></tr>" +
      '<tr><td>Class</td><td>' + esc(licence.licence_class) + '</td></tr>' +
      '<tr><td>Expires</td><td>' + dt(licence.expiry_date) + ' (' + Number(licence.days_until_expiry) + " days)</td></tr></table></div>"
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
        ? '<div class="table-wrap"><table>' + (d.vehicles.map(function (v) {
            return "<tr><td><b>" + esc(v.registration_number) + "</b><div class='small muted'>" + esc(v.make) + " " + esc(v.model) + " (" + v.year + ")</div></td><td>" + statusPill(v.status) + "</td></tr>";
          }).join("")) + "</table></div>"
        : '<div class="empty">No vehicles registered to you.</div>') +
      cars("Road alerts", ((d.active_alerts || []).length
        ? d.active_alerts.map(function (a) {
            return '<div class="small" style="padding:6px 0;border-bottom:1px solid var(--line);"><b>' + esc(INCIDENT_TYPE_LABELS[a.incident_type] || a.incident_type) + "</b> · " + esc(a.severity) +
              (a.verification === "unverified" ? " " + verificationPill(a.verification) : "") +
              (a.road_name ? ' on <b>' + esc(a.road_name) + "</b>" : "") +
              (a.stretch ? '<div class="muted">' + esc(a.stretch) + "</div>" : "") +
              (a.description ? "<div>" + esc(a.description) + "</div>" : "") + "</div>";
          }).join("") + '<p class="small" style="margin:10px 0 0;"><a href="#/planner">Plan a route around these</a></p>'
        : '<div class="empty">No active alerts.</div>') +
        '<p style="margin:12px 0 0;"><a class="btn gold sm" href="#/report">Report an incident</a></p>') +
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
    ? '<div class="table-wrap"><table><tr><th>Reg</th><th>Owner</th><th>Vehicle</th><th>Status</th></tr>' + vs.map(function (v) {
        return "<tr><td class='mono'><b>" + esc(v.registration_number) + "</b></td><td>" + esc(v.owner_name) + "</td><td>" + esc(v.make) + " " + esc(v.model) + "</td><td>" + statusPill(v.status) + "</td></tr>";
      }).join("") + "</table></div>"
    : '<div class="empty">No vehicles found.</div>';
}

function violationRows(vs) {
  return vs.length
    ? '<div class="table-wrap"><table><tr><th>Type</th><th>Category</th><th>Liable</th><th>Speed</th><th>Consequence</th><th>Offender</th><th>Location</th><th>When</th></tr>' + vs.map(function (v) {
        var offender = v.driver_name || v.owner_name || v.account_name || "Unknown offender";
        var vehicle = v.registration_number ? "<div class='small muted'>" + esc(v.registration_number) + "</div>" : "";
        return "<tr><td>" + esc(offenceTypeLabel(v.violation_type)) + "</td><td>" + categoryPill(v.category) + "</td><td>" + esc(LIABLE_LABELS[v.liable_party] || v.liable_party) + "</td><td>" + (v.speed_kmh == null ? '<span class="small muted">—</span>' : "<b>" + esc(String(v.speed_kmh).replace(/\.0$/, "")) + "</b> km/h") + "</td><td>" + (consequenceTags(v) || '<span class="small muted">—</span>') + "</td><td>" + esc(offender) + vehicle + "</td><td>" + esc(v.location) + "</td><td class='small'>" + dt(v.timestamp) + "</td></tr>";
      }).join("") + "</table></div>"
    : '<div class="empty">No violations recorded.</div>';
}

function challanRows(cs) {
  return cs.length
    ? '<div class="table-wrap"><table><tr><th>Ref</th><th>Category</th><th>Liable</th><th>Offender</th><th>Amount</th><th>Due</th><th>Status</th></tr>' + cs.map(function (c) {
        var offender = c.driver_name || c.owner_name || c.account_name || "Unknown offender";
        var vehicle = c.registration_number ? "<div class='small muted'>" + esc(c.registration_number) + "</div>" : "";
        return "<tr><td class='mono'>" + esc(c.reference) + "</td><td>" + categoryPill(c.category) + "</td><td>" + esc(LIABLE_LABELS[c.liable_party] || c.liable_party) + "</td><td>" + esc(offender) + vehicle + "</td><td>" + money(c.penalty_amount) + "</td><td class='small'>" + dt(c.due_date) + "</td><td>" + statusPill(c.status) + "</td></tr>";
      }).join("") + "</table></div>"
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
        var wasBlacklisted = btn.dataset.blacklisted === "true";
        var reason = wasBlacklisted ? "" : prompt("Blacklist reason:");
        if (reason === null) return;
        if (wasBlacklisted && !confirm("Remove this vehicle from the blacklist?")) return;
        var b = wasBlacklisted ? false : true;
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
        if (!confirm("Suspend this driver's licence? This revokes their driving privilege.")) return;
        api("/api/drivers/" + btn.dataset.id, { method: "DELETE" })
          .then(function () { toast("Driver suspended", "ok"); loadDrivers(); })
          .catch(function (e) { toast(e.message, "err"); });
      });
    });
  } catch (e) {
    table.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

/* ---------------- officer: licence applications ---------------- */

var LICENCE_STATUS_LABELS = {
  submitted: "Submitted",
  theory_test_scheduled: "Theory scheduled",
  theory_test_passed: "Theory passed",
  theory_test_failed: "Theory failed",
  practical_test_scheduled: "Practical scheduled",
  practical_test_passed: "Practical passed",
  practical_test_failed: "Practical failed",
  issued: "Issued",
  rejected: "Rejected"
};

VIEWS.licensing = async function () {
  $("#content").innerHTML =
    '<div class="card"><h3>Licence applications</h3>' +
      '<div class="toolbar"><select id="la-status"><option value="">All statuses</option>' +
        Object.keys(LICENCE_STATUS_LABELS).map(function (s) {
          return '<option value="' + s + '">' + LICENCE_STATUS_LABELS[s] + "</option>";
        }).join("") +
      '</select><button class="btn gold sm" id="la-btn">Filter</button></div>' +
      '<div id="la-table"><div class="empty">Loading…</div></div></div>';
  $("#la-btn").addEventListener("click", loadLicenceApplications);
  $("#la-status").addEventListener("change", loadLicenceApplications);
  await loadLicenceApplications();
};

function licenceAction(app) {
  switch (app.status) {
    case "submitted":
    case "theory_test_scheduled":
    case "theory_test_failed":
      return '<button class="btn ghost sm la-theory" data-id="' + esc(app.id) + '">Record theory score</button>';
    case "theory_test_passed":
    case "practical_test_scheduled":
    case "practical_test_failed":
      return '<button class="btn ghost sm la-practical" data-id="' + esc(app.id) + '">Record practical score</button>';
    case "practical_test_passed":
      return '<button class="btn gold sm la-issue" data-id="' + esc(app.id) + '">Issue licence</button>';
    default:
      return "";
  }
}

async function loadLicenceApplications() {
  var table = $("#la-table");
  table.innerHTML = '<div class="empty">Loading…</div>';
  var status = $("#la-status").value;
  try {
    var items = await api("/api/licence-applications/");
    if (status) items = items.filter(function (a) { return a.status === status; });
    if (!items.length) { table.innerHTML = '<div class="empty">No applications found.</div>'; return; }
    table.innerHTML = '<div class="table-wrap"><table><tr><th>Applicant</th><th>NRC</th><th>Class</th><th>Status</th><th>Theory</th><th>Practical</th><th>Licence no.</th><th></th></tr>' +
      items.map(function (a) {
        return "<tr><td>" + esc(a.first_name) + " " + esc(a.last_name) + '</td><td class="mono">' + esc(a.id_number) + "</td><td>" + esc(a.requested_class) + "</td><td>" +
          esc(LICENCE_STATUS_LABELS[a.status] || a.status) + "</td><td>" + (a.theory_score == null ? "—" : a.theory_score) + "</td><td>" +
          (a.practical_score == null ? "—" : a.practical_score) + '</td><td class="mono">' + esc(a.issued_licence_number || "—") + "</td><td>" + licenceAction(a) + "</td></tr>";
      }).join("") + "</table></div>";

    $$("#la-table .la-theory").forEach(function (btn) {
      btn.addEventListener("click", function () { recordLicenceScore(btn.dataset.id, "theory"); });
    });
    $$("#la-table .la-practical").forEach(function (btn) {
      btn.addEventListener("click", function () { recordLicenceScore(btn.dataset.id, "practical"); });
    });
    $$("#la-table .la-issue").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (!confirm("Issue a driver's licence for this applicant?")) return;
        api("/api/licence-applications/" + btn.dataset.id + "/issue", { method: "POST" })
          .then(function () { toast("Licence issued", "ok"); loadLicenceApplications(); })
          .catch(function (e) { toast(e.message, "err"); });
      });
    });
  } catch (e) {
    table.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

function recordLicenceScore(id, kind) {
  var raw = prompt("Enter the " + kind + " test score (0-100):");
  if (raw === null) return;
  var score = Number(raw);
  if (!Number.isInteger(score) || score < 0 || score > 100) {
    toast("Score must be a whole number from 0 to 100", "err");
    return;
  }
  var body = kind === "theory" ? { theory_score: score } : { practical_score: score };
  api("/api/licence-applications/" + id + "/" + kind, { method: "POST", body: JSON.stringify(body) })
    .then(function () {
      var passed = score >= 70;
      toast((kind === "theory" ? "Theory" : "Practical") + " test recorded — " + (passed ? "passed" : "failed"), passed ? "ok" : "err");
      loadLicenceApplications();
    })
    .catch(function (e) { toast(e.message, "err"); });
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
    table.innerHTML = '<div class="table-wrap"><table><tr><th>Ref</th><th>Category</th><th>Liable</th><th>Amount</th><th>Due</th><th>Status</th><th></th></tr>' +
      items.map(function (c) {
        var pay = c.status !== "paid"
          ? '<button class="btn gold sm pay" data-id="' + esc(c.id) + '" data-ref="' + esc(c.reference) + '">Mark paid</button>'
          : "";
        return "<tr><td class='mono'>" + esc(c.reference) + "</td><td>" + categoryPill(c.category) + "</td><td>" + esc(LIABLE_LABELS[c.liable_party] || c.liable_party) + "</td><td>" + money(c.penalty_amount) + '</td><td class="small">' + dt(c.due_date) + "</td><td>" + statusPill(c.status) + "</td><td>" + pay + "</td></tr>";
      }).join("") + "</table></div>";
    $$("#challan-table .pay").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (!confirm("Record " + btn.dataset.ref + " as paid?")) return;
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

/* ---------------- road network picker (accidents, incidents) ---------------- */

// Places are picked from the mapped network rather than typed, so every
// accident or incident lands on a real road that the alert feed, status board
// and route planner all understand.
async function loadRoadNetwork() {
  var res = await Promise.all([
    api("/api/road-network/roads"),
    api("/api/road-network/intersections"),
    api("/api/road-network/segments")
  ]);
  var net = { roads: res[0], intersections: res[1], segments: res[2], ix: {}, roadById: {} };
  net.intersections.forEach(function (i) { net.ix[i.id] = i; });
  net.roads.forEach(function (r) { net.roadById[r.id] = r; });
  return net;
}

function stretchLabel(net, seg) {
  var a = net.ix[seg.start_intersection_id], b = net.ix[seg.end_intersection_id];
  return (a ? a.name : "?") + " → " + (b ? b.name : "?");
}

// Rough metres between two lat/lng points (equirectangular is plenty at city scale).
function metresBetween(lat1, lng1, lat2, lng2) {
  var x = (lng2 - lng1) * Math.cos((lat1 + lat2) * Math.PI / 360);
  var y = lat2 - lat1;
  return Math.sqrt(x * x + y * y) * 111320;
}

function metresToSegment(net, seg, lat, lng) {
  var a = net.ix[seg.start_intersection_id], b = net.ix[seg.end_intersection_id];
  if (!a || !b) return Infinity;
  var k = Math.cos(lat * Math.PI / 180);
  var ax = a.longitude * k, ay = a.latitude, bx = b.longitude * k, by = b.latitude, px = lng * k, py = lat;
  var dx = bx - ax, dy = by - ay;
  var t = dx || dy ? ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy) : 0;
  t = Math.max(0, Math.min(1, t));
  var cx = ax + t * dx, cy = ay + t * dy;
  return Math.sqrt((px - cx) * (px - cx) + (py - cy) * (py - cy)) * 111320;
}

function currentPosition() {
  return new Promise(function (resolve, reject) {
    if (!navigator.geolocation) { reject(new Error("This browser can't share your location")); return; }
    navigator.geolocation.getCurrentPosition(
      function (p) { resolve({ lat: p.coords.latitude, lng: p.coords.longitude }); },
      function (e) { reject(new Error(e.code === 1 ? "Location permission was denied" : "Couldn't get your location")); },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
    );
  });
}

// Validation is done on submit by the caller (accidents need a stretch,
// incidents may cover a whole road), so the selects carry no `required`.
function roadPickerHtml(prefix) {
  return '<div class="field"><label>Road</label><select id="' + prefix + '-road"><option value="">Choose a road…</option></select></div>' +
    '<div class="field"><label>Stretch (between junctions)</label><select id="' + prefix + '-segment" disabled><option value="">Choose the road first</option></select>' +
    '<div class="toolbar" style="margin-top:6px;"><button type="button" class="btn ghost sm" id="' + prefix + '-here">Use my location</button>' +
    '<span class="small muted" id="' + prefix + '-here-msg"></span></div></div>';
}

// Returns a getter for { road_id, segment_id } or null when nothing is chosen.
// wholeRoad: offer "Whole road" as the empty stretch choice.
function wireRoadPicker(prefix, net, wholeRoad) {
  var roadSel = $("#" + prefix + "-road"), segSel = $("#" + prefix + "-segment"), msg = $("#" + prefix + "-here-msg");
  roadSel.innerHTML = '<option value="">Choose a road…</option>' + net.roads.map(function (r) {
    return '<option value="' + esc(r.id) + '">' + esc(r.name) + "</option>";
  }).join("");
  function fillSegments(selectId) {
    var segs = net.segments.filter(function (s) { return s.road_id === roadSel.value; });
    segSel.disabled = !segs.length;
    segSel.innerHTML = segs.length
      ? '<option value="">' + (wholeRoad ? "Whole road" : "Choose the stretch…") + "</option>" + segs.map(function (s) {
          return '<option value="' + esc(s.id) + '">' + esc(stretchLabel(net, s)) + "</option>";
        }).join("")
      : '<option value="">' + (roadSel.value ? "No mapped stretches on this road" : "Choose the road first") + "</option>";
    if (selectId) segSel.value = selectId;
  }
  roadSel.addEventListener("change", function () { msg.textContent = ""; fillSegments(); });
  $("#" + prefix + "-here").addEventListener("click", async function () {
    msg.textContent = "Locating…";
    try {
      var pos = await currentPosition();
      var best = null, bestM = Infinity;
      net.segments.forEach(function (s) {
        var m = metresToSegment(net, s, pos.lat, pos.lng);
        if (m < bestM) { bestM = m; best = s; }
      });
      if (!best) { msg.textContent = "No mapped roads to match against"; return; }
      roadSel.value = best.road_id;
      fillSegments(best.id);
      var km = (bestM / 1000).toFixed(1);
      msg.textContent = bestM > 2000
        ? "Nearest mapped road is " + km + " km away — check this is right"
        : "Matched to the nearest mapped road (" + km + " km)";
    } catch (e) { msg.textContent = e.message; }
  });
  fillSegments();
  return function () {
    if (!roadSel.value) return null;
    return { road_id: roadSel.value, segment_id: segSel.value || null };
  };
}

/* ---------------- officer: accidents ---------------- */

function accidentVehicleRowHtml() {
  return '<div class="acc-vehicle-row" style="border:1px solid var(--border, #333); border-radius:8px; padding:10px; margin-bottom:8px;">' +
    '<div class="toolbar" style="margin-bottom:6px;"><input type="text" class="acc-plate" placeholder="Plate number" required>' +
      '<button type="button" class="btn ghost sm acc-plate-go">Look up</button></div>' +
    '<div class="toolbar" style="margin-bottom:6px;"><input type="text" class="acc-licence" placeholder="Driver licence no. (optional)">' +
      '<button type="button" class="btn ghost sm acc-licence-go">Look up</button></div>' +
    '<div class="small muted acc-row-result" style="margin-bottom:6px;"></div>' +
    '<div class="toolbar"><input type="text" class="acc-role" placeholder="Role (e.g. at fault, passenger)">' +
      '<button type="button" class="btn ghost sm acc-remove-row">Remove</button></div>' +
  "</div>";
}

function wireAccidentVehicleRow(row) {
  row.querySelector(".acc-plate-go").addEventListener("click", async function () {
    var plate = row.querySelector(".acc-plate").value.trim();
    var out = row.querySelector(".acc-row-result");
    if (!plate) return;
    try {
      var v = await api("/api/vehicles/by-registration/" + encodeURIComponent(plate));
      row.dataset.vehicleId = v.id;
      out.innerHTML = "Vehicle: <b>" + esc(v.registration_number) + "</b> (" + esc(v.make) + " " + esc(v.model) + ")";
    } catch (e) {
      row.dataset.vehicleId = "";
      out.innerHTML = '<span class="err" style="color:var(--err);">' + esc(e.message) + "</span>";
    }
  });
  row.querySelector(".acc-licence-go").addEventListener("click", async function () {
    var licence = row.querySelector(".acc-licence").value.trim();
    var out = row.querySelector(".acc-row-result");
    if (!licence) return;
    try {
      var d = await api("/api/drivers/by-licence/" + encodeURIComponent(licence));
      row.dataset.driverId = d.id;
      out.innerHTML += (out.innerHTML ? " · " : "") + "Driver: <b>" + esc(d.first_name) + " " + esc(d.last_name) + "</b>";
    } catch (e) {
      row.dataset.driverId = "";
      out.innerHTML += (out.innerHTML ? " · " : "") + '<span class="err" style="color:var(--err);">' + esc(e.message) + "</span>";
    }
  });
  row.querySelector(".acc-remove-row").addEventListener("click", function () { row.remove(); });
}

VIEWS.accidents = async function () {
  $("#content").innerHTML =
    '<div class="grid cards" id="acc-stats"></div>' +
    '<div class="row">' +
      '<div class="card"><h3>Report an accident</h3>' +
        '<form id="acc-form">' +
          roadPickerHtml("acc") +
          '<div class="field"><label>Landmark / exact spot (optional)</label><input name="location" maxlength="120" placeholder="e.g. near Manda Hill"></div>' +
          '<div class="field"><label>Date &amp; time</label><input name="occurred_at" type="datetime-local" required></div>' +
          '<div class="field"><label>Severity</label><select name="severity">' +
            '<option value="minor">Minor</option><option value="serious">Serious</option><option value="fatal">Fatal</option>' +
          "</select></div>" +
          '<div class="field"><label>Description (optional)</label><textarea name="description" rows="2"></textarea></div>' +
          '<div class="field"><label>Vehicles involved (optional)</label><div id="acc-vehicles"></div>' +
            '<button type="button" class="btn ghost sm" id="acc-add-vehicle">+ Add vehicle</button></div>' +
          '<button class="btn gold" type="submit">Report accident</button>' +
        "</form></div>" +
      '<div class="card"><h3>Recent accidents</h3><div id="acc-list"><div class="empty">Loading…</div></div></div>' +
    "</div>";

  $("#acc-add-vehicle").addEventListener("click", function () {
    $("#acc-vehicles").insertAdjacentHTML("beforeend", accidentVehicleRowHtml());
    var rows = $$(".acc-vehicle-row", $("#acc-vehicles"));
    wireAccidentVehicleRow(rows[rows.length - 1]);
  });

  var when = $("#acc-form [name=occurred_at]");
  function localNow() {
    var d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 16);
  }
  when.max = localNow();
  when.value = localNow();

  var pickPlace = null;
  try {
    pickPlace = wireRoadPicker("acc", await loadRoadNetwork());
  } catch (err) {
    $("#acc-form").insertAdjacentHTML("afterbegin", '<div class="error-box">Couldn\'t load the road network: ' + esc(err.message) + "</div>");
  }

  $("#acc-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var data = formData(e.target);
    delete data["acc-road"]; delete data["acc-segment"];
    var place = pickPlace && pickPlace();
    if (!place || !place.segment_id) { toast("Choose the road and the stretch where it happened", "err"); return; }
    data.road_id = place.road_id;
    data.segment_id = place.segment_id;
    if (data.occurred_at && new Date(data.occurred_at) > new Date(Date.now() + 10 * 60000)) {
      toast("The accident time can't be in the future", "err"); return;
    }
    data.vehicles = $$(".acc-vehicle-row", $("#acc-vehicles")).map(function (row) {
      var plate = row.querySelector(".acc-plate").value.trim();
      if (!plate) return null;
      var v = { plate_number: plate };
      if (row.dataset.vehicleId) v.vehicle_id = row.dataset.vehicleId;
      if (row.dataset.driverId) v.driver_id = row.dataset.driverId;
      var role = row.querySelector(".acc-role").value.trim();
      if (role) v.role = role;
      return v;
    }).filter(function (v) { return v !== null; });

    var btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api("/api/accidents/", { method: "POST", body: JSON.stringify(data) });
      toast("Accident reported — the stretch is now closed in the route planner", "ok");
      e.target.reset();
      when.max = localNow();
      when.value = localNow();
      $("#acc-segment").disabled = true;
      $("#acc-here-msg").textContent = "";
      $("#acc-vehicles").innerHTML = "";
      await Promise.all([loadAccidentStats(), loadAccidents()]);
    } catch (err) { toast(err.message, "err"); }
    finally { btn.disabled = false; }
  });

  await loadAccidentStats();
  await loadAccidents();
};

async function loadAccidentStats() {
  try {
    var s = await api("/api/accidents/stats");
    $("#acc-stats").innerHTML =
      kpi("Total accidents", s.total) +
      kpi("Minor", s.by_severity.minor || 0) +
      kpi("Serious", s.by_severity.serious || 0) +
      kpi("Fatal", s.by_severity.fatal || 0);
  } catch (e) { $("#acc-stats").innerHTML = ""; }
}

async function loadAccidents() {
  var list = $("#acc-list");
  list.innerHTML = '<div class="empty">Loading…</div>';
  try {
    var items = await api("/api/accidents/");
    if (!items.length) { list.innerHTML = '<div class="empty">No accidents recorded yet.</div>'; return; }
    list.innerHTML = '<div class="table-wrap"><table><tr><th>Severity</th><th>Road</th><th>Location</th><th>When</th><th></th></tr>' +
      items.map(function (a) {
        var sevCls = a.severity === "fatal" ? "red" : a.severity === "serious" ? "amber" : "blue";
        var road = a.on_road ? '<span class="pill red">blocking</span>' : a.incident_id ? '<span class="pill green">cleared</span>' : '<span class="pill gray">—</span>';
        return "<tr><td><span class='pill " + sevCls + "'>" + esc(a.severity) + "</span></td><td>" + road + "</td><td>" + esc(a.location) + '</td><td class="small">' +
          dt(a.occurred_at) + '</td><td><button class="btn ghost sm acc-view" data-id="' + esc(a.id) + '">Vehicles</button>' +
          (a.on_road ? ' <button class="btn sm acc-clear" data-id="' + esc(a.id) + '">Clear from road</button>' : "") + "</td></tr>";
      }).join("") + "</table></div>";
    $$("#acc-list .acc-clear").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        if (!confirm("Mark the scene as cleared? The stretch reopens in the route planner and the alert is removed.")) return;
        btn.disabled = true;
        try {
          await api("/api/accidents/" + btn.dataset.id + "/clear-road", { method: "POST" });
          toast("Road reopened", "ok");
          await loadAccidents();
        } catch (e) { toast(e.message, "err"); btn.disabled = false; }
      });
    });
    $$("#acc-list .acc-view").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        try {
          var vs = await api("/api/accidents/" + btn.dataset.id + "/vehicles");
          toast(vs.length
            ? vs.map(function (v) { return v.plate_number + (v.role ? " (" + v.role + ")" : ""); }).join(", ")
            : "No vehicles recorded for this accident", "ok");
        } catch (e) { toast(e.message, "err"); }
      });
    });
  } catch (e) {
    list.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

/* ---------------- officer: ANPR ---------------- */

VIEWS.anpr = async function () {
  $("#content").innerHTML =
    '<div class="row">' +
      '<div class="card"><h3>Record an ANPR sighting</h3>' +
        '<form id="anpr-form">' +
          '<div class="field"><label>Plate number</label><input name="plate_number" placeholder="e.g. BAK 123" required></div>' +
          '<div class="field"><label>Location</label><input name="location" placeholder="e.g. Great East Road, camera 4" required></div>' +
          '<div class="field"><label>Camera ID (optional)</label><input name="camera_id" placeholder="e.g. CAM-04"></div>' +
          '<div class="field"><label>Confidence 0-1 (optional)</label><input name="confidence" type="number" min="0" max="1" step="0.01" placeholder="e.g. 0.94"></div>' +
          '<div class="field"><label>Image URL (optional)</label><input name="image_url" placeholder="https://…"></div>' +
          '<div class="field"><label>Date &amp; time (optional, defaults to now)</label><input name="timestamp" type="datetime-local"></div>' +
          '<button class="btn gold" type="submit">Record sighting</button>' +
        "</form></div>" +
      '<div class="card"><h3>Recent sightings</h3>' +
        '<div class="toolbar"><input type="text" id="anpr-filter" placeholder="Exact plate number…">' +
        '<button class="btn ghost sm" id="anpr-filter-go">Filter</button></div>' +
        '<div id="anpr-list"><div class="empty">Loading…</div></div></div>' +
    "</div>";

  $("#anpr-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var data = formData(e.target);
    if (data.confidence) data.confidence = Number(data.confidence);
    if (data.timestamp) data.timestamp = new Date(data.timestamp).toISOString();
    try {
      await api("/api/anpr/events", { method: "POST", body: JSON.stringify(data) });
      toast("ANPR sighting recorded", "ok");
      e.target.reset();
      await loadAnprEvents();
    } catch (err) { toast(err.message, "err"); }
  });

  $("#anpr-filter-go").addEventListener("click", loadAnprEvents);
  $("#anpr-filter").addEventListener("keydown", function (e) { if (e.key === "Enter") loadAnprEvents(); });

  await loadAnprEvents();
};

async function loadAnprEvents() {
  var list = $("#anpr-list");
  list.innerHTML = '<div class="empty">Loading…</div>';
  var plate = $("#anpr-filter").value.trim();
  try {
    var items = await api("/api/anpr/events" + (plate ? "?plate_number=" + encodeURIComponent(plate) : ""));
    if (!items.length) { list.innerHTML = '<div class="empty">No ANPR sightings recorded yet.</div>'; return; }
    list.innerHTML = '<div class="table-wrap"><table><tr><th>Plate</th><th>Matched vehicle</th><th>Location</th><th>Camera</th><th>Confidence</th><th>When</th></tr>' +
      items.map(function (a) {
        return "<tr><td class='mono'><b>" + esc(a.plate_number) + "</b></td><td>" +
          (a.vehicle_id ? '<span class="pill green">known</span>' : '<span class="pill gray">unregistered</span>') + "</td><td>" +
          esc(a.location) + "</td><td>" + esc(a.camera_id || "—") + "</td><td>" +
          (a.confidence == null ? "—" : Math.round(a.confidence * 100) + "%") + '</td><td class="small">' + dt(a.timestamp) + "</td></tr>";
      }).join("") + "</table></div>";
  } catch (e) {
    list.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

/* ---------------- officer: violations ---------------- */

VIEWS.violations = async function () {
  // Officers issue violations, and so do admins -- the API has always allowed
  // both (OFFICERS = (OFFICER, ADMIN) in app/core/security.py), so gating the
  // form to officers alone left admins unable to record from the panel even
  // though the endpoint would have accepted the request.
  var canRecord = USER.role === "officer" || USER.role === "admin";
  $("#content").innerHTML =
    '<div class="row">' +
      (canRecord ? '<div class="card"><h3>Record a violation</h3>' +
        '<form id="vio-form">' +
          '<div class="field"><label>Vehicle plate (lookup)</label>' +
            '<div class="toolbar" style="margin-bottom:0;"><input type="text" id="plate-lookup" placeholder="e.g. BAK 123">' +
            '<button type="button" class="btn ghost sm" id="plate-go">Look up</button></div>' +
            '<div class="small muted" id="plate-result" style="margin-top:6px;"></div></div>' +
          '<div class="field"><label>Vehicle ID (auto-filled by lookup)</label><input name="vehicle_id" id="vio-vehicle" placeholder="UUID or leave empty"></div>' +
          '<div class="field"><label>Driver licence number (lookup)</label>' +
            '<div class="toolbar" style="margin-bottom:0;"><input type="text" id="licence-lookup" placeholder="e.g. DL-2024-00123">' +
            '<button type="button" class="btn ghost sm" id="licence-go">Look up</button></div>' +
            '<div class="small muted" id="licence-result" style="margin-top:6px;"></div></div>' +
          '<div class="field"><label>Driver ID (auto-filled by lookup)</label><input name="driver_id" id="vio-driver" placeholder="UUID or leave empty"></div>' +
          '<div class="field"><label>Violation type</label><select name="violation_type" id="vio-type">' +
            '<optgroup label="Driver offence — liable to the driver, licence at risk">' +
              VO_DRIVER_TYPES.map(function (t) { return '<option value="' + t[0] + '">' + t[1] + "</option>"; }).join("") +
            "</optgroup>" +
            '<optgroup label="Vehicle offence — liable to the owner, vehicle impoundable">' +
              VO_VEHICLE_TYPES.map(function (t) { return '<option value="' + t[0] + '">' + t[1] + "</option>"; }).join("") +
            "</optgroup>" +
            '<optgroup label="Both — driver and owner may be liable">' +
              VO_BOTH_TYPES.map(function (t) { return '<option value="' + t[0] + '">' + t[1] + "</option>"; }).join("") +
            "</optgroup>" +
            "</select>" +
            '<div class="small muted" id="vio-hint" style="margin-top:6px;"></div></div>' +
          '<div class="field"><label>Location</label><input name="location" placeholder="e.g. Great East Road, toll gate 3" required></div>' +
          '<div class="field"><label>Speed at the time (km/h)</label>' +
            '<input name="speed_kmh" type="number" inputmode="decimal" step="0.1" min="0" max="500" placeholder="e.g. 112.5">' +
            '<div class="small muted" style="margin-top:6px;">Optional, but record it whenever it was measured — a speeding ticket is not defensible without the reading.</div></div>' +
          '<div class="field"><label>Date &amp; time (optional)</label><input name="timestamp" type="datetime-local"></div>' +
          '<div class="field"><label>Description (optional)</label><textarea name="description" rows="2"></textarea></div>' +
          '<button class="btn gold" type="submit" id="vio-save">Record &amp; generate e-challan</button>' +
        "</form></div>" : "") +
      '<div class="card"><h3>' + (canRecord ? "Recent violations" : "All recorded violations") + "</h3>" +
        (canRecord ? '<div class="small muted" style="margin:-4px 0 10px;">Showing the 50 most recent.</div>' : "") +
        '<div id="vio-list"><div class="empty">Loading…</div></div></div>' +
    "</div>";

  if (canRecord) {
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

  $("#licence-go").addEventListener("click", async function () {
    var licence = $("#licence-lookup").value.trim();
    var out = $("#licence-result");
    if (!licence) return;
    try {
      var d = await api("/api/drivers/by-licence/" + encodeURIComponent(licence));
      $("#vio-driver").value = d.id;
      out.innerHTML = 'Found <b>' + esc(d.first_name) + " " + esc(d.last_name) + "</b> · " + statusPill(d.status);
    } catch (e) {
      out.innerHTML = '<span class="err" style="color:var(--err);">' + esc(e.message) + "</span>";
    }
  });

  function refreshOffenceHint() {
    var sel = $("#vio-type");
    var hint = $("#vio-hint");
    if (!sel || !hint) return;
    var value = sel.value;
    if (VO_LICENCE_TYPES.indexOf(value) !== -1) {
      hint.innerHTML = "Driver offence: liable to the driver. Counts towards licence points, endorsement or disqualification.";
    } else if (VO_IMPOUND_TYPES.indexOf(value) !== -1) {
      hint.innerHTML = "Vehicle offence: liable to the registered owner or keeper. The vehicle can be impounded or taken off the road.";
    } else {
      hint.innerHTML = "Overlapping offence: both the driver and the owner can be held liable.";
    }
  }

  $("#vio-type").addEventListener("change", refreshOffenceHint);
  refreshOffenceHint();

  $("#vio-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var data = formData(e.target);
    if (!data.vehicle_id && !data.driver_id) {
      toast("Look up a vehicle plate or a driver licence first — a violation needs at least one.", "err");
      return;
    }
    var btn = $("#vio-save");
    btn.disabled = true;
    try {
      var created = await api("/api/enforcement/violations", { method: "POST", body: JSON.stringify(data) });
      toast("Violation recorded — challan generated", "ok");
      e.target.reset();
      loadViolations();
    } catch (err) { toast(err.message, "err"); }
    finally { btn.disabled = false; }
  });
  }

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

/* ---------------- officer: inspections & fitness certificates ---------------- */

var CURRENT_INSPECTION_VEHICLE = null;

VIEWS.inspections = async function () {
  $("#content").innerHTML =
    '<div class="row">' +
      '<div class="card"><h3>Vehicle inspections</h3>' +
        '<div class="field"><label>Vehicle plate (lookup)</label>' +
          '<div class="toolbar" style="margin-bottom:0;"><input type="text" id="insp-plate" placeholder="e.g. BAK 123">' +
          '<button type="button" class="btn ghost sm" id="insp-plate-go">Look up</button></div>' +
          '<div class="small muted" id="insp-vehicle-result" style="margin-top:6px;"></div></div>' +
        '<form id="insp-schedule-form" style="display:none;">' +
          '<div class="field"><label>Inspection centre</label><input name="inspection_centre" placeholder="e.g. Lusaka Main VID Centre" required></div>' +
          '<div class="field"><label>Scheduled date &amp; time</label><input name="scheduled_date" type="datetime-local" required></div>' +
          '<button class="btn gold" type="submit">Schedule inspection</button>' +
        "</form>" +
        '<form id="insp-result-form" style="display:none; margin-top:16px; border-top:1px solid var(--border, #333); padding-top:16px;">' +
          '<h4 style="margin:0 0 8px;">Record result</h4>' +
          '<div class="field"><label>Result</label><select name="result"><option value="passed">Passed</option><option value="failed">Failed</option></select></div>' +
          '<div class="field"><label>Findings (optional)</label><textarea name="findings" rows="2"></textarea></div>' +
          '<div class="field"><label>Inspected by (optional)</label><input name="inspected_by" placeholder="Inspector name"></div>' +
          '<button class="btn gold" type="submit">Save result</button> ' +
          '<button type="button" class="btn ghost" id="insp-result-cancel">Cancel</button>' +
        "</form>" +
      "</div>" +
      '<div class="card"><h3>Inspections for this vehicle</h3><div id="insp-list"><div class="empty">Look up a vehicle first.</div></div></div>' +
    "</div>";

  CURRENT_INSPECTION_VEHICLE = null;

  $("#insp-plate-go").addEventListener("click", async function () {
    var plate = $("#insp-plate").value.trim();
    var out = $("#insp-vehicle-result");
    if (!plate) return;
    try {
      var v = await api("/api/vehicles/by-registration/" + encodeURIComponent(plate));
      CURRENT_INSPECTION_VEHICLE = v.id;
      out.innerHTML = 'Found <b>' + esc(v.registration_number) + "</b> (" + esc(v.make) + " " + esc(v.model) + ") · " + statusPill(v.status);
      $("#insp-schedule-form").style.display = "";
      $("#insp-result-form").style.display = "none";
      await loadInspectionsForVehicle(v.id);
    } catch (e) {
      CURRENT_INSPECTION_VEHICLE = null;
      out.innerHTML = '<span class="err" style="color:var(--err);">' + esc(e.message) + "</span>";
      $("#insp-schedule-form").style.display = "none";
      $("#insp-list").innerHTML = '<div class="empty">Look up a vehicle first.</div>';
    }
  });

  $("#insp-schedule-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    if (!CURRENT_INSPECTION_VEHICLE) return;
    var data = formData(e.target);
    data.vehicle_id = CURRENT_INSPECTION_VEHICLE;
    if (data.scheduled_date) data.scheduled_date = new Date(data.scheduled_date).toISOString();
    try {
      await api("/api/inspections/", { method: "POST", body: JSON.stringify(data) });
      toast("Inspection scheduled", "ok");
      e.target.reset();
      await loadInspectionsForVehicle(CURRENT_INSPECTION_VEHICLE);
    } catch (err) { toast(err.message, "err"); }
  });

  $("#insp-result-cancel").addEventListener("click", function () {
    $("#insp-result-form").style.display = "none";
  });

  $("#insp-result-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var id = e.target.dataset.inspectionId;
    if (!id) return;
    var data = formData(e.target);
    try {
      await api("/api/inspections/" + id, { method: "PATCH", body: JSON.stringify(data) });
      toast("Inspection result recorded" + (data.result === "passed" ? " — fitness certificate issued" : ""), "ok");
      e.target.style.display = "none";
      await loadInspectionsForVehicle(CURRENT_INSPECTION_VEHICLE);
    } catch (err) { toast(err.message, "err"); }
  });
};

async function loadInspectionsForVehicle(vehicleId) {
  var list = $("#insp-list");
  list.innerHTML = '<div class="empty">Loading…</div>';
  try {
    var items = await api("/api/inspections/vehicle/" + vehicleId);
    if (!items.length) { list.innerHTML = '<div class="empty">No inspections recorded for this vehicle yet.</div>'; return; }
    list.innerHTML = '<div class="table-wrap"><table><tr><th>Centre</th><th>Scheduled</th><th>Result</th><th>Findings</th><th></th></tr>' +
      items.map(function (i) {
        var action = i.result === "pending"
          ? '<button class="btn ghost sm insp-record" data-id="' + esc(i.id) + '">Record result</button>'
          : (i.result === "passed" ? '<button class="btn ghost sm insp-cert" data-id="' + esc(i.id) + '">View certificate</button>' : "");
        return "<tr><td>" + esc(i.inspection_centre) + '</td><td class="small">' + dt(i.scheduled_date) + "</td><td>" + statusPill(i.result) + "</td><td>" +
          esc(i.findings || "—") + "</td><td>" + action + "</td></tr>";
      }).join("") + "</table></div>";

    $$("#insp-list .insp-record").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var form = $("#insp-result-form");
        form.dataset.inspectionId = btn.dataset.id;
        form.style.display = "";
        form.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    });
    $$("#insp-list .insp-cert").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        try {
          var cert = await api("/api/inspections/" + btn.dataset.id + "/fitness-certificate");
          toast("Certificate " + cert.certificate_number + " · valid until " + dt(cert.expiry_date), "ok");
        } catch (e) { toast(e.message, "err"); }
      });
    });
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
          '<button class="btn gold" type="submit">Process event</button> ' +
          '<button class="btn ghost" type="button" id="toll-sync">Sync queued events</button></form>' +
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
      if (/Network error/i.test(err.message)) {
        var queued = formData(e.target);
        queued.device_event_id = deviceId() + "-" + Date.now();
        queued.occurred_at = new Date().toISOString();
        queued.cached_issues = ["Compliance check captured while offline"];
        queued.cached_checks = [];
        var pending = offlineTollQueue();
        pending.push(queued);
        saveOfflineTollQueue(pending);
        out.innerHTML = '<div class="pill amber">Saved locally. It will sync when connectivity returns.</div>';
        e.target.reset();
      } else {
        out.innerHTML = '<div class="error-box">' + esc(err.message) + "</div>";
      }
    } finally { btn.disabled = false; }
  });

  $("#toll-sync").addEventListener("click", async function () {
    var out = $("#toll-result");
    try {
      var count = await syncOfflineTollEvents();
      out.innerHTML = '<div class="pill green">Synchronized ' + count + " queued event(s).</div>";
      loadTollEvents();
    } catch (e) { out.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>"; }
  });

  await loadTollEvents();
  if (offlineTollQueue().length && navigator.onLine) {
    try { await syncOfflineTollEvents(); loadTollEvents(); } catch (e) { /* retry from the sync button */ }
  }
};

async function loadTollEvents() {
  var list = $("#toll-list");
  list.innerHTML = '<div class="empty">Loading…</div>';
  try {
    var items = await api("/api/toll/transactions");
    list.innerHTML = items.length
      ? '<div class="table-wrap"><table><tr><th>Plate</th><th>Gate</th><th>Result</th><th>When</th></tr>' + items.map(function (t) {
          return "<tr><td class='mono'><b>" + esc(t.plate_number) + "</b></td><td>" + esc(t.gate_id) + "</td><td>" + (t.compliance_result === "compliant" ? '<span class="pill green">compliant</span>' : '<span class="pill red">flagged</span>') + '</td><td class="small">' + dt(t.timestamp) + "</td></tr>";
        }).join("") + "</table></div>"
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
    list.innerHTML = '<div class="table-wrap"><table><tr><th>Ref</th><th>Type</th><th>Category</th><th>Amount</th><th>Due</th><th>Status</th><th></th></tr>' +
      items.map(function (f) {
        var pay = f.status !== "paid"
          ? '<button class="btn gold sm finpay" data-id="' + esc(f.id) + '" data-ref="' + esc(f.reference) + '">Pay now</button>'
          : "";
        return "<tr><td class='mono'>" + esc(f.reference) + "</td><td>" + esc(offenceTypeLabel(f.violation_type)) + "</td><td>" + categoryPill(f.category) + "</td><td>" + money(f.penalty_amount) + '</td><td class="small">' + dt(f.due_date) + "</td><td>" + statusPill(f.status) + "</td><td>" + pay + "</td></tr>";
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
      '<div class="table-wrap"><table>' +
        "<tr><td>Licence number</td><td class='mono'><b>" + esc(l.licence_number) + "</b></td></tr>" +
        "<tr><td>Class</td><td>" + esc(l.licence_class) + "</td></tr>" +
        "<tr><td>Status</td><td>" + statusPill(l.status) + "</td></tr>" +
        "<tr><td>Issue date</td><td>" + dt(l.issue_date) + "</td></tr>" +
        "<tr><td>Expiry date</td><td>" + dt(l.expiry_date) + " (" + Number(l.days_until_expiry) + " days left)</td></tr>" +
        "<tr><td>Restrictions</td><td>" + esc(l.restrictions || "None") + "</td></tr>" +
      "</table></div>" +
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

var INCIDENT_TYPE_LABELS = {
  accident: "Accident", road_closed: "Road closed", maintenance: "Maintenance",
  congestion: "Congestion", roadworks: "Roadworks"
};

function isRoadStaff() { return USER && (USER.role === "officer" || USER.role === "admin"); }

// Where a report stands with RTSA. Mirrors IncidentVerification in app/models/road_network.py.
var VERIFICATION_LABELS = {
  official: ["Official", "blue"],
  unverified: ["Unverified", "amber"],
  confirmed: ["Confirmed", "green"],
  dismissed: ["Closed, no action", "gray"],
  "false": ["Found false", "red"]
};

function verificationPill(v) {
  var l = VERIFICATION_LABELS[v] || [v, "gray"];
  return '<span class="pill ' + l[1] + '">' + esc(l[0]) + "</span>";
}

VIEWS.alerts = async function () {
  var staff = isRoadStaff();
  $("#content").innerHTML =
    (staff ? '<div class="row">' : "") +
    '<div class="card"><h3>Active road alerts</h3>' +
      '<div class="small muted" style="margin:-4px 0 8px;">Accidents and closures here are routed around in the <a href="#/planner">route planner</a>' +
        (staff ? ", including citizen reports still waiting for review." : ".") + "</div>" +
      (USER.role === "citizen" ? '<p style="margin:0 0 10px;"><a class="btn gold sm" href="#/report">Report an incident</a></p>' : "") +
      '<div id="alert-list"><div class="empty">Loading…</div></div></div>' +
    (staff
      ? '<div class="card"><h3>Report a road incident</h3><form id="inc-form">' +
          roadPickerHtml("inc") +
          '<div class="field"><label>Type</label><select name="incident_type">' +
            Object.keys(INCIDENT_TYPE_LABELS).filter(function (k) { return k !== "accident"; }).map(function (k) {
              return '<option value="' + k + '">' + INCIDENT_TYPE_LABELS[k] + "</option>";
            }).join("") + "</select>" +
            '<div class="small muted field-hint">Accidents are reported from the <a href="#/accidents">Accidents</a> screen so the case is recorded too.</div></div>' +
          '<div class="field"><label>Severity</label><select name="severity"><option value="minor">Minor</option><option value="serious">Serious</option><option value="fatal">Fatal</option></select></div>' +
          '<div class="field"><label>Description</label><textarea name="description" rows="2" placeholder="What drivers should know"></textarea></div>' +
          '<button class="btn gold" type="submit">Publish alert</button></form></div></div>'
      : "");

  if (staff) {
    var pickPlace = null;
    try { pickPlace = wireRoadPicker("inc", await loadRoadNetwork(), true); }
    catch (err) { toast("Couldn't load the road network: " + err.message, "err"); }
    $("#inc-form").addEventListener("submit", async function (e) {
      e.preventDefault();
      var data = formData(e.target);
      var place = pickPlace && pickPlace();
      if (!place) { toast("Choose the road", "err"); return; }
      data.road_id = place.road_id;
      if (place.segment_id) data.segment_id = place.segment_id;
      var btn = e.target.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        await api("/api/incidents/", { method: "POST", body: JSON.stringify(data) });
        toast("Alert published", "ok");
        e.target.reset();
        $("#inc-segment").disabled = true;
        $("#inc-here-msg").textContent = "";
        await loadAlertList(true);
      } catch (err) { toast(err.message, "err"); }
      finally { btn.disabled = false; }
    });
  }
  await loadAlertList();
};

function alertHtml(a, staff) {
  var cls = a.severity === "fatal" ? "red" : a.severity === "serious" ? "amber" : "blue";
  var review = a.verification === "unverified";
  var who = "";
  if (staff && a.reporter) {
    var r = a.reporter;
    who = '<div class="small">Reported by <b>' + esc(r.name) + "</b>" +
      (r.nrc ? ' · NRC <span class="mono">' + esc(r.nrc) + "</span>" : "") +
      (r.phone ? " · " + esc(r.phone) : "") +
      (r.false_reports ? ' · <span class="pill red">' + Number(r.false_reports) + " false report" + (r.false_reports === 1 ? "" : "s") + "</span>" : "") +
      "</div>";
  } else if (a.citizen_report) {
    who = '<div class="small muted">' + (a.mine ? "Your report" : "Reported by a motorist") +
      (review ? ", waiting for an officer to verify it" : "") + "</div>";
  }
  var actions = "";
  if (staff && review) {
    // Dismiss / false open an inline reason box: the reporter sees the reason, and
    // a false report is fined, so the officer has to say why.
    actions = '<div class="review-actions">' +
      '<button type="button" class="btn gold sm inc-confirm" data-id="' + esc(a.id) + '">Confirm</button>' +
      '<button type="button" class="btn ghost sm inc-review" data-mode="dismiss">Dismiss</button>' +
      '<button type="button" class="btn ghost sm inc-review" data-mode="false" style="color:var(--err);">False report, fine reporter</button></div>' +
      '<form class="review-form hidden" data-id="' + esc(a.id) + '" style="margin-top:8px;">' +
        '<div class="small review-hint" style="margin-bottom:6px;"></div>' +
        '<div class="field" style="margin:0 0 6px;"><textarea rows="2" maxlength="500" required aria-label="Reason the reporter will see"></textarea></div>' +
        '<div class="review-actions"><button type="submit" class="btn sm review-submit"></button>' +
        '<button type="button" class="btn ghost sm review-cancel">Cancel</button></div></form>';
  } else if (staff) {
    actions = ' · <a href="#" class="inc-resolve" data-id="' + esc(a.id) + '">Mark resolved</a>';
  }
  return '<div class="report-row">' +
    "<b>" + esc(INCIDENT_TYPE_LABELS[a.incident_type] || a.incident_type) + "</b> · <span class='pill " + cls + "'>" + esc(a.severity) + "</span>" +
    (a.citizen_report ? " " + verificationPill(a.verification) : "") +
    (a.road ? ' <span class="muted">on <b>' + esc(a.road) + "</b></span>" : ' <span class="muted">(no road recorded)</span>') +
    (a.stretch ? '<div class="small muted">' + esc(a.stretch) + (a.blocking ? " · closed in the route planner" : "") + "</div>" : "") +
    (a.description ? '<div class="small">' + esc(a.description) + "</div>" : "") +
    who +
    '<div class="small muted">Since ' + dt(a.started_at) + (review ? "" : actions) + "</div>" +
    (review ? actions : "") +
  "</div>";
}

// force: reload even if an officer is part-way through writing a review reason
// (live updates call this without it, so a new report can't wipe their text).
async function loadAlertList(force) {
  var list = $("#alert-list");
  if (!list) return;
  var staff = isRoadStaff();
  var drafting = $$("#alert-list .review-form:not(.hidden) textarea").some(function (t) { return t.value.trim(); });
  if (drafting && !force) { toast("Road alerts changed — the list refreshes when you finish this review"); return; }
  try {
    var items = await api("/api/incidents/alerts");
    var fine = 0;
    if (staff && items.some(function (a) { return a.verification === "unverified"; })) {
      try { fine = (await api("/api/incidents/reporting-status")).fine_amount; } catch (e) { /* hint just omits it */ }
    }
    // Reports waiting for review go first so officers see them straight away.
    if (staff) items.sort(function (x, y) { return (y.verification === "unverified") - (x.verification === "unverified"); });
    list.innerHTML = items.length
      ? items.map(function (a) { return alertHtml(a, staff); }).join("")
      : '<div class="empty">No active alerts.</div>';

    $$("#alert-list .inc-resolve").forEach(function (link) {
      link.addEventListener("click", async function (e) {
        e.preventDefault();
        if (!confirm("Mark this incident resolved? The road reopens in the route planner.")) return;
        try {
          await api("/api/incidents/" + link.dataset.id + "/resolve", { method: "POST" });
          toast("Incident resolved", "ok");
          await loadAlertList(true);
        } catch (err) { toast(err.message, "err"); }
      });
    });
    $$("#alert-list .inc-confirm").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        btn.disabled = true;
        try {
          await api("/api/incidents/" + btn.dataset.id + "/confirm", { method: "POST" });
          toast("Report confirmed — motorists have been alerted", "ok");
          await loadAlertList(true);
        } catch (err) { toast(err.message, "err"); btn.disabled = false; }
      });
    });
    $$("#alert-list .inc-review").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var row = btn.closest(".report-row"), form = row.querySelector(".review-form");
        var falseReport = btn.dataset.mode === "false";
        form.dataset.mode = btn.dataset.mode;
        form.querySelector(".review-hint").innerHTML = falseReport
          ? '<span style="color:var(--err);">The reporter is fined' + (fine ? " <b>" + esc(money(fine)) + "</b>" : "") +
            " and it counts towards suspending their reporting.</span> Say what shows the report is false; they will see this."
          : "Closes the report and reopens the road. The reporter is not penalised and sees your reason.";
        form.querySelector("textarea").placeholder = falseReport
          ? "e.g. Officer at the scene at 14:05: no collision, traffic flowing"
          : "e.g. Scene already cleared on arrival";
        var submit = form.querySelector(".review-submit");
        submit.textContent = falseReport ? "Fine reporter" : "Close report";
        submit.className = "btn sm review-submit " + (falseReport ? "gold" : "ghost");
        form.classList.remove("hidden");
        form.querySelector("textarea").focus();
      });
    });
    $$("#alert-list .review-cancel").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var form = btn.closest(".review-form");
        form.querySelector("textarea").value = "";
        form.classList.add("hidden");
      });
    });
    $$("#alert-list .review-form").forEach(function (form) {
      form.addEventListener("submit", async function (e) {
        e.preventDefault();
        var reason = form.querySelector("textarea").value.trim();
        if (reason.length < 5) { toast("Give the reason in a few words", "err"); return; }
        var falseReport = form.dataset.mode === "false";
        var submit = form.querySelector(".review-submit");
        submit.disabled = true;
        try {
          await api("/api/incidents/" + form.dataset.id + "/dismiss", {
            method: "POST", body: JSON.stringify({ false_report: falseReport, reason: reason })
          });
          toast(falseReport ? "Marked false — the reporter has been fined" : "Report closed — the road reopens in the route planner", "ok");
          await loadAlertList(true);
        } catch (err) { toast(err.message, "err"); submit.disabled = false; }
      });
    });
  } catch (e) {
    list.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

/* ---------------- citizen: report a road incident ---------------- */

// Citizens report what they see. It goes on the road feed and closes the stretch
// in everyone's route planner at once, marked unverified until an officer
// reviews it. Reports are tied to the reporter's NRC; a false one is fined.
var REPORT_TYPES = ["accident", "road_closed", "congestion", "roadworks"];
var REPORT_STANDING = null;

VIEWS.report = async function () {
  $("#content").innerHTML =
    '<div class="row">' +
      '<div class="card"><h3>Report a road incident</h3>' +
        '<div id="rep-standing"></div>' +
        '<form id="rep-form" novalidate>' +
          '<div class="field"><label id="rep-type-label">What is happening?</label><div class="choice-row" role="group" aria-labelledby="rep-type-label">' +
            REPORT_TYPES.map(function (t, i) {
              return '<button type="button" class="btn ghost sm rep-type" data-type="' + t + '" aria-pressed="' + (i === 0) + '">' + esc(INCIDENT_TYPE_LABELS[t]) + "</button>";
            }).join("") + "</div></div>" +
          roadPickerHtml("rep") +
          '<div class="field"><label for="rep-severity">How serious is it?</label><select id="rep-severity" name="severity">' +
            '<option value="minor">Minor: traffic still moving</option><option value="serious" selected>Serious: lane or road blocked, injuries</option><option value="fatal">Fatal</option></select></div>' +
          '<div class="field"><label for="rep-description">Details (optional)</label><textarea id="rep-description" name="description" rows="2" maxlength="1000" placeholder="e.g. two cars blocking the left lane near the filling station"></textarea></div>' +
          '<label class="declare"><input type="checkbox" id="rep-declare" aria-labelledby="rep-declare-text"> <span id="rep-declare-text">I confirm this report is true and happening now.</span></label>' +
          '<button class="btn gold" type="submit" id="rep-submit">Send report</button>' +
        "</form></div>" +
      '<div class="card"><h3>My reports</h3><div id="rep-mine"><div class="empty">Loading…</div></div></div>' +
    "</div>";

  var type = REPORT_TYPES[0];
  $$(".rep-type").forEach(function (b) {
    b.addEventListener("click", function () {
      type = b.dataset.type;
      $$(".rep-type").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
    });
  });

  var pickPlace = null;
  try {
    pickPlace = wireRoadPicker("rep", await loadRoadNetwork());
  } catch (err) {
    $("#rep-form").insertAdjacentHTML("afterbegin", '<div class="error-box">Couldn\'t load the road network: ' + esc(err.message) + "</div>");
  }

  $("#rep-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var place = pickPlace && pickPlace();
    if (!place || !place.segment_id) { toast("Choose the road and the stretch, or tap “Use my location”", "err"); return; }
    if (!$("#rep-declare").checked) { toast("Tick the box to confirm the report is true", "err"); $("#rep-declare").focus(); return; }
    var body = {
      incident_type: type,
      severity: $("#rep-severity").value,
      road_id: place.road_id,
      segment_id: place.segment_id,
      description: $("#rep-description").value.trim() || null,
      declaration: true
    };
    var btn = $("#rep-submit");
    btn.disabled = true;
    try {
      await api("/api/incidents/", { method: "POST", body: JSON.stringify(body) });
      toast("Thank you — drivers are being routed around it now. An officer will verify your report.", "ok");
      e.target.reset();
      $("#rep-severity").value = "serious";
      $("#rep-road").dispatchEvent(new Event("change")); // empties the stretch list too
      $("#rep-here-msg").textContent = "";
      await Promise.all([loadReportStanding(), loadMyReports()]);
    } catch (err) { toast(err.message, "err"); }
    finally { btn.disabled = !(REPORT_STANDING && REPORT_STANDING.can_report); }
  });

  await Promise.all([loadReportStanding(), loadMyReports()]);

  // Someone reporting is usually standing at the scene: if they've already let
  // the app use their location, find the stretch for them.
  if (pickPlace && REPORT_STANDING && REPORT_STANDING.can_report && navigator.permissions && navigator.permissions.query) {
    navigator.permissions.query({ name: "geolocation" }).then(function (p) {
      if (p.state === "granted" && $("#rep-here") && !$("#rep-road").value) $("#rep-here").click();
    }).catch(function () { /* not supported: the button is still there */ });
  }
};

async function loadReportStanding() {
  var box = $("#rep-standing");
  if (!box) return;
  try {
    var st = REPORT_STANDING = await api("/api/incidents/reporting-status");
    var fine = money(st.fine_amount);
    if (!st.can_report) {
      box.innerHTML = '<div class="error-box">' + esc(st.reason) +
        (st.nrc_on_file ? "" : ' <a href="#/account">Add it now</a>') + "</div>";
    } else {
      box.innerHTML = '<div class="info-box">Your report goes on the route planner straight away so other drivers avoid the road, and an officer then verifies it. ' +
        "Reports are tied to your NRC: one found to be false is fined <b>" + esc(fine) + "</b>, and " +
        Number(st.strike_limit) + " false reports in " + Number(st.window_days) + " days suspend reporting." +
        (st.false_reports ? " You have <b>" + Number(st.false_reports) + "</b> on record." : "") + "</div>";
    }
    $("#rep-declare-text").textContent = "I confirm this report is true and happening now. I understand a false report is fined " + fine + ".";
    $$("#rep-form input, #rep-form select, #rep-form textarea, #rep-form button").forEach(function (el) { el.disabled = !st.can_report; });
    // A stretch can only be picked once a road is chosen.
    if (st.can_report && !$("#rep-road").value) $("#rep-segment").disabled = true;
  } catch (e) {
    box.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

async function loadMyReports() {
  var list = $("#rep-mine");
  if (!list) return;
  try {
    var items = await api("/api/incidents/mine");
    list.innerHTML = items.length
      ? items.map(function (r) {
          var state = r.verification === "confirmed" && !r.is_active ? '<span class="pill gray">Cleared</span>' : verificationPill(r.verification);
          return '<div class="report-row"><b>' + esc(INCIDENT_TYPE_LABELS[r.incident_type] || r.incident_type) + "</b> " + state +
            (r.road_name ? ' <span class="muted">on <b>' + esc(r.road_name) + "</b></span>" : "") +
            (r.stretch ? '<div class="small muted">' + esc(r.stretch) + "</div>" : "") +
            '<div class="small muted">' + dt(r.starts_at) + "</div>" +
            (r.review_note ? '<div class="small">Officer: ' + esc(r.review_note) + "</div>" : "") +
            (r.verification === "false" ? '<div class="small"><a href="#/fines">See the fine under Fines &amp; payments</a></div>' : "") +
          "</div>";
        }).join("")
      : '<div class="empty">You haven\'t reported anything yet.</div>';
  } catch (e) {
    list.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

/* ---------------- route planner (all roles) ---------------- */

VIEWS.planner = async function () {
  $("#content").innerHTML =
    '<div class="planner-layout">' +
      '<div class="planner-top row">' +
        '<div class="card"><h3>Plan a route <span class="subtle">(incident-aware)</span></h3>' +
          '<div class="field"><label>From</label><input id="from-select" class="loc-search" placeholder="Pick a junction…" autocomplete="off"><div class="suggest-list" id="from-suggest"></div></div>' +
          '<div class="toolbar" style="margin:-6px 0 12px;"><button type="button" class="btn ghost sm" id="from-here">Use my current location</button>' +
            '<span class="small muted" id="from-here-msg"></span></div>' +
          '<div class="field"><label>To</label><input id="to-select" class="loc-search" placeholder="Pick a junction…" autocomplete="off"><div class="suggest-list" id="to-suggest"></div></div>' +
          '<label><input type="checkbox" id="avoid-incidents" checked> Avoid incidents / road closures</label>' +
          '<div style="margin-top:12px;"><button class="btn gold" id="plan-btn">Find route</button></div>' +
          '<div id="route-result"></div></div>' +
        '<div class="card"><h3>Live road status</h3><div id="status-board"><div class="empty">Loading…</div></div></div>' +
      '</div>' +
      '<div class="card planner-map-card"><h3>Map view</h3>' +
        '<div class="small muted" style="margin:-4px 0 8px;">Road network with live incident/closure info. Click an intersection marker to inspect it.</div>' +
        '<div id="planner-map"></div>' +
      "</div>" +
    "</div>";

  var fromSel = $("#from-select"), toSel = $("#to-select");
  var inters = [];
  PLANNER_ORIGIN_POS = null;
  try {
    inters = await api("/api/road-network/intersections");
    INTERSECTIONS_BY_NAME = {};
    inters.forEach(function (i) { INTERSECTIONS_BY_NAME[i.name] = [i.latitude, i.longitude]; });
    wireLocationSearch(fromSel, $("#from-suggest"), inters, planRoute);
    wireLocationSearch(toSel, $("#to-suggest"), inters, planRoute);
    fromSel.addEventListener("input", function () {
      // Typing over "my location" means the user picked a junction instead.
      if (PLANNER_ORIGIN_POS) { PLANNER_ORIGIN_POS = null; $("#from-here-msg").textContent = ""; }
    });
  } catch (e) {
    fromSel.value = "";
    toSel.value = "";
    $("#status-board").innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }

  $("#from-here").addEventListener("click", async function () {
    var msg = $("#from-here-msg");
    msg.textContent = "Locating…";
    try {
      var pos = await currentPosition();
      var best = null, bestM = Infinity;
      inters.forEach(function (i) {
        var m = metresBetween(pos.lat, pos.lng, i.latitude, i.longitude);
        if (m < bestM) { bestM = m; best = i; }
      });
      if (!best) { msg.textContent = "No mapped junctions to start from"; return; }
      fromSel.value = best.name;
      fromSel.classList.remove("invalid");
      PLANNER_ORIGIN_POS = { lat: pos.lat, lng: pos.lng, junction: best.name, metres: bestM };
      var km = (bestM / 1000).toFixed(1);
      msg.textContent = bestM > 25000
        ? "You're " + km + " km from the mapped network — starting at its nearest junction"
        : "Nearest junction is " + km + " km from you";
      showOriginOnMap();
      planRoute();
    } catch (e) { msg.textContent = e.message; }
  });

  initPlannerMap();
  renderNetworkOnMap(inters);

  $("#plan-btn").addEventListener("click", planRoute);
  await loadStatusBoard();
};

var PLANNER_ORIGIN_POS = null; // { lat, lng, junction, metres } when "my location" is the start
var PLANNER_INCIDENTS = [];

// Exact (case-insensitive) junction name, or null. Only mapped junctions can be
// routed, so free text never reaches the API.
function knownJunction(name) {
  var n = String(name || "").trim().toLowerCase();
  if (!n) return null;
  var keys = Object.keys(INTERSECTIONS_BY_NAME);
  for (var i = 0; i < keys.length; i++) if (keys[i].toLowerCase() === n) return keys[i];
  return null;
}

function showOriginOnMap() {
  if (!window.L || !plannerMap) return;
  if (plannerMap._rtsaOrigin) { plannerMap.removeLayer(plannerMap._rtsaOrigin); plannerMap._rtsaOrigin = null; }
  if (!PLANNER_ORIGIN_POS) return;
  var p = PLANNER_ORIGIN_POS, j = INTERSECTIONS_BY_NAME[p.junction];
  var g = L.layerGroup();
  L.circleMarker([p.lat, p.lng], { radius: 7, color: "#fff", weight: 2, fillColor: "#2f80ed", fillOpacity: 1 })
    .bindPopup("<b>You are here</b>").addTo(g);
  if (j) {
    L.polyline([[p.lat, p.lng], j], { color: "#2f80ed", weight: 3, dashArray: "4 6", opacity: 0.85 })
      .bindPopup("~" + (p.metres / 1000).toFixed(1) + " km to " + esc(p.junction)).addTo(g);
  }
  g.addTo(plannerMap);
  plannerMap._rtsaOrigin = g;
}

function wireLocationSearch(input, list, inters, onPick) {
  function matches(q) {
    return inters.filter(function (i) {
      return i.name.toLowerCase().indexOf(q) !== -1;
    });
  }
  function render(q) {
    var ql = (q || "").toLowerCase().trim();
    // An empty box lists every junction, so it works like a dropdown too.
    var hits = (ql ? matches(ql) : inters.slice()).slice(0, 50);
    if (!hits.length) {
      list.innerHTML = '<div class="suggest-item muted" data-none="1">No mapped junction matches “' + esc(q) + '”</div>';
      list.style.display = "block";
      return;
    }
    list.innerHTML = hits.map(function (i) {
      return '<div class="suggest-item" data-name="' + esc(i.name) + '">' + esc(i.name) + "</div>";
    }).join("");
    list.style.display = "block";
  }
  function hide() { list.style.display = "none"; }
  function select(active) {
    var el = active || list.querySelector(".suggest-item.select");
    if (!el || el.getAttribute("data-none")) return;
    input.value = el.getAttribute("data-name");
    input.classList.remove("invalid");
    hide();
    onPick();
  }
  input.addEventListener("input", function () { render(input.value); });
  input.addEventListener("focus", function () { render(input.value); });
  input.addEventListener("blur", function () {
    setTimeout(hide, 120);
    // Snap to the canonical name, or flag text that isn't a mapped junction.
    var known = knownJunction(input.value);
    if (known) { input.value = known; input.classList.remove("invalid"); }
    else input.classList.toggle("invalid", !!input.value.trim());
  });
  input.addEventListener("keydown", function (e) {
    var items = $$(".suggest-item", list);
    if (e.key === "ArrowDown" && items.length) {
      e.preventDefault();
      var cur = items.indexOf(list.querySelector(".suggest-item.select"));
      items.forEach(function (el) { el.classList.remove("select"); });
      items[(cur + 1) % items.length].classList.add("select");
    } else if (e.key === "ArrowUp" && items.length) {
      e.preventDefault();
      var cur = items.indexOf(list.querySelector(".suggest-item.select"));
      items.forEach(function (el) { el.classList.remove("select"); });
      items[(cur - 1 + items.length) % items.length].classList.add("select");
    } else if (e.key === "Enter") {
      e.preventDefault();
      var sel = list.querySelector(".suggest-item.select");
      if (sel) { select(sel); } else { planRoute(); }
    } else if (e.key === "Escape") {
      hide();
    }
  });
  list.addEventListener("mousedown", function (e) {
    var el = e.target.closest(".suggest-item");
    if (el) { e.preventDefault(); select(el); }
  });
  list.addEventListener("mouseover", function (e) {
    var el = e.target.closest(".suggest-item");
    if (!el || !el.parentNode) return;
    $$(".suggest-item", list).forEach(function (x) { x.classList.remove("select"); });
    el.classList.add("select");
  });
}

function initPlannerMap() {
  var el = $("#planner-map");
  if (plannerMap) {
    if (el && !document.body.contains(plannerMap.getContainer())) {
      plannerMap.remove();
      plannerMap = null;
    } else {
      setTimeout(function () { plannerMap.invalidateSize(); }, 0);
      return;
    }
  }
  if (!el) return;
  try {
    plannerMap = L.map(el).setView([-15.42, 28.28], 13);
    var tiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      subdomains: "abc",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(plannerMap);
    var tileErrors = 0;
    tiles.on("tileerror", function () {
      tileErrors += 1;
      if (tileErrors === 3 && !plannerMap._rtsaTileWarned) {
        plannerMap._rtsaTileWarned = true;
        var oct = L.control({ position: "topleft" });
        oct.onAdd = function () {
          var d = L.DomUtil.create("div", "leaflet-bar");
          d.style.cssText = "background:#fff;border:1px solid #ccc;border-radius:4px;padding:6px 10px;font-size:12px;";
          d.innerText = "Street tiles unreachable — map shows network only";
          return d;
        };
        oct.addTo(plannerMap);
      }
    });
  } catch (e) {
    plannerMap = null;
  }
}

function renderNetworkOnMap(intersections) {
  if (!window.L || !plannerMap) return;
  var existing = plannerMap._rtsaNetwork;
  if (existing) { plannerMap.removeLayer(existing); plannerMap._rtsaNetwork = null; }
  var group = L.layerGroup();
  if (intersections && intersections.length) {
    var markers = L.layerGroup();
    var markerBounds = L.latLngBounds([]);
    intersections.forEach(function (i) {
      var point = [Number(i.latitude), Number(i.longitude)];
      markerBounds.extend(point);
      var m = L.circleMarker(point, {
        radius: 5, color: "#1e242a", weight: 1,
        fillColor: "#DDAF4D", fillOpacity: 0.9
      }).addTo(markers);
      m.bindPopup("<b>" + esc(i.name) + "</b>");
    });
    group.addLayer(markers);
    plannerMap.fitBounds(markerBounds.pad(0.15));
  }
  loadNetworkGeoJson(function (gj) {
    if (!gj || !gj.features || !window.L || !plannerMap) {
      if (group.getLayers().length) group.addTo(plannerMap);
      return;
    }
    plannerMap._rtsaGeo = gj;
    group.addTo(plannerMap);
    renderIncidentsOnMap();
  });
  plannerMap._rtsaNetwork = group;
}

// Network lines coloured by live state: red where an accident/closure blocks the
// stretch (the planner routes around it), amber where the road has an incident.
async function renderIncidentsOnMap() {
  if (!window.L || !plannerMap || !plannerMap._rtsaGeo) return;
  try { PLANNER_INCIDENTS = await api("/api/incidents/alerts"); } catch (e) { PLANNER_INCIDENTS = []; }
  if (!plannerMap) return;
  var bySeg = {}, byRoad = {};
  PLANNER_INCIDENTS.forEach(function (a) {
    if (a.segment_id) (bySeg[a.segment_id] = bySeg[a.segment_id] || []).push(a);
    else if (a.road) (byRoad[a.road] = byRoad[a.road] || []).push(a);
  });
  if (plannerMap._rtsaLines) plannerMap.removeLayer(plannerMap._rtsaLines);
  var lines = L.geoJSON(plannerMap._rtsaGeo, {
    style: function (feat) {
      var p = feat.properties || {};
      var here = bySeg[p.id] || [];
      if (here.some(function (a) { return a.blocking; })) return { color: "#e5484d", weight: 5, opacity: 0.95 };
      if (here.length || byRoad[p.road]) return { color: "#f5a524", weight: 4, opacity: 0.9 };
      return { color: "#8a94a0", weight: 2, opacity: 0.6 };
    },
    onEachFeature: function (feat, layer) {
      var p = feat.properties || {};
      var here = (bySeg[p.id] || []).concat(byRoad[p.road] || []);
      layer.bindPopup("<b>" + esc(p.road) + "</b><br>" + p.distance_km + " km · ~" + p.travel_minutes + " min" +
        here.map(function (a) {
          return "<br><b style='color:#e5484d'>" + esc(INCIDENT_TYPE_LABELS[a.incident_type] || a.incident_type) + "</b> (" + esc(a.severity) + ")" +
            (a.verification === "unverified" ? " <i>unverified report</i>" : "") +
            (a.description ? ": " + esc(a.description) : "");
        }).join(""));
    }
  });
  lines.addTo(plannerMap);
  lines.bringToBack();
  plannerMap._rtsaLines = lines;
}

async function loadNetworkGeoJson(onReady) {
  try {
    var gj = await api("/api/road-network/geojson");
    onReady(gj);
  } catch (e) {
    onReady(null);
  }
}

// Called on live road events while the planner is open: refresh the status
// board and map, and re-plan the route on screen without touching the inputs.
async function refreshPlannerLive() {
  if (!$("#status-board")) return;
  await Promise.all([loadStatusBoard(), renderIncidentsOnMap()]);
  if (plannerMap && plannerMap._rtsaRoutes && $("#from-select").value && $("#to-select").value) {
    await planRoute();
    toast("Road conditions changed — your route was updated", "ok");
  } else {
    toast("Road conditions changed — map updated", "ok");
  }
}

function showRouteOnMap(r) {
  if (!window.L || !plannerMap) return;
  clearRouteLayers();
  var routeLayers = [];
  var bounds = [];

  function stepCoords(s) {
    return [INTERSECTIONS_BY_NAME[s.from_intersection], INTERSECTIONS_BY_NAME[s.to_intersection]]
      .filter(Boolean);
  }

  function coordsFor(rt) {
    // Prefer real road geometry from OSRM; fall back to intersection points.
    if (rt.geometry && rt.geometry.length >= 2) {
      return rt.geometry.map(function (p) { return [Number(p[0]), Number(p[1])]; });
    }
    var pts = [];
    rt.steps.forEach(function (s) {
      var c = stepCoords(s);
      if (c.length) pts.push.apply(pts, c);
    });
    return pts;
  }

  (r.alternatives || []).forEach(function (alt) {
    var pts = coordsFor(alt);
    if (pts.length < 2) return;
    var line = L.polyline(pts, { color: "#5a6570", weight: 3, dashArray: "6 8", opacity: 0.75 });
    line.bindPopup("<b>Alternative</b> · " + alt.total_distance_km + " km");
    routeLayers.push(line);
    bounds = bounds.concat(pts);
  });

  var primary = r.primary_route;
  if (primary) {
    var pts = coordsFor(primary);
    if (pts.length > 1) {
      var line = L.polyline(pts, { color: "#c69a34", weight: 5, opacity: 0.95 });
      line.bindPopup("<b>Primary route</b> · " + primary.total_distance_km + " km");
      routeLayers.push(line);
      bounds = bounds.concat(pts);
    }
  }

  if (bounds.length) {
    // Circle markers: the default L.marker pin images come from the CDN, which
    // the Content-Security-Policy img-src blocks, so they rendered broken.
    var startM = L.circleMarker(bounds[0], { radius: 8, color: "#fff", weight: 2, fillColor: "#2fb344", fillOpacity: 1 }).addTo(plannerMap);
    startM.bindPopup("Start: " + esc(r.origin));
    var endM = L.circleMarker(bounds[bounds.length - 1], { radius: 8, color: "#fff", weight: 2, fillColor: "#e5484d", fillOpacity: 1 }).addTo(plannerMap);
    endM.bindPopup("Destination: " + esc(r.destination));
    routeLayers.push(startM, endM);
  }

  routeLayers.forEach(function (l) { l.addTo(plannerMap); });
  plannerMap._rtsaRoutes = routeLayers;
  if (bounds.length > 1) plannerMap.fitBounds(L.latLngBounds(bounds).pad(0.1));
}

function clearRouteLayers() {
  if (!plannerMap) return;
  if (plannerMap._rtsaRoutes) {
    plannerMap._rtsaRoutes.forEach(function (l) { plannerMap.removeLayer(l); });
    plannerMap._rtsaRoutes = null;
  }
}

async function planRoute() {
  var out = $("#route-result");
  var fromIn = $("#from-select"), toIn = $("#to-select");
  if (!fromIn.value.trim() || !toIn.value.trim()) return;
  var from = knownJunction(fromIn.value), to = knownJunction(toIn.value);
  fromIn.classList.toggle("invalid", !from);
  toIn.classList.toggle("invalid", !to);
  if (!from || !to) {
    out.innerHTML = '<div class="error-box">Pick ' + (!from && !to ? "both places" : !from ? "the start" : "the destination") +
      " from the list of mapped junctions" + (!from ? ", or use your current location" : "") + ".</div>";
    clearRouteLayers();
    return;
  }
  fromIn.value = from; toIn.value = to;
  if (from === to) { out.innerHTML = '<div class="error-box">Choose two different junctions.</div>'; clearRouteLayers(); return; }
  var avoid = $("#avoid-incidents").checked;
  out.innerHTML = '<span class="muted">Planning…</span>';
  try {
    var r = await api("/api/routing/route?from_=" + encodeURIComponent(from) + "&to=" + encodeURIComponent(to) + "&avoid_incidents=" + avoid + "&include_alternatives=true&use_osrm=true");
    var blocks = [];
    if (PLANNER_ORIGIN_POS && PLANNER_ORIGIN_POS.junction === from) {
      blocks.push('<div class="small muted" style="margin-top:10px;">Starting from your location: ~' +
        (PLANNER_ORIGIN_POS.metres / 1000).toFixed(1) + " km to " + esc(from) + " (blue dashed line), then:</div>");
    }
    if (r.primary_route) {
      blocks.push('<div class="card" style="box-shadow:none;margin:10px 0 0;padding:10px;"><b>Primary route</b> · ' + r.primary_route.step_count + " steps · " +
        r.primary_route.total_distance_km + " km · ~" + r.primary_route.total_minutes + ' min' +
        (r.incidents_avoided ? ' <span class="pill amber">avoided ' + r.incidents_avoided + ' incident segment(s)</span>' : "") +
        '<div class="table-wrap" style="margin-top:8px;"><table><tr><th>Road</th><th>From → To</th><th>Dist</th><th>Min</th></tr>' +
        r.primary_route.steps.map(function (s) {
          return "<tr><td>" + esc(s.road_name) + "</td><td class='small'>" + esc(s.from_intersection) + " → " + esc(s.to_intersection) + "</td><td>" + s.distance_km + "</td><td>" + s.travel_minutes + "</td></tr>";
        }).join("") + "</table></div></div>");
    } else {
      blocks.push('<div class="error-box">' + (avoid && r.incidents_avoided
        ? "Every way to " + esc(to) + " is currently closed by an incident. See <a href=\"#/alerts\">road alerts</a>, or untick “Avoid incidents” to see the usual route."
        : "No route found between these junctions.") + "</div>");
    }
    (r.alternatives || []).forEach(function (alt, idx) {
      blocks.push('<div class="card" style="box-shadow:none;margin:8px 0 0;padding:10px;"><b>Alternative ' + (idx + 1) + "</b> · " + alt.step_count + " steps · " +
        alt.total_distance_km + " km · ~" + alt.total_minutes + " min<br><span class='muted small'>Grey dashed line on the map</span></div>");
    });
    out.innerHTML = blocks.join("");
    showRouteOnMap(r);
  } catch (e) {
    out.innerHTML = '<div class="error-box">' + esc(e.message) + "</div>";
  }
}

async function loadStatusBoard() {
  var board = $("#status-board");
  try {
    var rows = await api("/api/routing/status");
    board.innerHTML = rows.length
      ? '<div class="table-wrap"><table><tr><th>Road</th><th>Class</th><th>Status</th><th>Incidents</th></tr>' + rows.map(function (r) {
          var cls = r.status === "closed" ? "red" : r.status === "congested" ? "amber" : "green";
          var inc = (r.active_incidents || []).map(function (i) { return i; }).join(", ");
          return "<tr><td>" + esc(r.road) + '</td><td>' + esc(r["class"]) + "</td><td><span class='pill " + cls + "'>" + esc(r.status) + "</span></td><td class='small'>" + esc(inc || "—") + "</td></tr>";
        }).join("") + "</table></div>"
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
  $("#signup-form").addEventListener("submit", function (e) {
    e.preventDefault();
    doSignup();
  });
  $("#to-signup").addEventListener("click", function (e) { e.preventDefault(); showAuthView("signup"); });
  $$("#to-signin, .to-signin").forEach(function (a) {
    a.addEventListener("click", function (e) { e.preventDefault(); showAuthView("signin"); });
  });
  $("#to-forgot").addEventListener("click", function (e) {
    e.preventDefault();
    var typed = $("#email").value.trim();
    showAuthView("forgot");
    if (typed) $("#fg-email").value = typed;
  });
  $("#forgot-form").addEventListener("submit", function (e) { e.preventDefault(); doForgot(); });
  $("#reset-form").addEventListener("submit", function (e) { e.preventDefault(); doReset(); });
  $("#resend-from-login").addEventListener("click", async function (e) {
    e.preventDefault();
    try { authMessage($("#login-msg"), await resendConfirmation($("#email").value.trim()), true); }
    catch (err) { authMessage($("#login-msg"), err.message); }
  });
  $("#verify-resend").addEventListener("click", async function () {
    try { toast(await resendConfirmation(USER.email), "ok"); } catch (err) { toast(err.message, "err"); }
  });
  $$("#signup-form input").forEach(function (input) {
    input.addEventListener("input", function () { input.removeAttribute("aria-invalid"); });
  });
  wireNrcInput($("#su-nrc"));
  $("#logout-btn").addEventListener("click", logout);
  $("#mfa-gate-signout").addEventListener("click", function (e) { e.preventDefault(); logout(); });
  $("#bell").addEventListener("click", openNotifs);
  $("#notif-close").addEventListener("click", function () { $("#notif-drawer").classList.add("hidden"); });
  $("#notif-drawer").addEventListener("click", function (e) {
    if (e.target === this || e.target.id === "notif-drawer") this.classList.add("hidden");
  });
  $("#menu-btn").addEventListener("click", openMenu);
  $("#mnav-close").addEventListener("click", closeMenu);
  $("#overlay").addEventListener("click", closeMenu);
  $("#mnav-list").addEventListener("click", function (e) {
    if (e.target.tagName === "A") closeMenu();
  });
  window.addEventListener("hashchange", function () {
    closeMenu();
    if (USER && /^#\/verify\?token=/.test(location.hash)) confirmEmail(takeLinkToken().token);
    else if (USER) loadRouter();
    else if (!getToken()) routeAuthHash();
  });
}

window.addEventListener("DOMContentLoaded", function () {
  wire();
  if (getToken()) {
    var link = takeLinkToken();
    if (link && link.kind === "reset") {
      // They asked to reset the password, so drop this browser's session and show the form.
      logout();
      RESET_TOKEN = link.token;
      showAuthView("reset");
      return;
    }
    $("#login-screen").classList.add("hidden");
    bootstrapApp().then(function () {
      if (link) confirmEmail(link.token);
    }).catch(function () {
      logout();
    });
  } else {
    $("#login-screen").classList.remove("hidden");
    routeAuthHash(); // deep links: #/signup (from /about), #/forgot, emailed #/reset and #/verify
  }
});