"""Talk companion (daily voice chat with memory): pages, API and the memory page."""
import difflib
import logging
import re
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from langgraph.types import Command
from sqlalchemy.orm import Session

from coach import memory
from coach.api.practice_routes import _transcribe_turn
from coach.api.streaming import acquire, run_streaming
from coach.api.account_routes import key_required_redirect
from coach.services.llm import as_user
from coach.auth import require_user_api, require_user_page
from coach.config import UPLOAD_DIR
from coach.db import get_db
from coach.models import TalkSession, User
from coach.practice.scenarios import LEVELS
from coach.talk.graph import summarize_session, talk_graph
from coach.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _own(db: Session, session_id: str, user: User) -> TalkSession:
    ts = db.get(TalkSession, session_id)
    if ts is None or ts.user_id != user.id:
        raise HTTPException(404, "Conversation not found")
    return ts


def _pending(snapshot) -> dict | None:
    for task in snapshot.tasks:
        for intr in getattr(task, "interrupts", ()) or ():
            return intr.value
    return None


def _close_in_background(sessions: list[TalkSession]) -> None:
    """Summarize earlier unfinished sessions into memory (they were left open when the tab closed)."""
    def run():
        for ts in sessions:
            try:
                msgs = (talk_graph.get_state(_config(ts.id)).values or {}).get("messages", [])
                with as_user(ts.user_id):
                    summarize_session(ts.id, ts.user_id, msgs)
            except Exception:
                log.exception("Could not close talk session %s", ts.id[:8])
    if sessions:
        threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- wake word

def _norm_words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def strip_wake_word(text: str, name: str) -> str | None:
    """If `text` contains the assistant's name (fuzzy, Whisper often misspells names), return the
    rest of the sentence (may be empty); otherwise None."""
    words, target = _norm_words(text), name.lower()
    for i, w in enumerate(words):
        if difflib.SequenceMatcher(None, w, target).ratio() >= 0.7:
            return " ".join(words[i + 1:])
    return None


# ---------------------------------------------------------------- pages

@router.get("/talk")
def talk_page(request: Request, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    if (r := key_required_redirect(user)) is not None:
        return r
    open_sessions = (db.query(TalkSession).filter_by(user_id=user.id, status="open")
                     .order_by(TalkSession.last_active_at.desc()).all())
    today = datetime.now().date()
    current = next((s for s in open_sessions if s.last_active_at.astimezone().date() == today), None)
    stale = [s for s in open_sessions if s is not current]
    for s in stale:
        s.status = "closing"
    _close_in_background(stale)
    if current is None:
        current = TalkSession(id=uuid.uuid4().hex, user_id=user.id)
        db.add(current)
        db.flush()
    settings = memory.get_settings(user.id)
    ctx = memory.load_context(user.id)
    return templates.TemplateResponse(request, "talk.html", {
        "user": user, "ts": current, "settings": settings, "level": LEVELS[settings["level"]],
        "memory_count": len(ctx["facts"]), "past_sessions": len(ctx["recent_sessions"]),
    })


@router.post("/talk/new")
def talk_new(user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    open_sessions = db.query(TalkSession).filter_by(user_id=user.id, status="open").all()
    for s in open_sessions:
        s.status = "closing"
    _close_in_background(open_sessions)
    db.add(TalkSession(id=uuid.uuid4().hex, user_id=user.id))
    return RedirectResponse("/talk", status_code=303)


@router.get("/memory")
def memory_page(request: Request, user: User = Depends(require_user_page)):
    return templates.TemplateResponse(request, "memory.html", {
        "user": user, "m": memory.list_all(user.id), "settings": memory.get_settings(user.id), "levels": LEVELS})


@router.post("/memory/settings")
def memory_settings(name: str = Form("Jarvis"), level: str = Form("intermediate"), corrections: bool = Form(False),
                    user: User = Depends(require_user_page)):
    name = re.sub(r"[^A-Za-z ]", "", name).strip()[:20] or "Jarvis"
    memory.save_settings(user.id, name=name, level=level if level in LEVELS else "intermediate",
                         corrections=corrections)
    return RedirectResponse("/memory?saved=1", status_code=303)


@router.post("/memory/delete")
def memory_delete(kind: str = Form(...), key: str = Form(...), user: User = Depends(require_user_page)):
    memory.delete_item(user.id, kind, key)
    return RedirectResponse("/memory", status_code=303)


@router.post("/memory/forget")
def memory_forget(user: User = Depends(require_user_page)):
    memory.forget_everything(user.id)
    return RedirectResponse("/memory", status_code=303)


# ---------------------------------------------------------------- API

def _stream(graph_input, session_id: str, emit) -> None:
    config = _config(session_id)
    for mode, chunk in talk_graph.stream(graph_input, config, stream_mode=["updates", "custom"]):
        if mode == "custom":
            emit(chunk)
        elif "__interrupt__" in chunk:
            emit(chunk["__interrupt__"][0].value)
    snap = talk_graph.get_state(config)
    if not snap.next:
        msgs = snap.values.get("messages", [])
        emit({"type": "done",
              "final_ai_text": msgs[-1]["text"] if snap.values.get("goodbye") and msgs and msgs[-1]["role"] == "ai" else None})


@router.post("/api/talk/{session_id}/start")
def talk_start(session_id: str, user: User = Depends(require_user_api), db: Session = Depends(get_db)):
    ts = _own(db, session_id, user)
    if ts.status != "open":
        raise HTTPException(409, "This conversation has ended. Start a new one.")
    lock = acquire(session_id)

    def work(emit):
        snap = talk_graph.get_state(_config(session_id))
        if snap.values:  # reload: send the history, then the open line
            emit({"type": "history", "messages": snap.values.get("messages", [])[:-1],
                  "turns": snap.values.get("turns", [])})
            q = _pending(snap)
            emit(q if q else {"type": "error", "message": "Conversation is not waiting for you."})
            return
        emit({"type": "status", "text": "Remembering our past chats…"})
        _stream({"session_id": session_id, "user_id": user.id}, session_id, emit)

    return run_streaming(session_id, lock, work, user.id)


@router.post("/api/talk/{session_id}/turn")
async def talk_turn(
    session_id: str,
    audio_file: UploadFile | None = File(None),
    live_transcript: str = Form(""),
    end: bool = Form(False),
    standby: bool = Form(False),
    user: User = Depends(require_user_api),
    db: Session = Depends(get_db),
):
    await run_in_threadpool(_own, db, session_id, user)
    lock = acquire(session_id)
    try:
        snap = await run_in_threadpool(talk_graph.get_state, _config(session_id))
        if not snap.next or _pending(snap) is None:
            raise HTTPException(409, "The conversation is not waiting for you to speak.")
        webm_path = None
        if audio_file is not None and not end:
            data = await audio_file.read()
            if data:
                webm_path = UPLOAD_DIR / f"talk_{session_id}_{uuid.uuid4().hex[:6]}.webm"
                webm_path.write_bytes(data)
        name = memory.get_settings(user.id)["name"]
    except BaseException:
        lock.release()
        raise

    def work(emit):
        if end:
            emit({"type": "status", "text": "Saving what I learned today…"})
            _stream(Command(resume={"end": True}), session_id, emit)
            return
        heard = _transcribe_turn(webm_path, live_transcript)
        if standby:
            # Standby: only react when addressed by name ("Hey Jarvis, …").
            rest = strip_wake_word(heard["text"], name)
            if rest is None:
                emit({"type": "ignored", "text": heard["text"]})
                return
            heard["text"] = rest if len(rest.split()) >= 2 else f"Hey {name}"
            emit({"type": "woke"})
        emit({"type": "heard", "text": heard["text"], "fluency": heard["fluency"]})
        _stream(Command(resume=heard), session_id, emit)

    return run_streaming(session_id, lock, work, user.id)
