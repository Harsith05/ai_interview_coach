"""Dashboard (history + progress), interview report page, PDF export, delete."""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from coach.auth import require_user_page
from coach.db import get_db
from coach.graph import jobs, nodes
from coach.services.llm import as_user
from coach.graph.builder import interview_graph
from coach.models import Interview, PracticeSession, User
from coach.practice.scenarios import LEVELS, SCENARIOS
from coach.services.report_pdf import render_pdf
from coach.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _own(db: Session, interview_id: str, user: User) -> Interview:
    iv = db.get(Interview, interview_id)
    if iv is None or iv.user_id != user.id:
        raise HTTPException(404, "Interview not found")
    return iv


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


@router.get("/dashboard")
def dashboard(request: Request, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    interviews = (db.query(Interview).filter(Interview.user_id == user.id)
                  .filter(Interview.status != "abandoned")
                  .order_by(Interview.created_at.desc()).all())
    completed = [iv for iv in interviews if iv.status == "completed"]
    in_progress = [iv for iv in interviews if iv.status == "in_progress"]

    chrono = sorted(completed, key=lambda iv: iv.completed_at or iv.created_at)
    chart = {
        "labels": [(iv.completed_at or iv.created_at).astimezone().strftime("%d %b %H:%M") for iv in chrono],
        "overall": [iv.overall_score for iv in chrono],
        "fillers": [(iv.summary or {}).get("filler_per_min") for iv in chrono],
        "eye": [(iv.summary or {}).get("avg_gaze_pct") for iv in chrono],
    }

    # Weakest topics across all completed interviews (lowest average grade).
    topic_scores: dict[str, list[float]] = {}
    for iv in completed:
        for a in iv.answers:
            topic = (a.result or {}).get("topic")
            if topic and a.grade is not None:
                topic_scores.setdefault(topic, []).append(a.grade)
    weakest = sorted(((t, _avg(s), len(s)) for t, s in topic_scores.items()), key=lambda x: x[1])[:5]

    summaries = [iv.summary or {} for iv in completed]
    stats = {
        "count": len(completed),
        "avg_score": _avg([iv.overall_score for iv in completed]),
        "best_score": max((iv.overall_score for iv in completed if iv.overall_score is not None), default=None),
        "avg_wpm": _avg([s.get("avg_wpm") for s in summaries]),
        "filler_per_min": _avg([s.get("filler_per_min") for s in summaries]),
        "eye_contact": _avg([s.get("avg_gaze_pct") for s in summaries]),
    }
    practice = (db.query(PracticeSession).filter(PracticeSession.user_id == user.id)
                .order_by(PracticeSession.created_at.desc()).limit(20).all())
    practice_done = [p for p in practice if p.status == "completed"]
    practice_stats = {
        "count": len(practice_done),
        "avg_score": _avg([p.overall_score for p in practice_done]),
        "latest_level": next((p.report.get("cefr_level") for p in practice_done if p.report and p.report.get("cefr_level")), None),
    }
    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user, "completed": completed, "in_progress": in_progress, "stats": stats,
        "weakest": weakest, "chart_json": json.dumps(chart),
        "practice": practice, "practice_stats": practice_stats,
        "SCENARIOS": SCENARIOS, "LEVELS": LEVELS,
    })


@router.get("/interviews/{interview_id}/report")
def report(request: Request, interview_id: str, user: User = Depends(require_user_page),
           db: Session = Depends(get_db)):
    iv = _own(db, interview_id, user)
    if iv.status != "completed":
        return RedirectResponse(f"/interviews/{iv.id}/live", status_code=303)
    return templates.TemplateResponse(request, "report.html", {
        "user": user, "iv": iv, "answers": [a.result for a in iv.answers], "s": iv.summary or {},
    })


@router.get("/interviews/{interview_id}/report.pdf")
async def report_pdf(interview_id: str, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    iv = await run_in_threadpool(_own, db, interview_id, user)
    if iv.status != "completed":
        raise HTTPException(409, "The report is available once the interview is complete.")
    pdf = await run_in_threadpool(render_pdf, iv, user)
    filename = f"interview-report-{iv.role}-{(iv.completed_at or iv.created_at):%Y%m%d}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/interviews/{interview_id}/regenerate")
async def regenerate(interview_id: str, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    iv = await run_in_threadpool(_own, db, interview_id, user)
    if iv.status != "completed":
        raise HTTPException(409, "The interview isn't finished yet.")
    def regen():
        with as_user(user.id):
            nodes.regenerate_report(iv.id)
    await run_in_threadpool(regen)
    return RedirectResponse(f"/interviews/{iv.id}/report", status_code=303)


@router.post("/interviews/{interview_id}/delete")
def delete_interview(interview_id: str, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    iv = _own(db, interview_id, user)
    jobs.forget(iv.id)
    try:
        interview_graph.checkpointer.delete_thread(iv.id)
    except Exception:
        log.exception("Could not delete checkpoints for %s", iv.id[:8])
    db.delete(iv)
    return RedirectResponse("/dashboard", status_code=303)
