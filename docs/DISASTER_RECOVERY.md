# Availability & disaster recovery

## Health probes

| Endpoint | Use |
|---|---|
| `GET /health/live` | Process is up (restart if this fails). |
| `GET /health/ready` | Database answers; returns **503** otherwise so a load balancer stops routing here. Point Render's `healthCheckPath` at it if you want traffic withheld during DB outages (`/health` is kept for backwards compatibility and always returns 200). |

## Backups

```bash
python -m scripts.backup                 # pg_dump custom format if pg_dump exists, else portable JSON snapshot
python -m scripts.backup --json          # force the portable format
python -m scripts.backup --verify FILE   # check a backup is complete
```

* Written to `BACKUP_DIR` (default `backups/`), newest `BACKUP_RETENTION` (14) kept.
* The worker takes one every 24 h on PostgreSQL; admins can also click *System health → Back up now* (`POST /api/system/backups`), which verifies the file.
* **Render's disk is ephemeral** – files written there vanish on redeploy. For real durability either
  (a) rely on Neon's point-in-time restore, and/or (b) run `scripts.backup` from a scheduled job that copies the file to
  object storage. Uploading off-box is *not* implemented here.

## Restore

Always rehearse against a scratch database first.

```bash
# 1. point DATABASE_URL at the empty target database
alembic upgrade head                                   # create the schema
python -m scripts.restore backups/<file>.json.gz --yes # portable snapshot
# or, for a pg_dump file:
python -m scripts.restore backups/<file>.dump --yes    # runs pg_restore --clean --if-exists
```

Restoring replaces data: the script refuses to run without `--yes`, and refuses a non-empty database unless `--wipe` is added.

## Targets (suggested – confirm with the client)

* RPO ≤ 24 h with the daily backup; near-zero with Neon PITR.
* RTO ≈ 30 min: provision DB → `alembic upgrade head` → restore → deploy → smoke test (`/health/ready`, login, `/api/system/metrics`).

## Failure playbook

| Symptom | Action |
|---|---|
| `/health/ready` 503 | Check DB status page/credentials; the app reconnects automatically (`pool_pre_ping`). |
| Bad deploy | Roll back in Render; migrations are additive (downgrade available: `alembic downgrade -1`). |
| Data corruption / accidental delete | Restore latest good backup into a scratch DB, compare, then restore or copy back the affected rows. |
| Lost admin MFA device | Another admin: Users → *Clear MFA*. Or use one of the user's recovery codes. |
| Leaked API key | Agency integrations → *Rotate key* (old key stops working immediately) or *Disable*. |
| Leaked `SECRET_KEY` | Rotate it; all sessions become invalid. If `ENCRYPTION_KEY` is unset, MFA secrets were derived from it, so users must re-enrol MFA – set `ENCRYPTION_KEY` explicitly in production to avoid this. |
