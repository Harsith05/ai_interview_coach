"""Gemini chat models via LangChain, using the *current user's* own API keys.

Which user? Request handlers wrap their work in `as_user(user_id)` (a context variable, which
LangGraph copies into the threads that run graph nodes). Every call builds a fallback chain over
all of the user's keys and both models:

    key 1 / main model → key 1 / fallback model → key 2 / main model → key 2 / fallback → …

so Gemini's "503 high demand" errors fall back to the lighter model, and a key that hits its
quota (429) falls back to the user's next key.
"""
import contextvars
from contextlib import contextmanager
from functools import lru_cache

import nh3

from coach.apikeys import NoApiKey, user_keys
from coach.config import GEMINI_FALLBACK_MODEL, GEMINI_MODEL

_current_user: contextvars.ContextVar[int | None] = contextvars.ContextVar("llm_user", default=None)


@contextmanager
def as_user(user_id: int):
    """Run AI calls in this block with `user_id`'s API keys."""
    token = _current_user.set(user_id)
    try:
        yield
    finally:
        _current_user.reset(token)


def _user_id() -> int:
    uid = _current_user.get()
    if uid is None:   # inside a graph run started with configurable.user_id
        try:
            from langgraph.config import get_config

            uid = get_config().get("configurable", {}).get("user_id")
        except Exception:
            uid = None
    if uid is None:
        raise NoApiKey("No user context for the AI call.")
    return uid


# Speed profiles: (main model kwargs, fallback model kwargs)
_PROFILES = {
    # Final report and grading: best quality.
    "default": (dict(temperature=0.4, max_retries=2), dict(temperature=0.4, max_retries=2)),
    # User is waiting (question planning, follow-ups): no "thinking" on the main model. The lite
    # fallback rejects thinking_budget=0 (400 INVALID_ARGUMENT) and is fast anyway.
    "fast": (dict(temperature=0.6, thinking_budget=0, max_retries=1, timeout=30),
             dict(temperature=0.6, max_retries=1, timeout=30)),
    # Live conversation: give up on a slow main model quickly (it can hang ~30 s before a 503).
    "realtime": (dict(temperature=0.7, thinking_budget=0, max_retries=0, timeout=8),
                 dict(temperature=0.7, max_retries=1, timeout=20)),
}


@lru_cache(maxsize=64)
def _model(api_key: str, model: str, speed: str, fallback: bool):
    from langchain_google_genai import ChatGoogleGenerativeAI

    kwargs = _PROFILES[speed][1 if fallback else 0]
    return ChatGoogleGenerativeAI(model=model, api_key=api_key, **kwargs)


def _chain(speed: str, schema=None):
    runnables = []
    for key in user_keys(_user_id()):
        for model, is_fallback in ((GEMINI_MODEL, False), (GEMINI_FALLBACK_MODEL, True)):
            m = _model(key, model, speed, is_fallback)
            runnables.append(m.with_structured_output(schema) if schema is not None else m)
    first, rest = runnables[0], runnables[1:]
    return first.with_fallbacks(rest) if rest else first


def get_llm():
    return _chain("default")


def get_fast_llm():
    return _chain("fast")


def structured(schema, fast: bool = False, speed: str | None = None):
    """Chat model returning `schema` (a Pydantic model), with key + model fallback.

    speed: "default" (best quality), "fast" (no thinking) or "realtime" (live conversation).
    """
    return _chain(speed or ("fast" if fast else "default"), schema)


ALLOWED_TAGS = {"h3", "h4", "h5", "p", "ul", "ol", "li", "strong", "em", "b", "i", "br", "hr",
                "table", "thead", "tbody", "tr", "th", "td", "span", "div", "blockquote", "code"}


def clean_html(html: str) -> str:
    """LLM output is rendered as HTML: strip everything except basic formatting tags."""
    html = html.strip()
    if html.startswith("```"):  # the model sometimes wraps HTML in a code fence
        html = html.strip("`").removeprefix("html").strip()
    return nh3.clean(html, tags=ALLOWED_TAGS, attributes={})
