"""Manage admin users from the command line.

    python -m coach.admin_cli make-admin you@example.com
    python -m coach.admin_cli remove-admin you@example.com
    python -m coach.admin_cli list-admins
"""
import sys

from coach.db import db_session, init_db
from coach.models import User


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("make-admin", "remove-admin", "list-admins"):
        print(__doc__)
        return 1
    init_db()   # makes sure the admin columns exist
    cmd = argv[0]
    with db_session() as db:
        if cmd == "list-admins":
            admins = db.query(User).filter(User.is_admin.is_(True)).order_by(User.email).all()
            print("\n".join(f"{u.email}  ({u.name})" for u in admins) or "No admins yet.")
            return 0
        if len(argv) < 2:
            print("Please give the user's email.")
            return 1
        user = db.query(User).filter(User.email == argv[1].strip().lower()).first()
        if user is None:
            print(f"No user with email {argv[1]!r}. They need to sign up first.")
            return 1
        user.is_admin = cmd == "make-admin"
        print(f"{user.email} is {'now an admin' if user.is_admin else 'no longer an admin'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
