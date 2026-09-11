# RTSA Integrated Transport Management System (ITMS) — Build Spec

## 0. How to use this file

This is a build spec for an AI coding agent. It is a **solo side project** —
build everything yourself, phase by phase, in the order given. Each phase
should end with something runnable and demoable before moving to the next.
Don't skip ahead to later-phase features even if they seem quick; finish a
phase's checklist first.

- **Language/runtime:** Python (FastAPI recommended for async + auto docs)
- **Database:** Neon (serverless Postgres) — use SQLAlchemy + Alembic for
  models/migrations
- **Optional edge/cache layer:** Turso (libSQL) — only introduce this in
  Phase 8 if there's a real read-heavy/offline need (e.g. toll-plaza offline
  cache); don't add it earlier just because it's available
- **Hosting:** Render (web service for the API, background worker if needed
  for notifications/toll sync)
- **Repo:** single repo, trunk-based — `main` branch only is fine for a solo
  project; commit often with clear messages

## 1. Project summary

Build the backend (and a minimal admin/citizen frontend if time allows) for
RTSA's Integrated Transport Management System: vehicle registration, driver
licensing, traffic enforcement, PSV management, vehicle inspection,
insurance verification, accident reporting, toll-gate compliance
enforcement (ANPR-based), e-Challan/penalty management, a citizen portal,
payments, notifications, admin tooling, security, and reporting.

## 2. Suggested repo structure

```
rtsa-itms/
  app/
    core/           # config, db session, security/auth, logging
    models/         # SQLAlchemy models, one file per domain
    schemas/        # Pydantic request/response schemas
    api/            # routers, one file per module (vehicles.py, drivers.py, ...)
    services/       # business logic (compliance engine, notifications, payments)
    workers/        # background jobs (toll sync, notification dispatch)
  alembic/          # migrations
  tests/
  scripts/          # seed data, one-off utilities
  .env.example
  requirements.txt
  README.md
```

## 3. Core data model (build in this order — later modules depend on earlier ones)

1. **Vehicles** — registration number, owner, make/model, status, blacklist flag
2. **Drivers** — personal info, licence number, licence class, status/restrictions
3. **Licence applications & tests** — theory/practical results, issuance, renewal
4. **Inspections** — inspection centre, results, fitness certificate + expiry
5. **Insurance** — provider, policy number, validity dates
6. **PSV** — operator, permit, route, compliance status
7. **Accidents** — location, date/time, vehicles/drivers involved, severity
8. **Violations / e-Challans** — type, location, timestamp, vehicle/driver, fine amount, status (unpaid/paid/overdue)
9. **ANPR events** — plate read, timestamp, location, linked vehicle
10. **Toll transactions** — gate id, vehicle, compliance result, flagged issues
11. **Users/roles** — citizens, officers, toll operators, admins (RBAC)
12. **Payments** — linked to fines/fees/permits/toll, gateway reference, receipt
13. **Notifications** — channel (SMS/email/in-app), trigger event, status
14. **Audit log** — actor, action, entity, timestamp (every module writes to this)

## 4. Toll-gate compliance decision logic (core business rule — build carefully)

When a vehicle approaches a toll gate:
1. Capture the vehicle registration number (ANPR/FASTag/manual).
2. Look up the vehicle in the central database.
3. Check: registration status, insurance validity, fitness certificate,
   PSV permit (if applicable), outstanding fines/toll dues, blacklist status.
4. If compliant → transaction proceeds normally.
5. If not compliant → flag vehicle, alert toll operator, alert enforcement
   if needed, auto-generate e-Challan where applicable, notify owner,
   record the event for audit/reporting.
Target: this check should resolve in well under a second — keep it a single
indexed query path, not multiple round trips.

## 5. Build phases (8 phases, one after another — solo pace, adjust timeline freely)

### Phase 1 — Foundations
- Scaffold FastAPI app, config, DB connection to Neon, base folder structure
- Set up Alembic; create and migrate: Vehicles, Drivers tables
- Basic health-check endpoint
- **Done when:** app boots, connects to Neon, `alembic upgrade head` runs clean

### Phase 2 — Auth & Core CRUD
- Implement JWT auth + RBAC (roles: citizen, officer, toll_operator, admin)
- CRUD endpoints: vehicle registration/renewal/search, driver records/licensing
- Shared audit-log utility, call it from every write endpoint going forward
- **Done when:** you can register a user, log in, and CRUD a vehicle + driver
  through authenticated endpoints

### Phase 3 — Inspection, Insurance & Licensing workflows
- Inspection scheduling + results + fitness certificate + expiry tracking
- Insurance record storage + validity/expiry checks
- Licence application → test result → issuance/renewal workflow
- **Done when:** a vehicle can have a full inspection + insurance + fitness
  status, and a driver can go from application to issued licence

### Phase 4 — Enforcement core (violations, ANPR, e-Challan)
- Violation entry endpoint (officer records a violation)
- ANPR event capture (mock feed is fine — a POST endpoint simulating a camera)
- e-Challan generation with unique reference, penalty amount, due date, status
- **Done when:** an officer (or mock ANPR event) can generate an e-Challan
  tied to a real vehicle/driver

### Phase 5 — Toll compliance engine
- Implement the decision logic from Section 4 above
- Wire in vehicle status, insurance, fitness, PSV permit, fines, blacklist checks
- Log every toll event (compliant or flagged) to its own table + audit log
- **Done when:** posting a mock toll-gate event returns a compliant/flagged
  result and, if flagged, auto-creates an e-Challan

### Phase 6 — PSV, Accidents & Payments
- PSV operator/vehicle/permit registration + route compliance
- Accident reporting, linked to vehicles/drivers, basic statistics
- Payment integration (sandbox gateway) for fines/fees/permits/toll; receipts
- **Done when:** a fine generated in Phase 4/5 can be paid and marked settled

### Phase 7 — Citizen portal, notifications & admin
- Citizen-facing endpoints: view vehicle/licence/fines, apply, track status, pay
- Notification service (SMS/email/in-app) triggered by expiry, violations,
  critical alerts — configurable rules
- Admin dashboard endpoints: user/role management, rule config, audit log
  access, reporting (vehicle/licence/violation/revenue reports, CSV/PDF export)
- **Done when:** a citizen can see and pay an outstanding fine and gets
  notified about it; an admin can pull a report

### Phase 8 — Hardening, deployment & polish
- Security pass: MFA for staff, session timeout, brute-force protection,
  HTTPS-only, secrets out of the repo
- Performance check on the toll compliance endpoint under simulated load
- Deploy to Render (web service + worker if using background notification/
  toll-sync jobs); point at Neon prod branch
- Only now consider Turso if there's a genuine offline/edge caching need
  (e.g. toll plaza working without connectivity, syncing on reconnect)
- Write the README (setup, env vars, how to run migrations, how to seed data)
- **Done when:** the app is live on Render, connected to Neon, with a
  README good enough that you could hand it to someone else cold

## 6. Non-functional expectations (keep in mind throughout, not just Phase 8)
- Sensitive data encrypted at rest where the DB supports it; never log
  secrets or full payment details
- Every state-changing endpoint writes to the audit log
- Config/secrets via environment variables only — `.env` in `.gitignore`,
  `.env.example` committed with placeholder values
- Prefer small, testable service functions over logic embedded in route
  handlers, especially for the compliance engine and payment reconciliation

## 7. Reference: full requirement list this system must satisfy
1. Vehicle Registration & Titling
2. Driver Licensing & Testing
3. Traffic Violation & Enforcement
4. Public Service Vehicle (PSV) Management
5. Vehicle Inspection & Roadworthiness
6. Insurance & Third-Party Verification
7. Accident & Incident Management
8. Integrated Toll Enforcement
9. ANPR & Vehicle Identification
10. e-Challan & Penalty Management
11. Citizen Self-Service Portal
12. Revenue & Payment Management
13. Notification Management
14. Administration
15. Security Requirements
16. Reporting & Analytics
17. Inter-Agency Integration
18. Performance
19. Availability & Disaster Recovery
20. Scalability
21. Toll-Gate Compliance Decision Logic (see Section 4 above)
22. Expected outcomes: improved road safety, faster non-compliance detection,
    better enforcement efficiency, reduced revenue leakage, stronger penalty
    collection, better compliance data, better accident intelligence,
    transparency, easier citizen access, toll gates as active compliance
    checkpoints rather than pure collection points
