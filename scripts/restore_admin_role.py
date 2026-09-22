"""Restore the platform admin account to the 'admin' role.

If the only admin was demoted (e.g. via the Users module), there is nobody left
to grant roles back, so use this small script against the Neon/Render database:

    python -m scripts.restore_admin_role admin@rtsa.gov.zm

It only flips that account's role to ``admin`` and reports the current role.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402


def main() -> int:
    email = sys.argv[1] if len(sys.argv) > 1 else "admin@rtsa.gov.zm"
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"No user found for {email}")
            return 1
        print(f"Found {email}  current_role={user.role.value}")
        if user.role != UserRole.ADMIN:
            user.role = UserRole.ADMIN
            db.commit()
            db.refresh(user)
            print(f"Restored role -> {user.role.value} (all permissions now granted)")
        else:
            print("Already admin, nothing to do.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())