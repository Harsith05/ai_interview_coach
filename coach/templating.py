from fastapi.templating import Jinja2Templates

from coach.config import BASE_DIR, MODES, ROLE_LABELS

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update(ROLE_LABELS=ROLE_LABELS, MODES=MODES)

STATIC_DIR = BASE_DIR / "static"


def static_url(path: str) -> str:
    """URL of a static file with a version stamp (its modification time).

    Browsers cache scripts aggressively; the stamp changes whenever the file changes, so an
    updated interview.js is always fetched instead of an old cached copy.
    """
    try:
        version = int((STATIC_DIR / path).stat().st_mtime)
    except OSError:
        version = 0
    return f"/static/{path}?v={version}"


def _fmt_dt(value) -> str:
    # Stored in UTC; show in the server's local timezone.
    return value.astimezone().strftime("%d %b %Y, %H:%M") if value else ""


def has_api_key(user) -> bool:
    from coach.apikeys import has_keys

    return bool(user) and has_keys(user.id)


templates.env.globals["static_url"] = static_url
templates.env.globals["has_api_key"] = has_api_key
templates.env.filters["dt"] = _fmt_dt
