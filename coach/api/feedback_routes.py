"""Users send feedback (bugs, ideas, ratings) from any page; admins read it in /admin."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from coach.auth import require_user_api
from coach.db import get_db
from coach.models import Feedback, Interview, User
from coach.schemas import FeedbackRequest

router = APIRouter(prefix="/api")

MAX_PER_HOUR = 10   # stops accidental double-submits and spam from one account


@router.post("/feedback", status_code=201)
def send_feedback(body: FeedbackRequest, request: Request, user: User = Depends(require_user_api),
                  db: Session = Depends(get_db)):
    if not body.message and body.rating is None:
        raise HTTPException(422, "Write a message or choose a rating.")

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    recent = db.query(func.count(Feedback.id)).filter(Feedback.user_id == user.id,
                                                      Feedback.created_at >= since).scalar()
    if recent >= MAX_PER_HOUR:
        raise HTTPException(429, "Thanks! You've sent a lot of feedback recently. Please try again in a while.")

    interview_id = body.interview_id
    if interview_id:  # only link interviews that belong to this user
        iv = db.get(Interview, interview_id)
        if iv is None or iv.user_id != user.id:
            interview_id = None

    db.add(Feedback(user_id=user.id, kind=body.kind, rating=body.rating, message=body.message,
                    page=(body.page or "")[:300] or None, interview_id=interview_id,
                    user_agent=(request.headers.get("user-agent") or "")[:300] or None))
    db.commit()
    return {"ok": True}
