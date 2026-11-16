"""Weak-topic tracker page and 'practise these topics' interviews."""
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from coach.api.account_routes import key_required_redirect
from coach.auth import require_user_page
from coach.config import DEFAULT_SENIORITY, QUESTIONS_PER_INTERVIEW
from coach.db import get_db
from coach.models import Interview, User
from coach.services import topics as topic_svc
from coach.templating import templates

router = APIRouter()


@router.get("/topics")
def topics_page(request: Request, user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    stats = topic_svc.topic_stats(db, user.id)
    groups = {key: [t for t in stats if t["status"] == key] for key in topic_svc.STATUS}
    return templates.TemplateResponse(request, "topics.html", {
        "user": user, "stats": stats, "groups": groups, "STATUS": topic_svc.STATUS,
        "max_focus": topic_svc.MAX_FOCUS_TOPICS,
        "flash": request.session.pop("flash", None),
    })


@router.post("/interviews/practice-topics")
def practice_topics(request: Request, topic: list[str] = Form(default=[]), only: str = Form(""),
                    user: User = Depends(require_user_page), db: Session = Depends(get_db)):
    """Start a mock interview that concentrates on the chosen weak topics (max 3).
    `only` comes from a single topic's own Practice button and wins over the ticked boxes."""
    if (r := key_required_redirect(user)) is not None:
        return r
    plan = topic_svc.focus_plan(topic_svc.topic_stats(db, user.id), [only] if only else topic)
    if plan is None:
        request.session["flash"] = {"kind": "error", "text": "Choose at least one topic to practise."}
        return RedirectResponse("/topics", status_code=303)

    last = (db.query(Interview.seniority).filter(Interview.user_id == user.id, Interview.seniority.isnot(None))
            .order_by(Interview.created_at.desc()).first())
    iv = Interview(id=uuid.uuid4().hex, user_id=user.id, role=plan["role"], mode=plan["mode"],
                   num_questions=min(QUESTIONS_PER_INTERVIEW, max(3, 2 * len(plan["topics"]))),
                   seniority=last[0] if last else DEFAULT_SENIORITY, focus_topics=plan["topics"])
    db.add(iv)
    db.commit()
    return RedirectResponse(f"/interviews/{iv.id}/live", status_code=303)
