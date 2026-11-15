"""English speaking practice: pages and the conversation API."""
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from langgraph.types import Command
from sqlalchemy.orm import Session

from coach.api.streaming import acquire, run_streaming
from coach.api.account_routes import key_required_redirect
from coach.auth import require_user_api, require_user_page
from coach.config import UPLOAD_DIR
from coach.db import get_db
from coach.models import PracticeSession, User
from coach.practice.graph import practice_graph
from coach.practice.scenarios import LEVELS, MAX_TURNS, SCENARIOS
from coach.services import audio
from coach.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _own(db: Session, session_id: str, user: User) -> PracticeSession:
    ps = db.get(PracticeSession, session_id)
    if ps is None or ps.user_id != user.id:
        raise HTTPException(404, "Practice session not found")
    return ps


def _pending(snapshot) -> dict | None:
    for task in snapshot.tasks:
        for intr in getattr(task, "interrupts", ()) or ():
            return intr.value
    return None


# ---------------------------------------------------------------- pages

@router.get("/practice")
def practice_setup(request: Request, user: User = Depends(require_user_page)):
    return templates.TemplateResponse(request, "practice_setup.html", {
        "user": user, "scenarios": SCENARIOS, "levels": LEVELS, "max_turns": MAX_TURNS})


@router.post("/practice")
def create_practice(scenario: str = Form("casual"), level: str = Form("intermediate"),
                    user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    if (r := key_required_redirect(user)) is not None:
        return r
    if scenario not in SCENARIOS or level not in LEVELS:
        raise HTTPException(400, "Unknown scenario or level")
    ps = PracticeSession(id=uuid.uuid4().hex, user_id=user.id, scenario=scenario, level=level)
    db.add(ps)
    return RedirectResponse(f"/practice/{ps.id}/live", status_code=303)


@router.get("/practice/{session_id}/live")
def practice_page(request: Request, session_id: str, user: User = Depends(require_user_page),
                  db: Session = Depends(get_db)):
    ps = _own(db, session_id, user)
    if ps.status == "completed":
        return RedirectResponse(f"/practice/{ps.id}/report", status_code=303)
    return templates.TemplateResponse(request, "practice.html", {
        "user": user, "ps": ps, "scenario": SCENARIOS[ps.scenario], "level": LEVELS[ps.level],
        "max_turns": MAX_TURNS})


@router.get("/practice/{session_id}/report")
def practice_report(request: Request, session_id: str, user: User = Depends(require_user_page),
                    db: Session = Depends(get_db)):
    ps = _own(db, session_id, user)
    if ps.status != "completed":
        return RedirectResponse(f"/practice/{ps.id}/live", status_code=303)
    return templates.TemplateResponse(request, "practice_report.html", {
        "user": user, "ps": ps, "scenario": SCENARIOS[ps.scenario], "level": LEVELS[ps.level],
        "r": ps.report or {}, "t": ps.transcript or {"messages": [], "turns": []}})


@router.post("/practice/{session_id}/delete")
def delete_practice(session_id: str, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    ps = _own(db, session_id, user)
    try:
        practice_graph.checkpointer.delete_thread(ps.id)
    except Exception:
        log.exception("Could not delete checkpoints for %s", ps.id[:8])
    db.delete(ps)
    return RedirectResponse("/dashboard", status_code=303)


# ---------------------------------------------------------------- API

def _stream(graph_input, session_id: str, emit) -> None:
    config = _config(session_id)
    for mode, chunk in practice_graph.stream(graph_input, config, stream_mode=["updates", "custom"]):
        if mode == "custom":
            emit(chunk)
        elif "__interrupt__" in chunk:
            emit(chunk["__interrupt__"][0].value)
    snap = practice_graph.get_state(config)
    if not snap.next:
        v = snap.values
        msgs = v.get("messages", [])
        emit({"type": "done", "report": v.get("report", {}), "turns": v.get("turns", []),
              # closing line when the turn limit was reached
              "final_ai_text": msgs[-1]["text"] if msgs and msgs[-1]["role"] == "ai" and v.get("user_turns", 0) >= MAX_TURNS else None,
              "report_url": f"/practice/{session_id}/report"})


@router.post("/api/practice/{session_id}/start")
def start_practice(session_id: str, user: User = Depends(require_user_api), db: Session = Depends(get_db)):
    ps = _own(db, session_id, user)
    if ps.status != "in_progress":
        raise HTTPException(409, "This session has already finished.")
    lock = acquire(session_id)
    initial = {"session_id": ps.id, "scenario": ps.scenario, "level": ps.level}

    def work(emit):
        snap = practice_graph.get_state(_config(session_id))
        if snap.values:  # resuming (page reload): send the history, then the open AI line
            emit({"type": "history", "messages": snap.values.get("messages", [])[:-1],
                  "turns": snap.values.get("turns", [])})
            q = _pending(snap)
            emit(q if q else {"type": "error", "message": "Session is not waiting for you to speak."})
            return
        _stream(initial, session_id, emit)

    return run_streaming(session_id, lock, work, user.id)


def _transcribe_turn(webm_path: Path | None, live: str) -> dict:
    """Whisper transcript + fluency for one learner turn (falls back to the browser transcript)."""
    out = {"text": "", "fluency": {}, "audio_status": "no audio"}
    wav = None
    try:
        if webm_path is not None:
            wav = webm_path.with_suffix(".wav")
            duration, peak = audio.webm_to_wav(webm_path.read_bytes(), wav)
            if peak < audio.SILENCE_DBFS:
                out["audio_status"] = "silent"
            else:
                r = audio.transcribe(wav)
                out["text"] = r["text"]
                if r["words"]:
                    out["fluency"] = audio.fluency(r["words"], duration)
                out["audio_status"] = "ok"
    except Exception:
        log.exception("Practice transcription failed")
    finally:
        for p in (webm_path, wav):
            if p is not None:
                p.unlink(missing_ok=True)
    if not out["text"] and live.strip():
        out["text"] = live.strip()
    return out


@router.post("/api/practice/{session_id}/turn")
async def practice_turn(
    session_id: str,
    audio_file: UploadFile | None = File(None),
    live_transcript: str = Form(""),
    end: bool = Form(False),
    user: User = Depends(require_user_api),
    db: Session = Depends(get_db),
):
    await run_in_threadpool(_own, db, session_id, user)
    lock = acquire(session_id)
    try:
        snap = await run_in_threadpool(practice_graph.get_state, _config(session_id))
        if not snap.next or _pending(snap) is None:
            raise HTTPException(409, "The session is not waiting for you to speak.")
        webm_path = None
        if audio_file is not None and not end:
            data = await audio_file.read()
            if data:
                webm_path = UPLOAD_DIR / f"practice_{session_id}_{uuid.uuid4().hex[:6]}.webm"
                webm_path.write_bytes(data)
    except BaseException:
        lock.release()
        raise

    def work(emit):
        if end:
            emit({"type": "status", "text": "Wrapping up…"})
            _stream(Command(resume={"end": True}), session_id, emit)
            return
        heard = _transcribe_turn(webm_path, live_transcript)
        emit({"type": "heard", "text": heard["text"], "audio_status": heard["audio_status"],
              "fluency": heard["fluency"]})
        _stream(Command(resume=heard), session_id, emit)

    return run_streaming(session_id, lock, work, user.id)
