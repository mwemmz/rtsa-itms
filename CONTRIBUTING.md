# Contributing

Simple workflow — since each developer owns their own files, we don't need
branches or pull requests. Just:

1. **Clone once**: `git clone <repo-url>`
2. **Pull before you start work each session**: `git pull`
3. **Edit only your own module's files** (see the ownership table below /
   in the project spec).
4. **Commit with a clear message**:
   `git add . && git commit -m "add vehicle registration endpoint"`
5. **Push straight to `main`**: `git push`

## Two rules
- Always `git pull` before you start editing, so you're never pushing from
  a stale copy.
- If you ever need to touch a shared file (`app/main.py`, `app/core/db.py`,
  config files), message the group first so two people don't edit it at
  the same time.

## Module ownership
Stick to your own module's files (see the ownership table in the project
spec) at all times. This is what makes pushing straight to `main` safe —
conflicts only happen when two people edit the same file.

## Environment
- Copy `.env.example` to `.env` and fill in your own values.
- Never commit `.env` or real credentials.
- Everyone connects to the same Neon database unless told otherwise.

## Migrations
- Use Alembic. Only touch migrations for tables your module owns.
- Run `alembic upgrade head` after pulling, in case someone else added one.