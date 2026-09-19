# RTSA ITMS — API Contract
## My Domain (Developer 2): Citizen, Revenue, Integration, Security & Platform

**Covers requirement sections:** 11 (Citizen Portal), 12 (Revenue & Payment), 13 (Notifications), 14 (Administration), 15 (Security), 16 (Reporting & Analytics), 17 (Inter-Agency Integration), 18 (Performance), 19 (Availability/DR), 20 (Scalability).

**Who this is for:** Dev 1 (core transport/enforcement domain), frontend/citizen clients, external agency systems.

This is my binding contract for every endpoint, event, and shared convention I own. Dev 1 builds against this without needing to read my implementation, and I build against their companion contract the same way.

---

## 0. Global Conventions

These apply to *every* endpoint in this document, and they're also the standard Dev 1 needs to follow when calling into my services (auth, audit, notifications).

### 0.1 Base URL & Versioning
```
https://api.itms.rtsa.gov.zm/v1/...
```
- All routes are versioned under `/v1`. Breaking changes bump to `/v2`; both are served in parallel for a deprecation window (minimum 90 days), announced via the `Deprecation` and `Sunset` response headers.
- Non-breaking additions (new optional fields, new endpoints) do not bump the version.

### 0.2 Authentication
- All authenticated requests carry `Authorization: Bearer <JWT>`.
- JWT is issued by my Auth service (§1) and is the **single source of truth** for identity across both our services — Dev 1's services validate the same token, they never mint their own.
- Service-to-service calls (Dev 1 → me, or either of us → external agencies) use a separate `X-Service-Key` header (mutual API keys, rotated quarterly) in addition to the JWT where a human identity is also relevant (e.g. an officer issuing a citation).
- Inter-agency calls (§7) additionally require mTLS.

### 0.3 Standard Headers
| Header | Direction | Notes |
|---|---|---|
| `Authorization` | Request | `Bearer <JWT>` |
| `X-Service-Key` | Request | Service-to-service only |
| `X-Request-Id` | Request/Response | UUID v4. Caller may supply one; if absent, generated and echoed back. Used for tracing across both our logs. |
| `X-Correlation-Id` | Request/Response | Groups a multi-step business transaction (e.g. citation → notification → payment). Propagated end-to-end. |
| `Idempotency-Key` | Request | Required on all `POST` calls that create money-movement or irreversible records (payments, e-Challan settlement, licence issuance triggers). Server stores result for 24h and replays it on retry instead of re-executing. |
| `Content-Type` | Request/Response | `application/json` unless stated (report exports use specific mime types, §6). |
| `ETag` / `If-Match` | Response/Request | Required on updates to Administration records (roles, thresholds) to prevent lost updates. |

### 0.4 Standard Error Envelope
Every non-2xx response uses this exact shape:
```json
{
  "error": {
    "code": "PAYMENT_DECLINED",
    "message": "Card was declined by the payment gateway.",
    "details": [
      { "field": "cardNumber", "issue": "invalid_luhn" }
    ],
    "requestId": "b3f1c2e0-...",
    "timestamp": "2026-09-07T10:15:00Z"
  }
}
```
- `code` is a stable, machine-readable string from the catalog in §9 — never change its meaning once shipped.
- HTTP status is still the primary signal (400/401/403/404/409/422/429/500/503).
- Validation errors (422) always populate `details[]`.

### 0.5 Pagination
Cursor-based on all list endpoints:
```
GET /v1/resource?limit=50&cursor=eyJpZCI6MTIzfQ
```
Response:
```json
{
  "data": [ ... ],
  "pagination": { "nextCursor": "eyJpZCI6MTQwfQ", "hasMore": true, "limit": 50 }
}
```
`limit` max 200, default 50. Offset pagination is not supported (dataset scale, see §20).

### 0.6 Filtering & Sorting
- Filters: `?status=OVERDUE&dateFrom=2026-01-01&dateTo=2026-06-30`
- Sorting: `?sort=-createdAt,amount` (`-` prefix = descending)
- Field selection (reduces payload for reporting): `?fields=id,amount,status`

### 0.7 Rate Limiting
- Per-token limit returned via `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.
- Citizen portal: 60 req/min per account. Service-to-service: 600 req/min per key, negotiated up for high-volume integrations (e.g. Dev 1's toll-plaza sync, §20).
- Exceeding limit returns `429` with `code: RATE_LIMITED` and a `Retry-After` header.

### 0.8 Audit Logging
- Every mutating call (`POST/PUT/PATCH/DELETE`) across **both** our services is logged by calling my internal audit sink:
```
POST /internal/v1/audit-events
{
  "actorId": "user-123", "actorType": "CITIZEN|OFFICER|ADMIN|SYSTEM",
  "action": "PAYMENT.SETTLED", "resourceType": "PAYMENT", "resourceId": "pay-789",
  "before": {...}, "after": {...}, "correlationId": "...", "occurredAt": "..."
}
```
- Dev 1 is expected to call this endpoint (or a thin SDK wrapper I provide) for every operational mutation, not just read it.
- Audit records are immutable and queryable via §5.4.

### 0.9 Event / Webhook Naming
Internal domain events (published to the shared event bus/queue) follow `DOMAIN.ENTITY.ACTION`, past tense:
```
CITIZEN.APPLICATION.SUBMITTED
PAYMENT.TRANSACTION.SETTLED
PAYMENT.TRANSACTION.FAILED
NOTIFICATION.MESSAGE.SENT
VIOLATION.CITATION.ISSUED        (published by Dev 1, consumed by me)
INSPECTION.CERTIFICATE.EXPIRING  (published by Dev 1, consumed by me)
ACCIDENT.REPORT.FILED            (published by Dev 1, consumed by me)
```
Envelope for every event:
```json
{
  "eventId": "uuid",
  "eventType": "PAYMENT.TRANSACTION.SETTLED",
  "occurredAt": "2026-09-07T10:15:00Z",
  "correlationId": "uuid",
  "version": 1,
  "payload": { ... }
}
```

---

## 1. Authentication, RBAC & Security (Section 15)

Owned entirely by me; consumed by Dev 1 and every client.

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/auth/login` | Email/NRC + password login. Returns JWT + refresh token. |
| POST | `/v1/auth/mfa/challenge` | Triggers OTP (SMS/email/authenticator) after primary credential check. |
| POST | `/v1/auth/mfa/verify` | Verifies OTP, returns final JWT. |
| POST | `/v1/auth/refresh` | Exchanges refresh token for new JWT. |
| POST | `/v1/auth/logout` | Revokes refresh token + current session. |
| GET | `/v1/auth/sessions` | Lists active sessions/devices for current user. |
| DELETE | `/v1/auth/sessions/{sessionId}` | Remote sign-out of a specific device. |
| POST | `/v1/auth/password/reset-request` | Sends reset link/OTP. |
| POST | `/v1/auth/password/reset-confirm` | Completes reset. |
| GET | `/v1/rbac/roles` | List roles (Admin, Officer, Inspector, Citizen, Auditor, ...). |
| POST | `/v1/rbac/roles` | Create a role with a permission set. |
| PUT | `/v1/rbac/roles/{roleId}` | Update permissions (requires `If-Match` ETag). |
| POST | `/v1/rbac/users/{userId}/roles` | Assign role(s) to a user. |
| GET | `/v1/security/devices` | Device-fingerprint history for a user (fraud/brute-force review). |
| POST | `/v1/security/lockouts/{userId}/release` | Admin releases an account locked by brute-force protection. |

**Contract details:**
- JWT claims: `sub`, `roles[]`, `permissions[]`, `agencyContext` (for inter-agency users), `exp` (15 min access token), `sid` (session id, for remote revocation).
- Brute-force protection: 5 failed attempts locks the account for 15 minutes and fires `SECURITY.ACCOUNT.LOCKED`; Dev 1's officer-facing apps must handle `401` + `code: ACCOUNT_LOCKED` distinctly from bad-credential `401`.
- All traffic is HTTPS/TLS 1.2+ only; HTTP requests are rejected at the gateway, not the application — nothing either of us has to implement per-endpoint.
- Field-level encryption at rest for NRC numbers, payment instrument tokens, and driver biometric refs (Dev 1's data, encrypted via a shared KMS-backed library I provide).

---

## 2. Citizen Self-Service Portal (Section 11)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/citizens/me` | Current citizen's profile. |
| PUT | `/v1/citizens/me` | Update contact details. |
| GET | `/v1/citizens/me/vehicles` | Vehicles linked to citizen (proxies Dev 1's vehicle service, cached read model). |
| GET | `/v1/citizens/me/licences` | Driving licence status/expiry (proxies Dev 1). |
| GET | `/v1/citizens/me/fines` | Outstanding + historic fines/e-Challans (proxies Dev 1, joined with payment status I own). |
| GET | `/v1/citizens/me/accidents` | Accident records linked to citizen (proxy). |
| POST | `/v1/applications` | Submit a new application (licence renewal, permit, ownership transfer request). Generic envelope with `applicationType` discriminator. |
| GET | `/v1/applications` | List own applications with status. |
| GET | `/v1/applications/{id}` | Application detail + status timeline. |
| GET | `/v1/applications/{id}/status` | Lightweight polling endpoint (for status-tracking UI). |
| POST | `/v1/applications/{id}/documents` | Upload supporting document (multipart). |
| POST | `/v1/applications/{id}/cancel` | Citizen-initiated withdrawal. |

**Contract details:**
- `applicationType` enum: `LICENCE_RENEWAL | VEHICLE_TRANSFER | PSV_PERMIT | INSPECTION_BOOKING | ...` — each type has a JSON-schema-validated `details` object documented in the Appendix (§10).
- Applications that require operational action (e.g. inspection booking) publish `CITIZEN.APPLICATION.SUBMITTED`; Dev 1's relevant service subscribes and owns the operational fulfillment, then calls back via `PATCH /v1/applications/{id}/fulfillment` (service-to-service only) to update status.
- Status enum is shared and fixed: `SUBMITTED → UNDER_REVIEW → ACTION_REQUIRED → APPROVED → REJECTED → COMPLETED → CANCELLED`.

---

## 3. Revenue & Payment Management (Section 12)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/payments/intents` | Create a payment intent for a fee/fine/permit/toll. Returns gateway redirect/token. Requires `Idempotency-Key`. |
| GET | `/v1/payments/intents/{id}` | Check intent status. |
| POST | `/v1/payments/webhooks/gateway` | Inbound webhook from the payment gateway (signature-verified). Not called by Dev 1. |
| GET | `/v1/payments/transactions` | List transactions (filterable by citizen, payer, type, date, status). |
| GET | `/v1/payments/transactions/{id}` | Transaction detail incl. reconciliation status. |
| GET | `/v1/payments/transactions/{id}/receipt` | Returns receipt (PDF, `Accept: application/pdf`). |
| POST | `/v1/payments/reconciliation/runs` | Admin-triggered reconciliation batch against gateway settlement file. |
| GET | `/v1/payments/reconciliation/runs/{id}` | Reconciliation run result, incl. mismatches. |
| POST | `/v1/payments/refunds` | Initiate a refund (admin only, requires justification + `Idempotency-Key`). |
| GET | `/v1/revenue/summary` | Aggregated revenue (used by Reporting, §6). |

**Contract details:**
- `paymentType` enum: `FINE | TOLL | LICENCE_FEE | PERMIT_FEE | INSPECTION_FEE | OTHER`. `referenceId` links back to the source record in Dev 1's domain (e.g. e-Challan id) — I never validate that reference's business rules, only that it's well-formed; Dev 1 owns truth of "is this fine still valid to pay."
- **This is the critical handoff:** Dev 1's e-Challan/toll modules call `POST /v1/payments/intents` to hand off settlement; on success/failure I emit `PAYMENT.TRANSACTION.SETTLED` / `PAYMENT.TRANSACTION.FAILED` with `referenceId` in the payload, and Dev 1 subscribes to flip the fine/toll record to `PAID`.
- All money fields are integer minor units (ngwee) with an explicit `currency: "ZMW"` field — never floats.
- Every transaction is immutable once `SETTLED`; corrections happen only via `/v1/payments/refunds`, itself audit-logged.

---

## 4. Notification Management (Section 13)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/notifications/send` | Send a notification now (service-to-service, used by Dev 1 for violations/expiries/accidents). |
| POST | `/v1/notifications/templates` | Create/update a message template (admin). |
| GET | `/v1/notifications/templates` | List templates. |
| GET | `/v1/notifications` | List notifications sent to/for a citizen (citizen or admin view). |
| GET | `/v1/notifications/{id}` | Delivery status detail (queued/sent/delivered/failed). |
| PUT | `/v1/citizens/me/notification-preferences` | Citizen opts in/out of SMS/email/in-app per category. |

**Contract details — this is the shared event interface Section 5 requires:**
```json
POST /v1/notifications/send
{
  "channelPreference": "AUTO",          // AUTO respects citizen prefs; or force SMS|EMAIL|IN_APP
  "templateKey": "LICENCE_EXPIRY_30D",
  "recipientCitizenId": "cit-123",
  "correlationId": "uuid",
  "variables": { "expiryDate": "2026-10-01", "licenceNumber": "DL-...:" }
}
```
- Returns `202 Accepted` with a `notificationId`; delivery is async — Dev 1 does not block on SMS gateway latency.
- Required `templateKey`s Dev 1 will trigger against (pre-agreed, extendable): `LICENCE_EXPIRY_30D/7D`, `INSURANCE_EXPIRY_30D/7D`, `INSPECTION_DUE`, `CITATION_ISSUED`, `FINE_OVERDUE`, `ACCIDENT_LOGGED`, `TOLL_BLACKLIST_WARNING`.
- Failure to deliver after retries publishes `NOTIFICATION.MESSAGE.FAILED` so Dev 1 can surface it operationally if needed (e.g. re-flagging a citation for manual follow-up).

---

## 5. Administration (Section 14)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/admin/users` | List system users (staff, officers, admins — not citizens). |
| POST | `/v1/admin/users` | Provision a new staff user. |
| PUT | `/v1/admin/users/{id}` | Update/deactivate a user. |
| GET | `/v1/admin/settings` | System-wide settings (fee schedules, grace periods, toll rates metadata). |
| PUT | `/v1/admin/settings/{key}` | Update a setting (requires `If-Match`, audit-logged, may require dual approval — see `requiresApproval` flag in response). |
| GET | `/v1/admin/thresholds` | Business rule thresholds (e.g. brute-force limit, fine escalation days, toll-check SLA of 500ms from §22 surfaced here for ops visibility). |
| PUT | `/v1/admin/thresholds/{key}` | Update a threshold. |
| GET | `/v1/admin/audit-logs` | Query audit trail (see §0.8), filterable by actor/resource/date. |

**Contract details:**
- Settings/thresholds that Dev 1's engine reads at runtime (e.g. toll compliance thresholds, fine escalation days) are exposed read-only to Dev 1 via `GET /v1/admin/thresholds` and cached with a short TTL + `SETTINGS.THRESHOLD.UPDATED` event for cache-busting — Dev 1 never writes to this table directly.

---

## 6. Reporting & Analytics (Section 16)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/reports/dashboard` | Aggregated KPIs across registration, licensing, violations, accidents, PSV, revenue, toll enforcement. |
| GET | `/v1/reports/{reportType}` | Parameterized report data (JSON), e.g. `reportType=revenue-by-region`. |
| POST | `/v1/reports/{reportType}/exports` | Kick off an export job. Body: `{ "format": "PDF"|"XLSX"|"CSV", "filters": {...} }`. Returns `202` + job id (large reports are async). |
| GET | `/v1/reports/exports/{jobId}` | Export job status; when `COMPLETED`, includes a signed download URL (expires in 1h). |
| GET | `/v1/reports/definitions` | Lists available `reportType`s, their filters and required permissions. |

**Contract details:**
- Report data is built from a read-optimized aggregation store, refreshed from Dev 1's operational events (violations, accidents, PSV, inspections) — Dev 1 doesn't need to build any reporting endpoints; they just need to make sure their domain events (§0.9) carry the fields reporting needs (documented per-event in the Appendix).
- Exports over a row-count threshold (configurable, default 10,000 rows) are forced async regardless of format.

---

## 7. Inter-Agency Integration (Section 17)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/integrations/police/incidents` | Receive incident data from Police systems (mTLS + API key). |
| GET | `/v1/integrations/police/vehicle-lookup` | Outbound: agencies query vehicle/owner status. |
| POST | `/v1/integrations/insurance/verify` | Outbound to insurers for real-time policy verification (used by Dev 1's insurance module via this gateway, never calling insurers directly). |
| POST | `/v1/integrations/hospitals/accident-notify` | Inbound accident/casualty data from hospital systems. |
| POST | `/v1/integrations/national-id/verify` | NRC verification against the national ID registry. |
| GET | `/v1/integrations/monitoring/status` | Health/status of each external integration (last successful call, error rate) for ops dashboards. |
| GET | `/v1/integrations/contracts` | Machine-readable registry of active data-sharing contracts (which agency, which fields, retention terms). |

**Contract details:**
- Every inbound/outbound integration call is logged to the audit sink (§0.8) with `actorType: SYSTEM` and the external agency id.
- Dev 1's insurance-verification and accident-management modules call **through** these endpoints of mine rather than integrating with external agencies directly — I own retries, circuit-breaking, and contract versioning per external partner.
- All external payloads are validated against a per-agency JSON schema before being accepted; malformed payloads return `422` and are never silently dropped.

---

## 8. Non-Functional Contracts (Sections 18, 19, 20)

These aren't citizen-facing endpoints, but they're still *contractual* — Dev 1 needs to know what to expect from me and what to instrument for.

### 8.1 Performance (18)
- All synchronous read endpoints in this document: p95 < 300ms, p99 < 800ms under nominal load.
- Payment intent creation: p95 < 1.5s (bounded by external gateway).
- We both expose `X-Response-Time-Ms` in responses for monitoring; slow endpoints get flagged in `/v1/integrations/monitoring/status`-style dashboards that I maintain.

### 8.2 Availability / Disaster Recovery (19)
- `GET /v1/health` — liveness (no auth). `GET /v1/health/ready` — readiness, checks DB + downstream deps.
- Target uptime: 99.5% for citizen-facing endpoints, 99.9% for the auth/RBAC service Dev 1 depends on.
- Automated Neon backups: point-in-time recovery retained 7 days minimum. RTO target 4h, RPO target 15min — Dev 1's services need to be stateless enough to redeploy against a restored DB without code changes.

### 8.3 Scalability (20)
- All list endpoints are cursor-paginated (§0.5) specifically so they scale to millions of rows without offset-scan cost.
- High-volume ingestion paths (ANPR events, toll-plaza offline sync batches from Dev 1) use a batched endpoint pattern, not one-row-per-call:
```
POST /v1/integrations/batch-ingest
{ "sourceSystem": "TOLL_PLAZA_07", "events": [ {...}, {...}, ... up to 500 ] }
```
  Returns per-item success/failure so partial batch failures don't block the whole sync.

---

## 9. Error Code Catalog (shared, extend by PR only)

| Code | HTTP | Meaning |
|---|---|---|
| `UNAUTHENTICATED` | 401 | Missing/invalid/expired token |
| `ACCOUNT_LOCKED` | 401 | Brute-force lockout in effect |
| `FORBIDDEN` | 403 | Valid token, insufficient permission |
| `NOT_FOUND` | 404 | Resource doesn't exist or not visible to caller |
| `VALIDATION_ERROR` | 422 | See `details[]` |
| `IDEMPOTENCY_CONFLICT` | 409 | Same key, different payload |
| `PAYMENT_DECLINED` | 402 | Gateway declined |
| `RATE_LIMITED` | 429 | See `Retry-After` |
| `DOWNSTREAM_UNAVAILABLE` | 503 | An upstream dependency (incl. Dev 1's services) is down |
| `CONTRACT_VERSION_MISMATCH` | 400 | External agency payload doesn't match registered schema version |
| `INTERNAL_ERROR` | 500 | Unhandled — always logged with `requestId` for triage |

---

## 10. Appendix — Extension Points

- **Application `details` schemas** (per `applicationType`), **report field dictionaries** (per `reportType`), and **inter-agency payload schemas** (per agency) are maintained as versioned JSON Schema files in a shared `/contracts/schemas` repo folder rather than inline here, so they can evolve without renegotiating this whole document.
- Any new event type, error code, or endpoint either of us adds must go into this document (or its schema appendix) in the same PR that ships it — this file is the source of truth referenced in Section 5's "agree on API contracts... before integration begins."
