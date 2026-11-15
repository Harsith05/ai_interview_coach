"""Question practice: manage your own question sets and let the AI ask them by voice."""
import logging
import random
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from langgraph.types import Command
from sqlalchemy.orm import Session

from coach.api.practice_routes import _transcribe_turn
from coach.api.streaming import acquire, run_streaming
from coach.api.account_routes import key_required_redirect
from coach.auth import require_user_api, require_user_page
from coach.config import UPLOAD_DIR
from coach.db import get_db
from coach.drill.graph import drill_graph
from coach.drill.questions import parse_text, parse_upload, to_text
from coach.models import DrillSession, QuestionSet, User
from coach.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _config(sid: str) -> dict:
    return {"configurable": {"thread_id": sid}}


def _own_set(db: Session, set_id: int, user: User) -> QuestionSet:
    qs = db.get(QuestionSet, set_id)
    if qs is None or qs.user_id != user.id:
        raise HTTPException(404, "Question set not found")
    return qs


def _own_drill(db: Session, sid: str, user: User) -> DrillSession:
    ds = db.get(DrillSession, sid)
    if ds is None or ds.user_id != user.id:
        raise HTTPException(404, "Practice session not found")
    return ds


def _pending(snapshot) -> dict | None:
    for task in snapshot.tasks:
        for intr in getattr(task, "interrupts", ()) or ():
            return intr.value
    return None


async def _items_from_form(text: str, upload: UploadFile | None) -> list[dict]:
    items = parse_text(text)
    if upload is not None and upload.filename:
        data = await upload.read()
        if len(data) > 1_000_000:
            raise ValueError("The file is too large (max 1 MB).")
        items += parse_upload(upload.filename, data)
    return items[:100]


# ---------------------------------------------------------------- question sets

@router.get("/questions")
def sets_page(request: Request, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    sets = db.query(QuestionSet).filter_by(user_id=user.id).order_by(QuestionSet.updated_at.desc()).all()
    drills = (db.query(DrillSession).filter_by(user_id=user.id)
              .order_by(DrillSession.created_at.desc()).limit(15).all())
    return templates.TemplateResponse(request, "questions.html", {
        "user": user, "sets": sets, "drills": drills, "error": None, "form": {}})


@router.post("/questions")
async def create_set(request: Request, title: str = Form(""), questions: str = Form(""),
                     upload: UploadFile | None = File(None),
                     user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    try:
        items = await _items_from_form(questions, upload)
    except ValueError as e:
        items, err = [], str(e)
    else:
        err = None if items else "Add at least one question (one per line), or upload a .txt/.csv file."
    if err:
        sets = db.query(QuestionSet).filter_by(user_id=user.id).order_by(QuestionSet.updated_at.desc()).all()
        drills = db.query(DrillSession).filter_by(user_id=user.id).order_by(DrillSession.created_at.desc()).limit(15).all()
        return templates.TemplateResponse(request, "questions.html", {
            "user": user, "sets": sets, "drills": drills, "error": err,
            "form": {"title": title, "questions": questions}}, status_code=400)
    qs = QuestionSet(user_id=user.id, title=(title.strip() or f"My questions ({len(items)})")[:120], items=items)
    db.add(qs)
    db.flush()
    return RedirectResponse(f"/questions/{qs.id}", status_code=303)


@router.get("/questions/{set_id}")
def set_page(request: Request, set_id: int, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    qs = _own_set(db, set_id, user)
    return templates.TemplateResponse(request, "question_set.html", {
        "user": user, "qs": qs, "text": to_text(qs.items), "saved": request.query_params.get("saved")})


@router.post("/questions/{set_id}")
async def update_set(set_id: int, title: str = Form(""), questions: str = Form(""),
                     upload: UploadFile | None = File(None),
                     user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    qs = _own_set(db, set_id, user)
    try:
        # A file uploaded here replaces the set; otherwise the edited text does.
        has_file = upload is not None and bool(upload.filename)
        items = await _items_from_form("" if has_file else questions, upload if has_file else None)
    except ValueError:
        items = []
    if items:
        qs.items = items
    qs.title = (title.strip() or qs.title)[:120]
    qs.updated_at = datetime.now(timezone.utc)
    return RedirectResponse(f"/questions/{qs.id}?saved=1", status_code=303)


@router.post("/questions/{set_id}/delete")
def delete_set(set_id: int, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    db.delete(_own_set(db, set_id, user))
    return RedirectResponse("/questions", status_code=303)


@router.post("/questions/{set_id}/start")
def start_drill(set_id: int, order: str = Form("in_order"), count: int = Form(0),
                feedback_mode: str = Form("each"),
                user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    qs = _own_set(db, set_id, user)
    if (r := key_required_redirect(user)) is not None:
        return r
    items = list(qs.items)

    if order == "shuffle":
        random.shuffle(items)
    if count and count > 0:
        items = items[:count]
    ds = DrillSession(id=uuid.uuid4().hex, user_id=user.id, set_id=qs.id, title=qs.title, items=items,
                      feedback_mode="end" if feedback_mode == "end" else "each")
    db.add(ds)
    return RedirectResponse(f"/drill/{ds.id}/live", status_code=303)


# ---------------------------------------------------------------- live session pages

@router.get("/drill/{sid}/live")
def drill_page(request: Request, sid: str, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    ds = _own_drill(db, sid, user)
    if ds.status == "completed":
        return RedirectResponse(f"/drill/{ds.id}/report", status_code=303)
    return templates.TemplateResponse(request, "drill.html", {"user": user, "ds": ds})


@router.get("/drill/{sid}/report")
def drill_report(request: Request, sid: str, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    ds = _own_drill(db, sid, user)
    if ds.status != "completed":
        return RedirectResponse(f"/drill/{ds.id}/live", status_code=303)
    return templates.TemplateResponse(request, "drill_report.html", {
        "user": user, "ds": ds, "r": ds.report or {}, "results": ds.results or []})


@router.post("/drill/{sid}/delete")
def delete_drill(sid: str, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    ds = _own_drill(db, sid, user)
    try:
        drill_graph.checkpointer.delete_thread(ds.id)
    except Exception:
        log.exception("Could not delete checkpoints for %s", ds.id[:8])
    db.delete(ds)
    return RedirectResponse("/questions", status_code=303)


# ---------------------------------------------------------------- API

def _stream(graph_input, sid: str, emit) -> None:
    config = _config(sid)
    for mode, chunk in drill_graph.stream(graph_input, config, stream_mode=["updates", "custom"]):
        if mode == "custom":
            emit(chunk)
        elif "__interrupt__" in chunk:
            emit(chunk["__interrupt__"][0].value)
    snap = drill_graph.get_state(config)
    if not snap.next:
        rep = snap.values.get("report", {})
        emit({"type": "done", "report": {k: v for k, v in rep.items() if k != "results"},
              "results": rep.get("results", []), "report_url": f"/drill/{sid}/report"})


@router.post("/api/drill/{sid}/start")
def api_start(sid: str, user: User = Depends(require_user_api), db: Session = Depends(get_db)):
    ds = _own_drill(db, sid, user)
    if ds.status != "in_progress":
        raise HTTPException(409, "This practice session has finished.")
    plan = ds.items or []
    lock = acquire(sid)
    initial = {"session_id": ds.id, "title": ds.title, "feedback_mode": ds.feedback_mode, "items": plan}

    def work(emit):
        snap = drill_graph.get_state(_config(sid))
        if snap.values:  # reload: re-send what was answered so far, then the open question
            emit({"type": "history", "results": snap.values.get("results", [])})
            q = _pending(snap)
            emit(q if q else {"type": "error", "message": "Session is not waiting for an answer."})
            return
        if not plan:
            emit({"type": "error", "message": "This session has no questions."})
            return
        _stream(initial, sid, emit)

    return run_streaming(sid, lock, work, user.id)


@router.post("/api/drill/{sid}/turn")
async def api_turn(sid: str, audio_file: UploadFile | None = File(None), live_transcript: str = Form(""),
                   end: bool = Form(False), skip: bool = Form(False),
                   user: User = Depends(require_user_api), db: Session = Depends(get_db)):
    ds = await run_in_threadpool(_own_drill, db, sid, user)
    live_feedback = ds.feedback_mode == "each"
    lock = acquire(sid)
    try:
        snap = await run_in_threadpool(drill_graph.get_state, _config(sid))
        if not snap.next or _pending(snap) is None:
            raise HTTPException(409, "The session is not waiting for an answer.")
        webm_path = None
        if audio_file is not None and not (end or skip):
            data = await audio_file.read()
            if data:
                webm_path = UPLOAD_DIR / f"drill_{sid}_{uuid.uuid4().hex[:6]}.webm"
                webm_path.write_bytes(data)
    except BaseException:
        lock.release()
        raise

    def work(emit):
        if end or skip:
            if end:
                emit({"type": "status", "text": "Finishing up…"})
            _stream(Command(resume={"end": end, "skip": skip}), sid, emit)
            return
        heard = _transcribe_turn(webm_path, live_transcript)
        emit({"type": "heard", "text": heard["text"], "fluency": heard["fluency"]})
        if heard["text"] and live_feedback:
            emit({"type": "status", "text": "Reviewing your answer…"})
        _stream(Command(resume=heard), sid, emit)

    return run_streaming(sid, lock, work, user.id)
