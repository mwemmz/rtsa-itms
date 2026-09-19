# RTSA Integrated Transport Management System (ITMS) — Project Spec

10-week school group project. 5 developers, Python (FastAPI), Neon (Postgres),
Render (hosting).

## Stack

- **API**: FastAPI (Python 3.11)
- **Database**: Neon serverless Postgres — SQLAlchemy + Alembic migrations
- **Hosting**: Render
- **Optional edge/cache**: Turso — only if a real offline/edge need shows up
  (e.g. toll-plaza offline caching)

## Module ownership

Each developer owns their own files/folders and pushes straight to `main` (no
branches/PRs). Conflicts only happen if two people edit the same file, so stick
to your area.

| Developer | Owns | Folder(s) |
|---|---|---|
| Developer 1 | Vehicle registration, driver licensing, inspection, insurance | `app/api/vehicles.py`, `app/api/drivers.py`, `app/api/inspections.py`, `app/api/insurance.py`, matching `app/models/` |
| Developer 2 | Violations, e-Challans, ANPR, toll compliance engine | `app/api/violations.py`, `app/api/anpr.py`, `app/api/toll.py`, `app/services/compliance_engine.py` |
| Developer 3 | PSV, accidents, inter-agency integration | `app/api/psv.py`, `app/api/accidents.py`, `app/api/inter_agency.py` |
| Developer 4 | Citizen portal, payments, notifications | `app/api/citizen.py`, `app/api/payments.py`, `app/services/notifications.py` |
| Developer 5 | Auth/RBAC, admin, security, reporting, CI/CD, Render+Neon infra | `app/core/`, `app/api/admin.py`, `app/api/reports.py`, `.github/workflows/` |

Shared files (`app/core/db.py`, `app/main.py`, `app/models/base.py`) are edited
by Developer 5 or by agreement in the weekly sync.

## 8-week phased roadmap

- **Week 1 — Setup & Data Modeling:** each developer designs their schema and
  migrates it; Developer 5 sets up CI, Render services, Neon project, RBAC
  design. Sync: confirm schemas don't conflict.
- **Week 2 — Core CRUD & Auth:** Developer 5 ships JWT/RBAC middleware; others
  build CRUD for their core entities. Sync: everyone wires in the shared auth.
- **Week 3 — Domain Business Logic:** licensing/inspection workflows (Dev 1),
  ANPR + fine linking (Dev 2), PSV/accident linking (Dev 3), fine viewing on
  citizen portal (Dev 4), audit log utility + admin skeleton (Dev 5).
  Sync: Dev 4 consumes Dev 2's API for the first time.
- **Week 4 — Mid-Point Integration & Toll Engine:** insurance/blacklist flags
  (Dev 1), toll compliance engine (Dev 2), inter-agency mocks (Dev 3), sandbox
  payments (Dev 4), reporting v1 + export utility (Dev 5).
  Sync: full demo of a vehicle flowing through the toll engine.
- **Week 5 — Notifications, PSV & Remaining Features:** history/search (Dev 1),
  e-Challan lifecycle + offline toll caching (Dev 2), route compliance/geo-fencing
  (Dev 3), notification service (Dev 4), security hardening + admin config (Dev 5).
  Sync: notifications tested against two other modules' triggers.
- **Week 6 — Reporting, Analytics & Citizen Portal Completion:** polish reports
  feed (Dev 1), toll analytics feed (Dev 2), inter-agency/accident reports
  (Dev 3), full citizen portal (Dev 4), full reporting dashboard (Dev 5).
  Sync: full regression across citizen/officer/admin journeys.
- **Week 7 — Hardening, Performance & Testing:** bug fixes and unit tests
  (Dev 1), load-test toll engine (Dev 2), integration tests (Dev 3), payment/
  notification edge cases (Dev 4), security review + backups/scalability (Dev 5).
  Sync: joint bug-bash.
- **Week 8 — Final Integration, Deployment & Demo Prep:** everyone freezes
  features and fixes bugs from the bug-bash; Dev 5 leads production deploy to
  Render + Neon prod branch. Sync: full dry-run demo together.