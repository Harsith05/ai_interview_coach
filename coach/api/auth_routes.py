"""Registration, login and logout pages."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import hashlib
import logging
import time
from datetime import datetime, timezone
from collections import defaultdict, deque

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from coach.auth import (EMAIL_RE, MIN_PASSWORD_LEN, authenticate, current_user_optional, hash_password,
                        login_session, logout_session, validate_registration)
from coach.config import APP_BASE_URL, SECRET_KEY
from coach.mailer import send_email
from coach.db import get_db
from coach.models import LoginEvent, User
from coach.templating import templates

router = APIRouter()
log = logging.getLogger(__name__)


def record_login(db: Session, request: Request, user: User | None, email: str, success: bool,
                 kind: str = "login") -> None:
    """Login history for the admin panel; also updates the user's last login and login count."""
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
    db.add(LoginEvent(user_id=user.id if user else None, email=(email or "").strip().lower()[:255],
                      success=success, kind=kind, ip=ip,
                      user_agent=(request.headers.get("user-agent") or "")[:300]))
    if user is not None and success:
        row = db.get(User, user.id)
        row.last_login_at = datetime.now(timezone.utc)
        row.login_count = (row.login_count or 0) + 1


def _safe_next(url: str | None) -> str:
    # Only allow same-site relative redirects (no "//evil.com" or absolute URLs).
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return "/"


@router.get("/register")
def register_page(request: Request, user: User | None = Depends(current_user_optional)):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "register.html", {"errors": {}, "form": {}})


@router.post("/register")
def register(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    db: Session = Depends(get_db),
):
    form = {"name": name.strip(), "email": email.strip().lower()}
    errors = validate_registration(name, email, password, confirm_password)
    if not errors and db.query(User).filter(User.email == form["email"]).first():
        errors["email"] = "An account with this email already exists."
    if errors:
        return templates.TemplateResponse(request, "register.html", {"errors": errors, "form": form},
                                          status_code=400)

    user = User(name=form["name"], email=form["email"], password_hash=hash_password(password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:  # two sign-ups with the same email at the same moment
        db.rollback()
        errors["email"] = "An account with this email already exists."
        return templates.TemplateResponse(request, "register.html", {"errors": errors, "form": form},
                                          status_code=400)
    login_session(request, user)
    record_login(db, request, user, user.email, True, kind="register")
    return RedirectResponse("/", status_code=303)


@router.get("/login")
def login_page(request: Request, next: str | None = None, user: User | None = Depends(current_user_optional)):
    if user:
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None, "form": {}, "next": next or ""})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    user = authenticate(db, email, password)
    if user is None:
        known = db.query(User).filter(User.email == email.strip().lower()).first()
        record_login(db, request, known, email, False)
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Incorrect email or password.", "form": {"email": email.strip()}, "next": next},
            status_code=400,
        )
    login_session(request, user)
    record_login(db, request, user, user.email, True)
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/logout")
def logout(request: Request):
    logout_session(request)
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------- password reset

RESET_MAX_AGE = 30 * 60   # seconds
_reset = URLSafeTimedSerializer(SECRET_KEY, salt="password-reset")
_reset_requests: dict[str, deque] = defaultdict(deque)   # email -> request times (simple rate limit)


def _pw_fingerprint(password_hash: str) -> str:
    # Part of the token: once the password changes, older reset links stop working (single use).
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def _reset_user(db: Session, token: str) -> User | None:
    try:
        data = _reset.loads(token, max_age=RESET_MAX_AGE)
    except (SignatureExpired, BadSignature):
        return None
    user = db.get(User, data.get("u"))
    if user is None or data.get("h") != _pw_fingerprint(user.password_hash):
        return None
    return user


@router.get("/forgot-password")
def forgot_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {"sent": False, "email": ""})


@router.post("/forgot-password")
def forgot(request: Request, email: str = Form(""), db: Session = Depends(get_db)):
    email = email.strip().lower()
    now = time.time()
    recent = _reset_requests[email]
    while recent and now - recent[0] > 900:
        recent.popleft()
    if EMAIL_RE.match(email) and len(recent) < 3:
        recent.append(now)
        user = db.query(User).filter(User.email == email).first()
        if user is not None:
            token = _reset.dumps({"u": user.id, "h": _pw_fingerprint(user.password_hash)})
            link = f"{APP_BASE_URL}/reset-password/{token}"
            send_email(
                user.email, "Reset your AI Interview Coach password",
                "\n".join([f"Hi {user.name},", "",
                           "Use this link to choose a new password (valid for 30 minutes):", link, "",
                           "If you didn't ask for this, you can ignore this email."]),
                f"<p>Hi {user.name},</p><p><a href=\"{link}\">Choose a new password</a> (valid for 30 minutes).</p>"
                "<p>If you didn't ask for this, you can ignore this email.</p>",
            )
    # Same answer whether or not the account exists, so emails can't be probed.
    return templates.TemplateResponse(request, "forgot_password.html", {"sent": True, "email": email})


@router.get("/reset-password/{token}")
def reset_page(request: Request, token: str, db: Session = Depends(get_db)):
    user = _reset_user(db, token)
    return templates.TemplateResponse(request, "reset_password.html",
                                      {"valid": user is not None, "token": token, "error": None})


@router.post("/reset-password/{token}")
def reset(request: Request, token: str, password: str = Form(""), confirm_password: str = Form(""),
          db: Session = Depends(get_db)):
    user = _reset_user(db, token)
    if user is None:
        return templates.TemplateResponse(request, "reset_password.html",
                                          {"valid": False, "token": token, "error": None}, status_code=400)
    error = None
    if len(password) < MIN_PASSWORD_LEN or len(password.encode()) > 72:
        error = f"Password must be {MIN_PASSWORD_LEN}–72 characters."
    elif password != confirm_password:
        error = "Passwords do not match."
    if error:
        return templates.TemplateResponse(request, "reset_password.html",
                                          {"valid": True, "token": token, "error": error}, status_code=400)
    user.password_hash = hash_password(password)
    login_session(request, user)
    record_login(db, request, user, user.email, True, kind="reset")
    request.session["flash"] = {"kind": "info", "text": "Your password has been reset."}
    return RedirectResponse("/", status_code=303)
