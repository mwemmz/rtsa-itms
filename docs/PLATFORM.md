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
* **Passwords**: bcrypt; policy = min length (setting) + letters and numbers.
* **Lockout**: N failed logins (setting, default 5) lock the *account* for M minutes
  (default 15), on top of the per-IP throttle. Admins unlock. Every attempt is stored (`login_attempts`).
* **MFA (TOTP)**: `/api/auth/mfa/setup` → `/enable` (returns 8 one-time recovery codes) → login becomes two-step
  (`mfa_required` + `mfa_token`, then `/api/auth/mfa/verify`). Secrets are Fernet-encrypted at rest.
  An admin can clear a user's MFA if they lose their device.
* **Sessions**: every JWT carries a session id (`sid`) checked against `user_sessions` on each request –
  logout, idle timeout (default 30 min), password change, role change, deactivation and admin revoke all take effect immediately.
* **Devices**: fingerprint = hash(user-agent, language, `X-Device-Id`). New devices raise a `new_device_login` notification;
  users can trust/block devices (a blocked device can't sign in).
* **Transport/headers**: `FORCE_HTTPS` redirects http→https (behind a proxy, via `X-Forwarded-Proto`); HSTS in production;
  nosniff, frame-deny, referrer policy, CSP on the SPA. `Cache-Control: no-store` on `/api`.
* **RBAC**: 13 permissions × 4 roles, editable in *Settings & access*; `admin` always holds all.
* **Not done**: hard enforcement of MFA for staff (`security.require_mfa_staff` only flags `mfa_setup_required`);
  CAPTCHA; per-field DB encryption beyond MFA secrets and gateway payloads (database-level encryption at rest is the
  hosting provider's – Neon encrypts storage).

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
`NATIONAL_ID_API_URL` points at a real registry.

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
* The app is stateless apart from three in-process caches/limiters (login IP throttle, agency rate limit, metrics). With more
  than one instance, move those to Redis; everything else (sessions, lockout, settings, ledger) is in the database.
* Route heavy work (large exports, notification delivery) to the worker as volume grows; the service functions are already queue-friendly.
