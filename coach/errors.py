"""Friendly error pages.

- Browser page requests (Accept: text/html) get the styled ``error.html`` page.
- API / fetch() requests keep getting JSON ``{"detail": ...}`` so the existing JS keeps working.
- A database outage becomes a 503 "temporarily unavailable" page (with Retry-After) instead of a crash.
- ``/offline`` + ``/sw.js``: a service worker caches the offline page and shows it when the
  server can't be reached at all (a stopped server can't send an error page itself).
- ``/healthz`` lets the offline page and the in-page banner detect when the server is back.
"""
import logging
import uuid

import psycopg
import psycopg_pool
import sqlalchemy.exc
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from coach.db import engine
from coach.templating import STATIC_DIR, templates

log = logging.getLogger(__name__)

DB_RETRY_AFTER = "30"

PAGES = {
    "not_found": dict(title="We couldn't find that page",
                      message="The page you're looking for doesn't exist, was moved, or the link is out of date.",
                      tone=""),
    "forbidden": dict(title="You don't have access to this page",
                      message="This page belongs to another account or needs different permissions.",
                      tone="warn"),
    "unavailable": dict(title="We're temporarily unavailable",
                        message="The coach can't reach its database right now. Your saved progress is safe.",
                        hint="This usually fixes itself within a minute. We'll retry for you.",
                        tone="warn", retry=True),
    "offline": dict(title="Can't reach the server",
                    message="The AI Interview Coach server isn't responding. It may be restarting, down for "
                            "maintenance, or your internet connection may have dropped.",
                    hint="This page will reload by itself as soon as the server is back.",
                    tone="warn", retry=True),
    "server_error": dict(title="Something went wrong on our side",
                         message="An unexpected error stopped this page from loading. It's not something you did.",
                         hint="Try again in a moment. If it keeps happening, quote the reference below.",
                         tone="danger", retry=True),
    "generic": dict(title="Something went wrong",
                    message="We couldn't complete that request.",
                    tone=""),
}


def _wants_html(request: Request) -> bool:
    return not request.url.path.startswith("/api") and "text/html" in request.headers.get("accept", "")


def render_error(request: Request, kind: str, status_code: int | None, *, message: str | None = None,
                 ref: str | None = None, headers: dict | None = None):
    ctx = {"kind": kind, "status_code": status_code, "ref": ref, "hint": None, "retry": False,
           **PAGES.get(kind, PAGES["generic"])}
    if message:
        ctx["message"] = message
    return templates.TemplateResponse(request, "error.html", ctx, status_code=status_code or 200, headers=headers)


async def _http_error(request: Request, exc: StarletteHTTPException):
    if not _wants_html(request):
        return await http_exception_handler(request, exc)
    kind = {404: "not_found", 403: "forbidden", 503: "unavailable"}.get(exc.status_code)
    if kind is None:
        kind = "server_error" if exc.status_code >= 500 else "generic"
    # For 4xx the detail is written for users (e.g. "Interview not found"); 5xx details may be internal.
    custom = exc.detail if isinstance(exc.detail, str) and exc.status_code < 500 and kind == "generic" else None
    return render_error(request, kind, exc.status_code, message=custom, headers=getattr(exc, "headers", None))


async def _db_unavailable(request: Request, exc: Exception):
    log.error("Database unavailable on %s %s: %s", request.method, request.url.path, exc)
    headers = {"Retry-After": DB_RETRY_AFTER}
    if not _wants_html(request):
        return JSONResponse({"detail": "The database is temporarily unavailable. Please try again shortly."},
                            status_code=503, headers=headers)
    return render_error(request, "unavailable", 503, headers=headers)


async def _unhandled(request: Request, exc: Exception):
    ref = uuid.uuid4().hex[:8]
    # Starlette re-raises after this handler, so the server log prints the full traceback right after.
    log.error("Unhandled error [ref %s] on %s %s: %r", ref, request.method, request.url.path, exc)
    if not _wants_html(request):
        return JSONResponse({"detail": f"Internal server error (ref {ref})."}, status_code=500)
    return render_error(request, "server_error", 500, ref=ref)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_error)
    for exc_type in (sqlalchemy.exc.OperationalError, sqlalchemy.exc.InterfaceError,
                     psycopg.OperationalError, psycopg_pool.PoolTimeout):
        app.add_exception_handler(exc_type, _db_unavailable)
    app.add_exception_handler(Exception, _unhandled)

    @app.get("/offline", include_in_schema=False)
    def offline(request: Request):
        """Cached by the service worker; shown when a page can't be loaded because the server is down."""
        return render_error(request, "offline", None, headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        # Served from the root (not /static) so the worker's scope covers the whole site.
        return FileResponse(STATIC_DIR / "js" / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "degraded", "database": "down"}, status_code=503)
        return {"status": "ok"}
