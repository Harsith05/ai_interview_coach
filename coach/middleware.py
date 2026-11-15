"""Logs slow requests, so slow pages show up in the server window with their timing.

Pure ASGI (not BaseHTTPMiddleware, which interferes with streaming responses). Measures the
time until the response *starts*, so long-lived streams (webcam, NDJSON) aren't flagged.
"""
import logging
import threading
import time

log = logging.getLogger("slow-request")


class SlowRequestLogger:
    def __init__(self, app, threshold: float = 2.0):
        self.app = app
        self.threshold = threshold

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        t0 = time.perf_counter()
        logged = False

        async def send_wrapper(message):
            nonlocal logged
            if message["type"] == "http.response.start" and not logged:
                logged = True
                took = time.perf_counter() - t0
                if took > self.threshold:
                    log.warning("%s %s took %.1fs to respond (threads in use: %d)",
                                scope["method"], scope["path"], took, threading.active_count())
            await send(message)

        await self.app(scope, receive, send_wrapper)
