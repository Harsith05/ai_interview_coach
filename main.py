"""AI Interview Coach — FastAPI + LangGraph + Postgres.

Run with:
    docker compose up -d          # Postgres
    uvicorn main:app --reload
"""
# Imported here, in the main thread, on purpose: it registers an interpreter-exit hook, which
# Python refuses once shutdown has begun. Otherwise the model warm-up thread can import it (via
# joblib/loky) during a Ctrl+C or --reload shutdown and fail with "can't register atexit after shutdown".
import concurrent.futures.process  # noqa: F401
import logging
import os
import threading
import warnings
from contextlib import asynccontextmanager
from urllib.parse import quote

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
warnings.filterwarnings("ignore", category=FutureWarning)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from coach.api import (account_routes, auth_routes, dashboard_routes, drill_routes,  # noqa: E402
                       feedback_routes, practice_routes, staff_routes, talk_routes)
from coach.admin import setup_admin  # noqa: E402
from coach.api.routes import api, pages  # noqa: E402
from coach.auth import LoginRequired  # noqa: E402
from coach.config import BASE_DIR, SECRET_KEY, SESSION_HTTPS_ONLY  # noqa: E402
from coach.db import checkpoint_pool, init_db  # noqa: E402
from coach.errors import register_error_handlers  # noqa: E402
from coach.middleware import SlowRequestLogger  # noqa: E402
from coach.services.vision import camera  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)
# The Gemini SDK logs every call ("AFC is enabled…") and every automatic retry at INFO.
# Retries are expected (frequent 503 "high demand"); real failures are still logged by our code.
logging.getLogger("google_genai").setLevel(logging.WARNING)


def _warm_up_librosa(audio):
    """librosa JIT-compiles its pitch tracker on first use (~5 s); do that now, not after an answer."""
    import tempfile
    import wave
    from pathlib import Path

    import numpy as np

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "warmup.wav"
        tone = (np.sin(np.linspace(0, 2 * np.pi * 220, 16000)) * 8000).astype(np.int16)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(tone.tobytes())
        audio.tone(p)


_shutting_down = threading.Event()


def _warm_up_models():
    """Load the heavy models once at startup instead of during the first question.

    Stops between models if the server is shutting down, so a quick Ctrl+C or --reload
    restart doesn't keep loading models (or report errors) in a process that is exiting.
    """
    log = logging.getLogger("warmup")
    try:
        from deepface import DeepFace

        from coach.services import audio, scoring, vision

        steps = (scoring._model, vision._face_mesh,
                 lambda: DeepFace.build_model(model_name="Emotion", task="facial_attribute"),
                 audio._whisper, lambda: _warm_up_librosa(audio))
        for step in steps:
            if _shutting_down.is_set():
                return
            step()
        log.info("Models loaded")
    except Exception:
        if _shutting_down.is_set():
            log.info("Model warm-up stopped: server is shutting down")
        else:
            log.exception("Model warm-up failed (they will load on first use)")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    threading.Thread(target=_warm_up_models, daemon=True).start()
    yield
    _shutting_down.set()
    camera.release()
    checkpoint_pool.close()


app = FastAPI(title="AI Interview Coach", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="aic_session",
                   max_age=14 * 24 * 3600, same_site="lax", https_only=SESSION_HTTPS_ONLY)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(auth_routes.router)
app.include_router(account_routes.router)
app.include_router(pages)
app.include_router(dashboard_routes.router)
app.include_router(practice_routes.router)
app.include_router(talk_routes.router)
app.include_router(drill_routes.router)
app.include_router(api)
app.include_router(staff_routes.router)
app.include_router(feedback_routes.router)
setup_admin(app)   # Django-admin-style panel at /admin (admins only)
app.add_middleware(SlowRequestLogger, threshold=2.0)
register_error_handlers(app)   # friendly 404/500/503 pages, offline page + service worker, /healthz


@app.exception_handler(LoginRequired)
async def _login_redirect(_: Request, exc: LoginRequired):
    return RedirectResponse(f"/login?next={quote(exc.next_url)}", status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
