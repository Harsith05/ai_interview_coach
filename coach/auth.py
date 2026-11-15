"""Password hashing and the logged-in-user dependencies.

Sessions use Starlette's signed-cookie SessionMiddleware (configured in main.py): the cookie
only holds the user id, signed with SECRET_KEY.
"""
import re

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from coach.db import db_session
from coach.models import User

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8


def hash_password(password: str) -> str:
    # bcrypt only uses the first 72 bytes; reject longer passwords at validation time.
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# A real hash to compare against when the email doesn't exist, so login takes the same time
# whether or not the account exists.
_DUMMY_HASH = hash_password("timing-equalizer-password")


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def validate_registration(name: str, email: str, password: str, confirm: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not name.strip():
        errors["name"] = "Please enter your name."
    elif len(name.strip()) > 100:
        errors["name"] = "Name is too long."
    if not EMAIL_RE.match(email.strip()):
        errors["email"] = "Please enter a valid email address."
    if len(password) < MIN_PASSWORD_LEN:
        errors["password"] = f"Password must be at least {MIN_PASSWORD_LEN} characters."
    elif len(password.encode()) > 72:
        errors["password"] = "Password must be at most 72 bytes."
    if password != confirm:
        errors["confirm_password"] = "Passwords do not match."
    return errors


def login_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id


def logout_session(request: Request) -> None:
    request.session.clear()


def current_user_optional(request: Request) -> User | None:
    """Look the user up with a short-lived session.

    Using the request-scoped session here would keep a pooled DB connection checked out for
    the whole request, including slow ones (camera-frame analysis, the webcam stream), and a
    burst of those could leave no connections for ordinary pages like the dashboard.
    """
    uid = request.session.get("user_id")
    if not uid:
        return None
    with db_session() as db:
        return db.get(User, uid)  # attributes stay loaded (expire_on_commit=False)


class LoginRequired(Exception):
    """Raised by page dependencies; main.py turns it into a redirect to /login."""

    def __init__(self, next_url: str):
        self.next_url = next_url


def require_user_page(request: Request, user: User | None = Depends(current_user_optional)) -> User:
    if user is None:
        raise LoginRequired(str(request.url.path))
    return user


def require_user_api(user: User | None = Depends(current_user_optional)) -> User:
    if user is None:
        raise HTTPException(401, "Please log in.")
    return user
