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
* Admins can click *System health → Back up now* (`POST /api/system/backups`), which verifies the file and copies it
  off-site. The worker also takes one every 24 h on PostgreSQL - but see below: on Render's free plan it may not run.
* **Render's disk is ephemeral** – files written there vanish on redeploy. Two independent safety nets:

### 1. Off-site copies (S3-compatible storage)

Set `BACKUP_S3_BUCKET` (plus endpoint, region and keys - see `.env.example`) and every backup is copied to
Amazon S3, Cloudflare R2, Backblaze B2 or MinIO, confirmed by size, and old copies beyond `BACKUP_S3_RETENTION` (30)
are deleted. *System health* shows whether this is on, how many copies exist and the latest one.

* **Encrypt them.** Backups contain personal data (names, NRC numbers, payments). With `BACKUP_ENCRYPTION_KEY` set
  (a Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) they're
  encrypted before upload and stored as `<name>.enc`.
* **Keep that key outside the deployment** (a password manager the whole team can reach). It's deliberately separate
  from `ENCRYPTION_KEY`/`SECRET_KEY`: if the server and its secrets are lost, you still need to be able to read the backups.
  Without the key they are unrecoverable.
* Give the storage credentials write/list/delete on that bucket only, and keep the bucket private.

**Nightly schedule:** `.github/workflows/nightly-backup.yml` runs `scripts.backup --json` at 00:30 UTC from GitHub
Actions (Render's disk is wiped on deploy and its free plan doesn't run background workers). Add the repository
secrets it lists; until then it skips itself. It refuses to upload without `BACKUP_ENCRYPTION_KEY` because the
repository (and so its Actions logs) is public. It uses the JSON format because the runner's `pg_dump` may be older than
the database server. Run it once by hand (*Actions → Nightly database backup → Run workflow*) and check the copy
appears in the bucket.

```bash
python -m scripts.offsite --list                 # off-site copies, newest first
python -m scripts.offsite --download latest      # fetch + decrypt into BACKUP_DIR
```

### 2. Neon point-in-time restore

Neon keeps a history of the database and can restore (or branch) it to any moment inside the plan's history window.
It's the fastest way back from a bad deploy or an accidental delete, and doesn't depend on anything above - but it lives
with the same provider as the database, so it isn't a substitute for off-site copies if the Neon project itself is lost.
Check the window your plan gives you in the Neon console (*Settings → Storage / History retention*).

## Restore

Always rehearse against a scratch database first.

```bash
# 1. point DATABASE_URL at the empty target database
alembic upgrade head                                   # create the schema
python -m scripts.restore backups/<file>.json.gz --yes # portable snapshot
# or, for a pg_dump file:
python -m scripts.restore backups/<file>.dump --yes    # runs pg_restore --clean --if-exists
# or straight from off-site storage (downloads and decrypts first; needs BACKUP_ENCRYPTION_KEY):
python -m scripts.restore --from-offsite latest --yes
```

Restoring replaces data: the script refuses to run without `--yes`, and refuses a non-empty database unless `--wipe` is added.

## Targets (suggested – confirm with the client)

* RPO ≤ 24 h with the nightly off-site backup; near-zero with Neon PITR while the Neon project exists.
* RTO ≈ 30 min: provision DB → `alembic upgrade head` → restore → deploy → smoke test (`/health/ready`, login, `/api/system/metrics`).

## Failure playbook

| Symptom | Action |
|---|---|
| `/health/ready` 503 | Check DB status page/credentials; the app reconnects automatically (`pool_pre_ping`). |
| Bad deploy | Roll back in Render; migrations are additive (downgrade available: `alembic downgrade -1`). |
| Data corruption / accidental delete | Restore latest good backup into a scratch DB, compare, then restore or copy back the affected rows. |
| Lost admin MFA device | Another admin: Users → *Clear MFA*. Or use one of the user's recovery codes. |
| Leaked API key | Agency integrations → *Rotate key* (old key stops working immediately) or *Disable*. |
| Neon project deleted / unreachable | New database → `alembic upgrade head` → `python -m scripts.restore --from-offsite latest --yes` → point `DATABASE_URL` at it. |
| Nightly backup job failing | *Actions → Nightly database backup* shows why (missing secret, storage refused, database unreachable); the job fails loudly on any of these. |
| Leaked storage credentials | Revoke/rotate them at the storage provider and update the secrets. Encrypted copies stay unreadable without `BACKUP_ENCRYPTION_KEY`. |
| Lost `BACKUP_ENCRYPTION_KEY` | Existing encrypted copies can't be read. Generate a new key, update it everywhere, and take a fresh backup immediately. |
| Leaked `SECRET_KEY` | Rotate it; all sessions become invalid. If `ENCRYPTION_KEY` is unset, MFA secrets were derived from it, so users must re-enrol MFA – set `ENCRYPTION_KEY` explicitly in production to avoid this. |
