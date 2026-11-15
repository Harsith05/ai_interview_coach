"""Admin-only views of any user's reports (linked from the admin panel)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from coach.admin import is_admin
from coach.auth import require_user_page
from coach.db import get_db
from coach.models import DrillSession, Interview, PracticeSession, User
from coach.practice.scenarios import LEVELS, SCENARIOS
from coach.services.report_pdf import render_pdf
from coach.templating import templates

router = APIRouter(prefix="/staff")


def require_admin(user: User = Depends(require_user_page)) -> User:
    if not is_admin(user):
        raise HTTPException(403, "Admins only")
    return user


def _banner(owner: User) -> dict:
    return {"kind": "info", "text": f"Admin view: this report belongs to {owner.name} ({owner.email})."}


@router.get("/interview/{interview_id}")
def interview_report(request: Request, interview_id: str, admin: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    iv = db.get(Interview, interview_id)
    if iv is None:
        raise HTTPException(404, "Interview not found")
    return templates.TemplateResponse(request, "report.html", {
        "user": admin, "iv": iv, "answers": [a.result for a in iv.answers], "s": iv.summary or {},
        "staff": True, "flash": _banner(iv.user)})


@router.get("/interview/{interview_id}/pdf")
async def interview_pdf(interview_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    iv = db.get(Interview, interview_id)
    if iv is None or iv.status != "completed":
        raise HTTPException(404, "Report not found")
    pdf = await run_in_threadpool(render_pdf, iv, iv.user)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="interview-{iv.user.email}-{iv.id[:8]}.pdf"'})


@router.get("/practice/{session_id}")
def practice_report(request: Request, session_id: str, admin: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    ps = db.get(PracticeSession, session_id)
    if ps is None:
        raise HTTPException(404, "Session not found")
    return templates.TemplateResponse(request, "practice_report.html", {
        "user": admin, "ps": ps, "scenario": SCENARIOS.get(ps.scenario, SCENARIOS["casual"]),
        "level": LEVELS.get(ps.level, LEVELS["intermediate"]), "r": ps.report or {},
        "t": ps.transcript or {"messages": [], "turns": []}, "flash": _banner(ps.user)})


@router.get("/drill/{session_id}")
def drill_report(request: Request, session_id: str, admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    ds = db.get(DrillSession, session_id)
    if ds is None:
        raise HTTPException(404, "Session not found")
    return templates.TemplateResponse(request, "drill_report.html", {
        "user": admin, "ds": ds, "r": ds.report or {}, "results": ds.results or [], "flash": _banner(ds.user)})
