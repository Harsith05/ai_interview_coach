"""Account settings: profile, password change and the user's own Gemini API keys."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from coach import apikeys
from coach.auth import MIN_PASSWORD_LEN, hash_password, require_user_page, verify_password
from coach.db import get_db
from coach.models import ApiKey, User
from coach.templating import templates

router = APIRouter()


def key_required_redirect(user: User) -> RedirectResponse | None:
    """Send users without a Gemini key to the API keys page before they start an AI session."""
    if apikeys.has_keys(user.id):
        return None
    return RedirectResponse("/settings/api-keys?need=1", status_code=303)


def _flash(request: Request, kind: str, text: str) -> None:
    request.session["flash"] = {"kind": kind, "text": text}


def _pop_flash(request: Request):
    return request.session.pop("flash", None)


# ---------------------------------------------------------------- account

@router.get("/settings")
def account_page(request: Request, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "account.html", {
        "user": user, "flash": _pop_flash(request), "key_count": len(apikeys.list_keys(db, user.id))})


@router.post("/settings/profile")
def update_profile(request: Request, name: str = Form(""), user: User = Depends(require_user_page),
                   db: Session = Depends(get_db)):
    name = name.strip()
    if not name or len(name) > 100:
        _flash(request, "error", "Please enter a name (up to 100 characters).")
    else:
        db.get(User, user.id).name = name
        _flash(request, "info", "Profile updated.")
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/password")
def change_password(request: Request, current_password: str = Form(""), new_password: str = Form(""),
                    confirm_password: str = Form(""), user: User = Depends(require_user_page),
                    db: Session = Depends(get_db)):
    row = db.get(User, user.id)
    if not verify_password(current_password, row.password_hash):
        _flash(request, "error", "Your current password is incorrect.")
    elif len(new_password) < MIN_PASSWORD_LEN or len(new_password.encode()) > 72:
        _flash(request, "error", f"The new password must be {MIN_PASSWORD_LEN}–72 characters.")
    elif new_password != confirm_password:
        _flash(request, "error", "The new passwords do not match.")
    else:
        row.password_hash = hash_password(new_password)
        _flash(request, "info", "Password changed.")
    return RedirectResponse("/settings", status_code=303)


# ---------------------------------------------------------------- Gemini API keys

def _own_key(db: Session, key_id: int, user: User) -> ApiKey:
    row = db.get(ApiKey, key_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Key not found")
    return row


@router.get("/settings/api-keys")
def keys_page(request: Request, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    keys = apikeys.list_keys(db, user.id)
    return templates.TemplateResponse(request, "api_keys.html", {
        "user": user, "keys": keys, "flash": _pop_flash(request), "max_keys": apikeys.MAX_KEYS,
        "need": request.query_params.get("need") and not keys})


@router.post("/settings/api-keys")
async def add_key(request: Request, label: str = Form(""), api_key: str = Form(""),
                  user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    row, message = await run_in_threadpool(apikeys.add_key, db, user.id, label, api_key)
    _flash(request, "info" if row else "error", f"Key saved. {message}" if row else message)
    return RedirectResponse("/settings/api-keys", status_code=303)


@router.post("/settings/api-keys/{key_id}/edit")
async def edit_key(request: Request, key_id: int, label: str = Form(""), api_key: str = Form(""),
                   user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    row = _own_key(db, key_id, user)
    ok, message = await run_in_threadpool(apikeys.update_key, db, row, label, api_key)
    _flash(request, "info" if ok else "error", message)
    return RedirectResponse("/settings/api-keys", status_code=303)


@router.post("/settings/api-keys/{key_id}/test")
async def test_key(request: Request, key_id: int, user: User = Depends(require_user_page),
                   db: Session = Depends(get_db)):
    row = _own_key(db, key_id, user)
    ok, message = await run_in_threadpool(apikeys.test_key, db, row)
    _flash(request, "info" if ok else "error", f"“{row.label}”: {message}")
    return RedirectResponse("/settings/api-keys", status_code=303)


@router.post("/settings/api-keys/{key_id}/default")
def make_default(request: Request, key_id: int, user: User = Depends(require_user_page),
                 db: Session = Depends(get_db)):
    row = _own_key(db, key_id, user)
    apikeys.set_default(db, user.id, row.id)
    _flash(request, "info", f"“{row.label}” is now your default key.")
    return RedirectResponse("/settings/api-keys", status_code=303)


@router.post("/settings/api-keys/{key_id}/delete")
def delete_key(request: Request, key_id: int, user: User = Depends(require_user_page),
               db: Session = Depends(get_db)):
    row = _own_key(db, key_id, user)
    label = row.label
    apikeys.delete_key(db, row)
    _flash(request, "info", f"Deleted “{label}”.")
    return RedirectResponse("/settings/api-keys", status_code=303)
