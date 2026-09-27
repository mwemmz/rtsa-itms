# Platform guide (Developer 2)

Covers the citizen, revenue, notification, admin, security, reporting,
integration and non-functional modules. Developer 1's operational modules
consume most of this through a few stable interfaces, listed first.

## Interfaces for Developer 1

| Need | Use | Notes |
|---|---|---|
| Raise a notification | `from app.services.notifications import notify, broadcast` → `notify(db, user_id, "event_name", {...}, dedupe_key=None)` | Looks the event up in `notification_rules`; honours user channel preferences; SMS/email are queued for the worker. Add the event's rule via the admin UI or `scripts/seed.py`. |
| Take a payment / settle a fine or toll | `from app.services.payments import create_payment` → `create_payment(db, user, PaymentType.FINE, challan_id)` | The server decides the amount for fines and tolls, checks ownership, writes the ledger, issues a receipt, sets `Challan.status` / `TollTransaction.is_paid`. **Don't set `status = PAID` yourself** – it bypasses the ledger. |
| Require a permission | `Depends(require_permission("reports:view"))` (`app.core.permissions`) | Finer than `require_role`; admins can re-map permissions per role at runtime. `require_role` still works. |
| Write an audit entry | `log_action(db, action, entity_type, entity_id, details, actor_id)` | Unchanged. Every state-changing endpoint should call it. |
| Read a business threshold | `from app.services import settings; settings.get(db, "enforcement.challan_due_days")` | Admin-editable (Settings & access). `enforcement.*` and `toll.*` keys are declared but **not yet consumed by Developer 1's code** – wire them in when convenient. |
| Current user | `Depends(get_current_user)` | Now also validates the server-side session (revocation, idle timeout, blocked device). |

Events currently wired: `payment_receipt`, `payment_failed`, `refund_issued`,
`new_device_login`, `licence_expiring`, `insurance_expiring`, `fitness_expiring`,
`psv_permit_expiring`, plus Developer 1's `challan_created`, `toll_flagged`,
`road_alert`, `licence_renewed`.

## Security

* **Registration** only creates citizens. Staff are created by an admin (`POST /api/admin/users`).
  Citizens sign up from the web app ("Create a citizen account" on the sign-in card, or deep link `/#/signup`,
  linked from `/about`) and are signed straight in afterwards. `POST /api/auth/register` stores the email trimmed and
  lowercased, rejects case-insensitive duplicates, validates name and optional mobile number, and allows 10 sign-ups
  per IP per 5 minutes. Sign-in matches the email case-insensitively.
* **Passwords**: bcrypt; policy = min length (setting) + letters and numbers.
* **Lockout**: N failed logins (setting, default 5) lock the *account* for M minutes
  (default 15), on top of the per-IP throttle. Admins unlock. Every attempt is stored (`login_attempts`).
* **MFA (TOTP)**: `/api/auth/mfa/setup` → `/enable` (returns 8 one-time recovery codes) → login becomes two-step
  (`mfa_required` + `mfa_token`, then `/api/auth/mfa/verify`). Secrets are Fernet-encrypted at rest.
  An admin can clear a user's MFA if they lose their device.
  **Hard enforcement for staff**: turn on `security.require_mfa_staff` and every officer/toll-operator/admin
  account without MFA enabled is blocked from every endpoint except `GET /api/auth/me`, `/mfa/setup`, `/mfa/enable`,
  `/logout` and `/change-password` (enforced centrally in `get_current_user`, `app/core/security.py`) until they
  enrol. Citizens are unaffected. The web app shows a dedicated full-screen "Two-factor authentication required"
  gate for this instead of a bare 403 (`renderMfaGate()` in `app/static/app.js`). Since admin is itself a staff
  role, flipping the setting on is refused (`PATCH /api/admin/settings/security.require_mfa_staff`, 400) unless the
  *acting* admin already has MFA enabled on their own account - otherwise they'd lock themselves out of Settings
  the moment it takes effect.
* **CAPTCHA**: off by default (`security.captcha_enabled`). When on, `POST /api/auth/login` and `/register` need a
  solved challenge. `GET /api/auth/captcha` returns either a built-in sandbox challenge (a signed, stateless
  arithmetic question - no external service, but also not real bot resistance, same honest caveat as the National
  ID sandbox below) or, once `CAPTCHA_PROVIDER`/`CAPTCHA_SITE_KEY`/`CAPTCHA_SECRET_KEY` are set, the site key of a
  real provider (reCAPTCHA v2/v3, hCaptcha, Cloudflare Turnstile) for the frontend to render. The web login form
  only fetches a challenge after a first attempt is rejected, so there's no extra round trip while it's off; it
  only actually renders the sandbox widget - wiring a real provider's JS + CSP allowance into the page is a
  deployment step, not code (`app/services/captcha.py`).
* **Sessions**: every JWT carries a session id (`sid`) checked against `user_sessions` on each request –
  logout, idle timeout (default 30 min), password change, role change, deactivation and admin revoke all take effect immediately.
  Only requests the user makes count as activity: the web app sends `X-Background-Refresh: 1` on requests it makes by
  itself (live-update reloads, the notification bell, stream reconnects), and those don't push back the idle timeout -
  otherwise a screen left open on a busy page would never sign out. They're still refused once the session has idled out.
  The page and its scripts/styles are served with `Cache-Control: no-cache` (revalidated via ETag), so browsers pick up
  a deploy straight away instead of running a cached `app.js` against a newer API.
* **Live-updates stream**: access tokens never go in a URL (URLs end up in uvicorn/Render/proxy access logs).
  The browser's `EventSource` can't send an `Authorization` header, so the web app calls `POST /api/events/ticket`
  first and opens `/api/events/stream?ticket=...` with a ticket that only opens the stream, expires after 30 s and
  works once (`app/api/events.py`). After a drop it reconnects itself with a fresh ticket, backing off 1 s → 30 s.
  Scripts can still use `Authorization: Bearer <access token>`. The stream is excluded from gzip
  (`StreamSafeGZipMiddleware`, `app/core/middleware.py`) - older Starlette versions otherwise buffer every frame.
  **Each viewer only receives what they may see** (`app/services/event_visibility.py`): road incidents and broadcasts
  go to everyone; changes about a user's own account, sessions, devices, vehicles, fines, payments or licence
  applications go to that user; operational records go to staff; accounts, sessions, devices, settings, roles, agencies
  and notification rules only to holders of the matching permission; anything unclassified only to `audit:read`
  holders. Who made the change is only included for `audit:read` holders. The owners of a record are looked up once
  when the change is published. An open stream re-reads its session and permissions every 30 s and closes if the user
  was signed out, deactivated, blocked or went idle; a demotion or permission change applies without reconnecting.
  When adding a new entity type to `log_action`, classify it in `event_visibility.py` (until then only admins see it).
* **Devices**: fingerprint = hash(user-agent, language, `X-Device-Id`). New devices raise a `new_device_login` notification;
  users can trust/block devices (a blocked device can't sign in).
* **Transport/headers**: `FORCE_HTTPS` redirects http→https (behind a proxy, via `X-Forwarded-Proto`); HSTS in production;
  nosniff, frame-deny, referrer policy, CSP on the SPA. `Cache-Control: no-store` on `/api`.
* **RBAC**: 13 permissions × 4 roles, editable in *Settings & access*; `admin` always holds all. The last active
  admin account can't be demoted or deactivated by anyone else, even with `roles:manage`/`users:manage` remapped
  onto another role (`_is_last_admin`, `app/api/admin.py`) - it can only happen by promoting a replacement first.
* **Not done**: per-field DB encryption beyond MFA secrets and gateway payloads (database-level encryption at rest
  is the hosting provider's – Neon encrypts storage).

## Payments & revenue

Gateways: `sandbox` (instant), `sandbox_decline`, `mobile_money` (async – pending until a signed webhook arrives).
Real gateway integration = implement the charge call in `create_payment` and point the provider's callback at
`POST /api/payments/webhook/{gateway}` (HMAC-SHA256 over the raw body in `X-Signature`, secret derived from `SECRET_KEY`).

* Idempotency: `Idempotency-Key` header – a retry returns the original payment (HTTP 200) instead of charging again.
* Ledger: `payment_events` is append-only (initiated → completed / failed → refunded → reconciled).
* Refunds: full or partial; a fully refunded fine reopens the challan.
* Reconciliation: `POST /api/payments/reconciliation/run` with the gateway's settlement rows; reports matched, amount
  mismatches, rows missing from our ledger and payments missing from the statement.
* Receipts: JSON and PDF (`/api/payments/{id}/receipt[.pdf]`).

## Reporting

`/api/reports/{registrations|licensing|violations|accidents|psv|revenue|toll}?format=json|csv|xlsx|pdf&date_from&date_to`,
plus `/api/reports/dashboard` (KPIs, monthly trends, breakdowns; cached `REPORT_CACHE_SECONDS`).
Exports need `reports:export`, are audit-logged, capped at 50 000 rows, and CSV/Excel cells that look like formulas are neutralised.

## Inter-agency integration

Agencies authenticate with `X-API-Key` (`rtsa_<prefix>_<secret>`; only a SHA-256 hash is stored; shown once at creation/rotation).
Each agency has a *data-sharing contract* = a set of scopes (a subset of what its type may hold), optional expiry and a
per-minute rate limit. Every call is logged to `integration_logs` (status, latency) and summarised in *Agency integrations → Monitoring*.

| Agency | Endpoints |
|---|---|
| Police | `GET /api/integration/police/vehicles/{plate}`, `/police/drivers/{licence}`, `POST /police/accidents` |
| Insurance | `GET /insurance/verify/{plate}`, `PUT /insurance/policies` (push new/renewed/cancelled policies) |
| Hospital | `POST /hospital/accidents` |
| Toll authority | `GET /toll-authority/compliance/{plate}` (runs Developer 1's compliance engine) |
| National ID | outbound `GET /national-id/verify?nrc=` (staff); inbound `GET /national-id/lookup?nrc=` |

The national-ID adapter is a **sandbox** (format check only, and it says so in the response) until
`NATIONAL_ID_API_URL` points at a real registry. That's the only thing missing - the code side is ready to go:

* **Contract**: `GET {NATIONAL_ID_API_URL}?nrc=<nrc>` with `Authorization: Bearer {NATIONAL_ID_API_TOKEN}`, expecting
  a JSON body `{"verified": bool, "full_name"?: str}`.
* **Resilience**: an 8 s timeout; a transient failure (connection error, timeout, 5xx) is retried up to twice with a
  short backoff before giving up, since this is usually on a staff member's critical path (issuing a licence,
  registering a vehicle); anything else (bad JSON, wrong shape, 4xx) fails clean with a 502 rather than a 500.
* **Guardrails**: refuses to even attempt a call if the token is empty while a URL is set (a common misconfiguration
  - fails with a clear 500 instead of an opaque 401 from the registry), and if `NATIONAL_ID_API_URL` isn't `https://`
  once `ENVIRONMENT=production` (NRC numbers are PII).
* **To go live**: get a URL + bearer token from the registry that match the contract above, set them via env vars,
  redeploy. No code changes needed (`app/services/integration.py::verify_national_id`).

## Performance

* `X-Process-Time` / `Server-Timing` on every response; requests slower than `SLOW_REQUEST_MS` are logged.
* `/api/system/metrics`: per-route p50/p95/p99 and the toll-decision p95 against the `toll.compliance_target_ms` setting.
* Connection pooling with `pool_pre_ping` + recycle; GZip; permission and setting lookups are cached for a few seconds.
* Measured on SQLite with 600 000 toll rows: dashboard 2.2 s cold / 9 ms cached; JSON report ~0.9 s;
  CSV export of 50 000 rows ~2.6 s; Excel ~8–10 s; PDF (2 000 rows) ~4 s. Postgres with the new indexes should do better but has not been measured.

## Availability & disaster recovery

See [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md).

## Scalability

* Migration `a7c3d91e5b20` adds indexes for the hot paths (payments by status/date and payer, challans by status/vehicle,
  toll/violation/accident timestamps, audit log by time/entity/actor, expiry dates used by reminders, notifications by status).
* All list endpoints paginate (`skip`/`limit`, capped); reports aggregate in SQL, not Python.
* **Running more than one instance:** set `REDIS_URL` (on Render: add a *Key Value* instance and use its internal URL).
  Sessions, lockouts, settings and the ledger are already in the database; these five things would otherwise be
  per-process and go wrong behind a load balancer, so with `REDIS_URL` they're shared through Redis
  (`app/core/shared.py`):

  | What | Without Redis (one instance) | With `REDIS_URL` |
  |---|---|---|
  | Login and sign-up throttles | per-process counts | one sliding window per IP, all instances |
  | Agency API rate limits | per-process counts | one window per agency, all instances |
  | Single-use stream tickets | used-once per process | used-once across instances |
  | Live updates | only this instance's connections | published to a Redis channel; every instance relays to its own viewers, with the same per-viewer filtering |
  | `/api/system/metrics` | this instance | every live instance merged (each publishes its window every 10 s; `instances` shows how many) |

  Nothing changes when `REDIS_URL` is unset. If Redis becomes unreachable, each of these falls back to its
  single-instance behaviour and logs it (at most once a minute) rather than failing requests. Short read caches stay per
  instance: permissions and settings (5 s) and the analytics dashboard (`REPORT_CACHE_SECONDS`) - a change can take
  that long to show on another instance. These paths are tested against an in-memory stand-in for Redis
  (`tests/test_shared_state.py`); run one real two-instance check before relying on it.
* Route heavy work (large exports, notification delivery) to the worker as volume grows; the service functions are already queue-friendly.
