"""Users' own Gemini API keys: encrypted storage, validation and lookup.

Keys are encrypted with Fernet (AES-128-CBC + HMAC) using ENCRYPTION_KEY, or a key derived
from SECRET_KEY if that isn't set. Plain keys never leave the server and are never shown again
after saving: the UI only gets the last 4 characters.
"""
import base64
import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from coach.config import ENCRYPTION_KEY, GEMINI_MODEL, SECRET_KEY
from coach.db import db_session
from coach.models import ApiKey

log = logging.getLogger(__name__)
MAX_KEYS = 10


class NoApiKey(Exception):
    """The user hasn't added a Gemini API key yet."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    if ENCRYPTION_KEY:
        return Fernet(ENCRYPTION_KEY.encode())
    derived = hashlib.sha256(("api-key-encryption:" + SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        log.error("Could not decrypt an API key (was ENCRYPTION_KEY or SECRET_KEY changed?)")
        return None


def clean(key: str) -> str:
    return "".join((key or "").split())   # people often paste with spaces/newlines


def looks_valid(key: str) -> bool:
    # Google AI Studio keys start with "AIza" and are 39 characters long.
    return key.startswith("AIza") and 30 <= len(key) <= 60


def check_key(key: str) -> tuple[bool, str]:
    """Ask Google whether the key works. Listing models is free (no generation quota used)."""
    from google import genai

    try:
        client = genai.Client(api_key=key, http_options={"timeout": 15000})
        names = [m.name for m in client.models.list(config={"page_size": 100})]
    except Exception as e:
        msg = str(e)
        if "API_KEY_INVALID" in msg or "API key not valid" in msg:
            return False, "Google says this API key is not valid."
        if "PERMISSION_DENIED" in msg:
            return False, "This key doesn't have access to the Gemini API."
        return False, f"Could not verify the key: {msg[:160]}"
    if names and not any(GEMINI_MODEL in n for n in names):
        return True, f"Key works, but the model {GEMINI_MODEL} isn't listed for it; a fallback model will be used."
    return True, "Key works."


# ---------------------------------------------------------------- lookup (with a short cache)

_cache: dict[int, tuple[float, list[str]]] = {}
_cache_lock = threading.Lock()


def invalidate(user_id: int) -> None:
    with _cache_lock:
        _cache.pop(user_id, None)


def user_keys(user_id: int) -> list[str]:
    """Decrypted keys for a user, default first. Raises NoApiKey if there are none."""
    with _cache_lock:
        hit = _cache.get(user_id)
        if hit and time.monotonic() - hit[0] < 60:
            keys = hit[1]
        else:
            keys = None
    if keys is None:
        with db_session() as db:
            rows = (db.query(ApiKey).filter(ApiKey.user_id == user_id, ApiKey.status != "invalid")
                    .order_by(ApiKey.is_default.desc(), ApiKey.created_at).all())
            keys = [k for k in (decrypt(r.key_encrypted) for r in rows) if k]
        if keys:   # never cache "no keys": a key added via another server process must work at once
            with _cache_lock:
                _cache[user_id] = (time.monotonic(), keys)
    if not keys:
        raise NoApiKey("Add your Gemini API key on the API keys page to use the AI features.")
    return keys


def has_keys(user_id: int) -> bool:
    try:
        return bool(user_keys(user_id))
    except NoApiKey:
        return False


# ---------------------------------------------------------------- management

def list_keys(db, user_id: int) -> list[ApiKey]:
    return (db.query(ApiKey).filter_by(user_id=user_id)
            .order_by(ApiKey.is_default.desc(), ApiKey.created_at).all())


def add_key(db, user_id: int, label: str, key: str) -> tuple[ApiKey | None, str]:
    key = clean(key)
    if not looks_valid(key):
        return None, "That doesn't look like a Gemini API key (they start with \"AIza\")."
    existing = list_keys(db, user_id)
    if len(existing) >= MAX_KEYS:
        return None, f"You can store up to {MAX_KEYS} keys."
    if any(decrypt(k.key_encrypted) == key for k in existing):
        return None, "You've already added this key."
    ok, message = check_key(key)
    if not ok:
        return None, message
    row = ApiKey(user_id=user_id, label=(label.strip() or f"Key {len(existing) + 1}")[:60],
                 key_encrypted=encrypt(key), last4=key[-4:], is_default=not existing, status="ok",
                 last_checked_at=datetime.now(timezone.utc))
    db.add(row)
    db.flush()
    invalidate(user_id)
    return row, message


def update_key(db, row: ApiKey, label: str, new_key: str) -> tuple[bool, str]:
    if label.strip():
        row.label = label.strip()[:60]
    new_key = clean(new_key)
    if new_key:
        if not looks_valid(new_key):
            return False, "That doesn't look like a Gemini API key (they start with \"AIza\")."
        ok, message = check_key(new_key)
        if not ok:
            return False, message
        row.key_encrypted, row.last4 = encrypt(new_key), new_key[-4:]
        row.status, row.last_error, row.last_checked_at = "ok", None, datetime.now(timezone.utc)
        invalidate(row.user_id)
        return True, "Key replaced. " + message
    invalidate(row.user_id)
    return True, "Saved."


def test_key(db, row: ApiKey) -> tuple[bool, str]:
    plain = decrypt(row.key_encrypted)
    ok, message = check_key(plain) if plain else (False, "This key could not be decrypted; please replace it.")
    row.status = "ok" if ok else "invalid"
    row.last_error = None if ok else message[:300]
    row.last_checked_at = datetime.now(timezone.utc)
    invalidate(row.user_id)
    return ok, message


def set_default(db, user_id: int, key_id: int) -> None:
    for k in list_keys(db, user_id):
        k.is_default = k.id == key_id
    invalidate(user_id)


def delete_key(db, row: ApiKey) -> None:
    uid, was_default = row.user_id, row.is_default
    db.delete(row)
    db.flush()
    if was_default:
        rest = list_keys(db, uid)
        if rest:
            rest[0].is_default = True
    invalidate(uid)
