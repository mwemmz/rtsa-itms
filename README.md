# RTSA Integrated Transport Management System (ITMS)

Backend API for the RTSA's Integrated Transport Management System. Covers
vehicle registration, driver licensing, vehicle inspection, insurance
verification, PSV management, accidents, traffic enforcement, ANPR, e-Challan
generation, toll-gate compliance enforcement, payments, notifications, a
citizen portal, admin tooling, reporting — plus a **live Lusaka road network**:
road leaflets/GeoJSON maps, incident alerts with reroute suggestions, an
in-API route planner with alternatives, and a **driver self-service portal**.

Built with **FastAPI + SQLAlchemy + Alembic** on a **Neon (Postgres)** database.

## Architecture

```
app/
  core/        config, db session, security (JWT/RBAC), rate limiting, audit util
  models/      SQLAlchemy models, one file per domain (incl. road_network graph)
  schemas/     Pydantic request/response models
  api/         routers, one file per module
  services/    business logic (compliance engine, routing/Dijkstra, notifications, audit)
  workers/     background jobs (notification dispatch, licence-expiry scan)
alembic/       migrations
tests/         API integration tests
scripts/       seed data (demo users, vehicles, Lusaka road network)
```

## Prerequisites

- Python 3.11+
- A PostgreSQL database (Neon, or locally via Docker)

## Setup

1. **Create a virtual environment and install dependencies**

   ```bash
   python -m venv .venv
   .venv\Scripts\activate       # Windows  (or `source .venv/bin/activate` on Linux/macOS)
   pip install -r requirements.txt
   ```

2. **Configure environment variables**

   Copy `.env.example` to `.env` and fill in your values:

   ```bash
   cp .env.example .env
   ```

   | Variable        | Description                                        |
   |-----------------|----------------------------------------------------|
   | `DATABASE_URL`  | Postgres connection string (Neon supports `?sslmode=require`) |
   | `SECRET_KEY`    | Secret used to sign JWTs — use a strong random value in production |
   | `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT lifetime (default 30)                 |
   | `ENVIRONMENT`   | `development` or `production`                       |

   `.env` is git-ignored; never commit it.

3. **Run migrations**

   ```bash
   alembic upgrade head
   ```

   (On Windows with the venv active, use `python -m alembic upgrade head`.)

4. **Start the API**

   ```bash
   uvicorn main:app --reload
   ```

   Interactive docs at http://localhost:8000/docs

5. **(Optional) Seed demo data**

   ```bash
   python scripts/seed.py
   ```

   Creates demo users, vehicles, default notification rules, and the Lusaka
   road network (12 intersections, 9 roads, 16 segments, one live demo
   incident on Great East Road):
   - `admin@rtsa.gov.zm` / `admin123` (admin)
   - `officer@rtsa.gov.zm` / `officer123` (officer)
   - `citizen@example.com` / `citizen123` (citizen — owns `BAL 1234` & `BAL 5678`,
     has a licence expiring in 25 days, and an unpaid ZMW 600 fine on `BAL 5678`)
   - Vehicles: `BAL 1234` (fully compliant), `BAL 5678` (no insurance/fitness), `BAL 9999` (blacklisted)

## Running tests

```bash
python -m pytest tests/ -q
```

Tests use an in-memory-on-disk SQLite database so they run without Postgres.
A `conftest.py` rebuilds the schema at the start of every session.

## Background worker

The notification worker:
- polls for pending notifications and marks them sent (swap in real SMS/email
  providers in production),
- scans for licences expiring within 30 days and sends a `licence_expiring`
  warning (deduplicated per day).

```bash
python -m app.workers.notification_worker
```

## Key flows

### Toll-gate compliance check (core business logic)

POST `/api/toll/events` with a plate number and gate id. The engine looks up
the vehicle and checks, in a single indexed query path:

1. Registration status
2. Insurance validity
3. Fitness certificate validity
4. Outstanding fines / challans
5. Blacklist status
6. PSV permit (if the vehicle has ever held one)

If all pass → `compliant`. Otherwise → `flagged` with the failing checks,
auto-generates an e-Challan, alerts the operator, notifies the owner, and
records the event to the audit log.

```bash
curl -X POST http://localhost:8000/api/toll/events \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"plate_number": "BAL 1234", "gate_id": "KAFUE-01", "toll_amount": 500}'
```

### End-to-end: violation → e-Challan → payment

1. Officer registers a violation: `POST /api/enforcement/violations`
2. An e-Challan is auto-generated with reference, penalty, due date.
3. Citizen sees it: `GET /api/citizen/my-fines`
4. Citizen pays: `POST /api/payments/` with `payment_type: "fine"`
   (sandbox gateway resolves instantly and marks the challan `paid`).

### Live road alerts + route guidance

1. Officer reports an incident: `POST /api/incidents/` with an
   `incident_type` (`accident`, `road_closed`, ...) and a `segment_id`.
2. The system broadcasts a `road_alert` to every registered motorist and
   (for `accident`/`road_closed`) treats the segment as blocked for routing.
3. Motorists see the live feed: `GET /api/incidents/alerts`
4. They plan a route: `GET /api/routing/route?from=...&to=...` — the planner
   runs Dijkstra over the road graph, avoids blocked segments, reports how many
   incidents it dodged, and returns up to two alternatives.

```bash
curl "http://localhost:8000/api/routing/route?from=Great%20East%20/%20Airport%20Junction&to=CBD%20-%20Cairo%20Road" \
  -H "Authorization: Bearer <token>"
```

The road network is explorable as a **leaflet** (`GET /api/road-network/leaflet`,
human-readable status of every road) and as **GeoJSON** (`GET /api/road-network/geojson`,
ready for a Leaflet/OpenLayers map), plus a live board at
`GET /api/routing/status`.

### Driver self-service portal

`/api/portal` is the driver-facing UI backend (login via `/api/portal/login`):

- `GET /api/portal/dashboard` — licence state, vehicles, outstanding fines, road alerts
- `GET /api/portal/licence` + `POST /api/portal/licence/renew` (sandbox fee payment)
- `GET /api/portal/fines` + `POST /api/portal/fines/{id}/pay`
- `GET /api/portal/alerts` — active incident alerts

## Roles (RBAC)

| Role            | Scope                                                        |
|-----------------|--------------------------------------------------------------|
| `citizen`       | Own vehicles/fines, apply for licences, pay                   |
| `officer`       | Record violations, inspections, issue licences                |
| `toll_operator` | Post toll events                                               |
| `admin`         | User management, notification rules, audit logs, reports       |

## Deployment (Render + Neon)

The repo ships with `render.yaml` (a Render Blueprint) defining two services:

- **web** `rtsa-itms-api` — runs the API (`uvicorn main:app`)
- **worker** `rtsa-itms-notification-worker` — runs the notification dispatcher

Both point at the same `DATABASE_URL`. A `releaseCommand` runs
`alembic upgrade head && python -m scripts.seed` against Neon before each deploy,
so migrations and demo data apply automatically.

### 1. Create the Neon database

1. Sign up at https://neon.tech and create a **new project**.
2. Pick a region close to your Render region (e.g. **Frankfurt (eu-central-1)**) and a name like `rtsa-itms`.
3. On the dashboard **Connect**, copy the connection string. Use the
   **Pooled connection** (PgBouncer) string for serverless/low-connection use:

   ```
   postgresql://user:password@ep-xxx-pooler.region.aws.neon.tech/rtsa_itms?sslmode=require
   ```

   (The direct connection string works too for this sync SQLAlchemy engine —
   just make sure it ends with `?sslmode=require`.)
4. Note the **default database name** — change the path in the URL to match it
   (`.../rtsa_itms` if you created a database called `rtsa_itms`, otherwise the
   name shown in the dashboard, e.g. `neondb`).

### 2. Push the code to GitHub

```bash
git remote add origin git@github.com:<you>/rtsa-itms.git
git push -u origin main
```

### 3. Deploy on Render

1. Go to https://dashboard.render.com → **New + → Blueprint** (only blueprint
   mode reads `render.yaml` automatically; you can also create each service
   manually from the same repo).
2. Connect the GitHub repo. Render will detect `render.yaml` and propose the
   two services.
3. After the services are created, open each one → **Environment**, and set:
   - `DATABASE_URL` → your Neon pooled connection string
   - `SECRET_KEY` → a strong random value, e.g.
     `openssl rand -hex 32` (Linux/macOS) or
     `python -c "import secrets; print(secrets.token_hex(32))"`
   - `ENVIRONMENT` is set to `production` by the blueprint (no need to change).
4. **Manual Deploy** the web service. The release command will migrate + seed
   the Neon DB automatically.

> Free plan notes: the web service sleeps after 15 minutes without traffic and
> wakes on the next request (a few seconds of cold start). The connection pool
> uses `pool_pre_ping`, so stale Neon connections are re-negotiated safely.

### 4. Verify

- `GET /health` on the web service URL → `{"status": "healthy", "database": "connected"}`
- `GET /` → the RTSA landing page
- `GET /docs` → Swagger UI
- Login with the seeded admin: `admin@rtsa.gov.zm` / `admin123`.

> Demo data (demo users, vehicles, Lusaka road network, portal citizen) is
> created by `python -m scripts.seed`, which the blueprint runs automatically
> before each deploy. Seeding is idempotent — it skips records that already exist.

## Security notes

- Passwords hashed with bcrypt; JWTs signed with `SECRET_KEY` and auto-expire.
- Login is rate-limited per IP (5 attempts / 5 min) to blunt brute force.
- Logging never includes passwords, secrets, or full payment details.
- All secrets come from environment variables; `.env` is git-ignored.

## API surface

| Area                     | Base path                        |
|--------------------------|----------------------------------|
| Auth                     | `/api/auth`                      |
| Vehicles                 | `/api/vehicles`                  |
| Drivers                  | `/api/drivers`                   |
| Inspections & fitness    | `/api/inspections`               |
| Insurance                | `/api/insurance`                 |
| Licence applications     | `/api/licence-applications`      |
| Enforcement & e-Challan  | `/api/enforcement`               |
| ANPR                     | `/api/anpr`                      |
| Toll compliance          | `/api/toll`                      |
| PSV                      | `/api/psv`                       |
| Accidents                | `/api/accidents`                 |
| Payments                 | `/api/payments`                  |
| Citizen portal           | `/api/citizen`                   |
| Notifications            | `/api/notifications`             |
| Admin                    | `/api/admin`                     |
| Road network (roads/intersections/segments, GeoJSON, leaflet) | `/api/road-network` |
| Incidents & alerts       | `/api/incidents`                 |
| Routing & status board   | `/api/routing`                   |
| Driver portal            | `/api/portal`                    |
| Health                   | `/health`                        |