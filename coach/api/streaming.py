"""Shared helpers for endpoints that advance a LangGraph session and stream NDJSON events."""
import json
import logging
import queue
import threading
from collections import defaultdict

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

log = logging.getLogger(__name__)

# One lock per session (interview or practice) so a double-click can't resume a graph twice.
_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def acquire(session_id: str) -> threading.Lock:
    lock = _locks[session_id]
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "This session is busy processing your last action.")
    return lock


def _event(obj: dict) -> str:
    return json.dumps(obj, default=str) + "\n"


def run_streaming(session_id: str, lock: threading.Lock, work, user_id: int | None = None) -> StreamingResponse:
    """Run `work(emit)` on its own thread and stream its events as NDJSON.

    The graph keeps running even if the browser disconnects mid-stream, so a closed tab never
    leaves a session half-advanced. `lock` is released when the work ends. AI calls inside use
    `user_id`'s own Gemini API keys.
    """
    from coach.apikeys import NoApiKey
    from coach.services.llm import as_user

    q: queue.Queue = queue.Queue()
    done = object()

    def worker():
        try:
            if user_id is not None:
                with as_user(user_id):
                    work(q.put)
            else:
                work(q.put)
        except NoApiKey as e:
            q.put({"type": "error", "message": str(e), "need_api_key": True})
        except Exception as e:
            log.exception("Session %s failed", session_id[:8])
            q.put({"type": "error", "message": str(e)})
        finally:
            lock.release()
            q.put(done)

    threading.Thread(target=worker, daemon=True, name=f"session-{session_id[:8]}").start()

    def gen():
        while True:
            item = q.get()
            if item is done:
                return
            yield _event(item)

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
