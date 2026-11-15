"""Long-term memory for the Talk companion, stored in LangGraph's PostgresStore.

Namespaces (per user):
  ("memories", uid)  facts about the user          {"fact", "created_at"}
  ("mistakes", uid)  recurring English mistakes    {"type", "count", "examples": [...], "last_seen"}
  ("sessions", uid)  past conversation summaries   {"summary", "topics", "date"}
  ("settings", uid)  key "assistant": {"name", "level", "corrections"}
"""
import re
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from coach.db import checkpoint_pool

DEFAULT_SETTINGS = {"name": "Jarvis", "level": "intermediate", "corrections": True}
MAX_FACTS = 60


@lru_cache(maxsize=1)
def get_store():
    from langgraph.store.postgres import PostgresStore

    return PostgresStore(checkpoint_pool)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ns(kind: str, user_id: int) -> tuple[str, str]:
    return (kind, str(user_id))


def _all(store, kind: str, user_id: int, limit: int = 200):
    return store.search(_ns(kind, user_id), limit=limit)


# ---------------------------------------------------------------- settings

def get_settings(user_id: int, store=None) -> dict:
    store = store or get_store()
    item = store.get(_ns("settings", user_id), "assistant")
    return {**DEFAULT_SETTINGS, **(item.value if item else {})}


def save_settings(user_id: int, **values) -> dict:
    store = get_store()
    settings = {**get_settings(user_id, store), **values}
    store.put(_ns("settings", user_id), "assistant", settings)
    return settings


# ---------------------------------------------------------------- writing

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def add_facts(user_id: int, facts: list[str], store=None) -> None:
    store = store or get_store()
    existing = {_norm(i.value.get("fact", "")) for i in _all(store, "memories", user_id)}
    for fact in facts:
        fact = (fact or "").strip()
        if len(fact) < 4 or _norm(fact) in existing:
            continue
        store.put(_ns("memories", user_id), uuid.uuid4().hex, {"fact": fact, "created_at": _now()})
        existing.add(_norm(fact))
    # Keep memory bounded: drop the oldest facts.
    items = sorted(_all(store, "memories", user_id), key=lambda i: i.value.get("created_at", ""))
    for old in items[:-MAX_FACTS]:
        store.delete(old.namespace, old.key)


def record_mistake(user_id: int, mistake_type: str, original: str, corrected: str, store=None) -> None:
    store = store or get_store()
    key = re.sub(r"[^a-z0-9]+", "-", (mistake_type or "other").lower()).strip("-")[:60] or "other"
    ns = _ns("mistakes", user_id)
    item = store.get(ns, key)
    value = item.value if item else {"type": mistake_type or "other", "count": 0, "examples": []}
    value["count"] += 1
    value["examples"] = ([{"original": original, "corrected": corrected}] + value["examples"])[:3]
    value["last_seen"] = _now()
    store.put(ns, key, value)


def add_session_summary(user_id: int, session_id: str, summary: str, topics: list[str], store=None) -> None:
    store = store or get_store()
    store.put(_ns("sessions", user_id), session_id,
              {"summary": summary, "topics": topics, "date": _now()})


# ---------------------------------------------------------------- reading

def load_context(user_id: int, store=None) -> dict:
    """Everything the companion should know about the user, for its system prompt."""
    store = store or get_store()
    facts = sorted(_all(store, "memories", user_id), key=lambda i: i.value.get("created_at", ""))
    mistakes = sorted(_all(store, "mistakes", user_id), key=lambda i: -i.value.get("count", 0))
    sessions = sorted(_all(store, "sessions", user_id), key=lambda i: i.value.get("date", ""), reverse=True)
    return {
        "facts": [i.value["fact"] for i in facts][-40:],
        "mistakes": [{"type": i.value["type"], "count": i.value["count"],
                      "example": (i.value.get("examples") or [{}])[0]} for i in mistakes[:5]],
        "recent_sessions": [{"date": i.value["date"][:10], "summary": i.value["summary"]} for i in sessions[:3]],
        "settings": get_settings(user_id, store),
    }


def context_text(ctx: dict) -> str:
    parts = []
    if ctx["facts"]:
        parts.append("What you remember about the user:\n" + "\n".join(f"- {f}" for f in ctx["facts"]))
    if ctx["recent_sessions"]:
        parts.append("Your recent conversations with them (newest first):\n"
                     + "\n".join(f"- {s['date']}: {s['summary']}" for s in ctx["recent_sessions"]))
    if ctx["mistakes"]:
        parts.append("English mistakes they make repeatedly (help them practice these naturally):\n"
                     + "\n".join(f"- {m['type']} ({m['count']}×), e.g. \"{m['example'].get('original', '')}\" → "
                                 f"\"{m['example'].get('corrected', '')}\"" for m in ctx["mistakes"]))
    return "\n\n".join(parts) or "You don't know anything about the user yet. Get to know them naturally."


def list_all(user_id: int) -> dict:
    store = get_store()
    return {
        "facts": sorted(_all(store, "memories", user_id), key=lambda i: i.value.get("created_at", ""), reverse=True),
        "mistakes": sorted(_all(store, "mistakes", user_id), key=lambda i: -i.value.get("count", 0)),
        "sessions": sorted(_all(store, "sessions", user_id), key=lambda i: i.value.get("date", ""), reverse=True),
    }


# ---------------------------------------------------------------- deleting

def delete_item(user_id: int, kind: str, key: str) -> None:
    if kind in ("memories", "mistakes", "sessions"):
        get_store().delete(_ns(kind, user_id), key)


def forget_everything(user_id: int) -> None:
    store = get_store()
    for kind in ("memories", "mistakes", "sessions"):
        for item in _all(store, kind, user_id, limit=1000):
            store.delete(item.namespace, item.key)
