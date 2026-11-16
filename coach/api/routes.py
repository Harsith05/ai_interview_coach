"""Interview pages and the interview session API (LangGraph)."""
import json
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, StreamingResponse
from langgraph.types import Command
from sqlalchemy.orm import Session

from coach.api.streaming import acquire, run_streaming
from coach.api.account_routes import key_required_redirect
from coach import apikeys
from coach.auth import current_user_optional, require_user_api, require_user_page
from coach.config import (DEFAULT_SENIORITY, DOMAIN_FILES, ENABLE_SERVER_CAMERA, MODES, QUESTIONS_PER_INTERVIEW,
                          SENIORITY, UPLOAD_DIR)
from coach.db import get_db
from coach.graph import jobs
from coach.graph.builder import interview_graph
from coach.models import Interview, User
from coach.schemas import FrameRequest, FrameResponse, ScoreRequest, ScoreResponse
from coach.services import resume, scoring, vision
from coach.templating import templates

log = logging.getLogger(__name__)
pages = APIRouter()
api = APIRouter(prefix="/api")

MAX_JD_CHARS = 8000


def _config(interview_id: str) -> dict:
    return {"configurable": {"thread_id": interview_id}}


def _own_interview(db: Session, interview_id: str, user: User) -> Interview:
    iv = db.get(Interview, interview_id)
    if iv is None or iv.user_id != user.id:
        raise HTTPException(404, "Interview not found")
    return iv


def _pending_question(snapshot) -> dict | None:
    for task in snapshot.tasks:
        for intr in getattr(task, "interrupts", ()) or ():
            return intr.value
    return None


# ---------------------------------------------------------------- pages

@pages.get("/")
def home(request: Request, user: User | None = Depends(current_user_optional), db: Session = Depends(get_db)):
    """Landing page for visitors; a hub of practice modes for signed-in users."""
    if user is None:
        return templates.TemplateResponse(request, "landing.html", {"user": None})
    recent = (db.query(Interview).filter_by(user_id=user.id, status="completed")
              .order_by(Interview.completed_at.desc()).limit(3).all())
    return templates.TemplateResponse(request, "home.html", {
        "user": user, "recent": recent, "has_key": apikeys.has_keys(user.id),
        "flash": request.session.pop("flash", None)})


@pages.get("/interviews/new")
def new_interview(request: Request, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    if (r := key_required_redirect(user)) is not None:
        return r
    last = (db.query(Interview.seniority).filter(Interview.user_id == user.id, Interview.seniority.isnot(None))
            .order_by(Interview.created_at.desc()).first())
    return templates.TemplateResponse(request, "index.html", {
        "user": user, "error": None, "default_n": QUESTIONS_PER_INTERVIEW,
        "last_seniority": last[0] if last else None,
    })


@pages.post("/interviews")
async def create_interview(
    request: Request,
    role: str = Form("ml"),
    mode: str = Form("technical"),
    seniority: str = Form(DEFAULT_SENIORITY),
    num_questions: int = Form(QUESTIONS_PER_INTERVIEW),
    job_description: str = Form(""),
    resume_file: UploadFile | None = File(None),
    user: User = Depends(require_user_page),
    db: Session = Depends(get_db),
):
    if (r := key_required_redirect(user)) is not None:
        return r
    def fail(msg: str):
        return templates.TemplateResponse(request, "index.html", {
            "user": user, "error": msg, "default_n": num_questions, "last_seniority": seniority}, status_code=400)

    if role not in DOMAIN_FILES or mode not in MODES or seniority not in SENIORITY:
        return fail("Please choose a valid role, experience level and interview type.")
    if not 1 <= num_questions <= 10:
        return fail("Number of questions must be between 1 and 10.")
    resume_text = ""
    if resume_file is not None and resume_file.filename:
        try:
            resume_text = await run_in_threadpool(resume.extract_text, await resume_file.read(), resume_file.filename)
        except ValueError as e:
            return fail(str(e))

    iv = Interview(id=uuid.uuid4().hex, user_id=user.id, role=role, mode=mode, num_questions=num_questions,
                   seniority=seniority, resume_text=resume_text or None,
                   job_description=job_description.strip()[:MAX_JD_CHARS] or None)
    db.add(iv)
    return RedirectResponse(f"/interviews/{iv.id}/live", status_code=303)


@pages.get("/interviews/{interview_id}/live")
def interview_page(request: Request, interview_id: str, user: User = Depends(require_user_page),
                   db: Session = Depends(get_db)):
    iv = _own_interview(db, interview_id, user)
    if iv.status == "completed":
        return RedirectResponse(f"/interviews/{iv.id}/report", status_code=303)
    return templates.TemplateResponse(request, "interview.html", {
        "user": user, "interview": iv, "server_camera": ENABLE_SERVER_CAMERA,
    })


@pages.get("/video_feed")
def video_feed(user: User = Depends(require_user_api)):
    if not ENABLE_SERVER_CAMERA:
        raise HTTPException(404, "Server camera disabled")
    return StreamingResponse(vision.camera.frames(), media_type="multipart/x-mixed-replace; boundary=frame")


# ---------------------------------------------------------------- streaming helper

def _stream_graph(graph_input, interview_id: str, emit) -> None:
    """Advance the graph and translate its stream into UI events."""
    config = _config(interview_id)
    analyzed, total = 0, 0
    for mode, chunk in interview_graph.stream(graph_input, config,
                                              stream_mode=["updates", "messages", "custom"]):
        if mode == "custom":
            emit(chunk)
        elif mode == "messages":
            msg, meta = chunk
            if meta.get("langgraph_node") == "generate_feedback":
                text = msg.text if isinstance(msg.text, str) else msg.text()
                if text:
                    emit({"type": "token", "text": text})
        elif mode == "updates":
            if "__interrupt__" in chunk:
                emit(chunk["__interrupt__"][0].value)
            if "generate_follow_up" in chunk and chunk["generate_follow_up"].get("next_action") == "ask":
                emit({"type": "status", "text": "One follow-up question…"})
            if "analyze_answer" in chunk:
                analyzed += 1
                if not total:
                    total = len(interview_graph.get_state(config).values.get("answers", []))
                emit({"type": "progress", "text": f"Analyzed {analyzed} of {total} answers…",
                      "done": analyzed, "total": total})
    snap = interview_graph.get_state(config)
    if not snap.next:
        v = snap.values
        emit({"type": "done", "results": v.get("results", []), "summary": v.get("summary", {}),
              "feedback": v.get("feedback", ""), "report_url": f"/interviews/{interview_id}/report"})


# ---------------------------------------------------------------- interview API

@api.get("/interviews/{interview_id}")
def get_interview(interview_id: str, user: User = Depends(require_user_api), db: Session = Depends(get_db)):
    iv = _own_interview(db, interview_id, user)
    snap = interview_graph.get_state(_config(interview_id))
    v = snap.values or {}
    return {
        "id": iv.id, "status": iv.status, "role": iv.role, "mode": iv.mode,
        "started": bool(v), "done": bool(v) and not snap.next,
        "pending_question": _pending_question(snap) if v else None,
        "turn": v.get("turn", 0), "main_asked": v.get("main_asked", 0), "total": iv.num_questions,
        "background_analysis": jobs.status(interview_id),
    }


@api.post("/interviews/{interview_id}/start")
def start_interview(interview_id: str, user: User = Depends(require_user_api), db: Session = Depends(get_db)):
    iv = _own_interview(db, interview_id, user)
    if iv.status != "in_progress":
        raise HTTPException(409, "This interview has already finished.")
    lock = acquire(interview_id)
    initial = {"interview_id": iv.id, "role": iv.role, "mode": iv.mode, "num_questions": iv.num_questions,
               "resume_text": iv.resume_text or "", "job_description": iv.job_description or "",
               "seniority": iv.seniority or "", "focus_topics": iv.focus_topics or []}

    def work(emit):
        snap = interview_graph.get_state(_config(interview_id))
        if snap.values:  # already started (e.g. page reload): just re-send the open question
            q = _pending_question(snap)
            emit(q if q else {"type": "error", "message": "Interview is not waiting for an answer."})
            return
        _stream_graph(initial, interview_id, emit)

    return run_streaming(interview_id, lock, work, user.id)


@api.post("/interviews/{interview_id}/answer")
async def submit_answer(
    interview_id: str,
    audio_file: UploadFile | None = File(None),
    live_transcript: str = Form(""),
    camera_samples: str = Form("[]"),
    user: User = Depends(require_user_api),
    db: Session = Depends(get_db),
):
    iv = await run_in_threadpool(_own_interview, db, interview_id, user)
    lock = acquire(interview_id)
    try:
        snap = await run_in_threadpool(interview_graph.get_state, _config(interview_id))
        current = (snap.values or {}).get("current")
        if not snap.next or current is None or _pending_question(snap) is None:
            raise HTTPException(409, "The interview is not waiting for an answer.")

        submission = {"live_transcript": live_transcript.strip(), "webm_path": None, "audio_bytes": 0}
        try:
            submission["camera_samples"] = json.loads(camera_samples or "[]")
        except json.JSONDecodeError:
            submission["camera_samples"] = []
        if audio_file is not None:
            data = await audio_file.read()
            submission["audio_bytes"] = len(data)
            if data:
                path = UPLOAD_DIR / f"{interview_id}_{current['turn']}_{uuid.uuid4().hex[:6]}.webm"
                path.write_bytes(data)
                submission["webm_path"] = str(path)
    except BaseException:
        lock.release()
        raise

    log.info("Answer turn %d for %s: audio=%d bytes, live_transcript=%d chars, camera_samples=%d",
             current["turn"] + 1, interview_id[:8], submission["audio_bytes"],
             len(submission["live_transcript"]), len(submission["camera_samples"]))

    def work(emit):
        job = jobs.start(interview_id, iv.mode, current, dict(submission))
        live = submission["live_transcript"]
        if len(live.split()) >= 5:
            quick = live  # browser transcript is good enough to decide what to ask next
        else:
            emit({"type": "status", "text": "Listening back to your answer…"})
            quick = jobs.quick_transcript(job, live)
        _stream_graph(Command(resume={**submission, "quick_transcript": quick}), interview_id, emit)

    return run_streaming(interview_id, lock, work, user.id)


# ---------------------------------------------------------------- live helpers

@api.post("/score_answer", response_model=ScoreResponse)
async def score_answer(body: ScoreRequest, user: User = Depends(require_user_api)):
    score, best, missing = await run_in_threadpool(scoring.similarity_score, body.user_answer, body.ideal_answers)
    return ScoreResponse(score=score, best_match=best, missing_keywords=missing)


@api.post("/analyze_frame", response_model=FrameResponse)
async def analyze_frame(body: FrameRequest, user: User = Depends(require_user_api)):
    frame = vision.decode_data_url(body.image)
    if frame is None:
        raise HTTPException(400, "Invalid image data")
    return FrameResponse(**await run_in_threadpool(vision.analyze_frame, frame))
