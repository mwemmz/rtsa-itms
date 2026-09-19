# RTSA Integrated Transport Management System (ITMS)

Group project — 5 developers, Python (FastAPI), Neon (Postgres), Render (hosting).

## Setup
1. Clone the repo.
2. `python -m venv venv && source venv/bin/activate`
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env`, fill in your Neon connection string.
5. `alembic upgrade head`
6. `uvicorn app.main:app --reload`

## Workflow
Simple: pull, edit your own module's files, commit, push to `main`.
See CONTRIBUTING.md for the two rules that keep this conflict-free.

## Team
See CONTRIBUTING.md for module ownership.

## Roadmap
See PROJECT_SPEC.md for the 8-week phased build plan.