"""'Ask about my feedback': chat with the coach about one finished interview.

The coach answers from that interview's evidence only (questions, transcripts, grades, report),
streams its reply as NDJSON tokens, and the conversation is saved per interview.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from html import unescape

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from coach.api.streaming import acquire, run_streaming
from coach.apikeys import NoApiKey
from coach.auth import require_user_api
from coach.config import MODES, ROLE_LABELS, SENIORITY, SENIORITY_GUIDE
from coach.db import db_session, get_db
from coach.models import Interview, ReportChatMessage, User
from coach.services.llm import get_fast_llm

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

MAX_CHARS = 1000          # per question
HISTORY = 12              # earlier messages sent back to the model
MAX_PER_DAY = 60          # questions per user per day (each one is a Gemini call on their key)

CHAT_PROMPT = """You are the AI interview coach who ran and graded this mock interview. The candidate is now \
asking you follow-up questions about their report.

Rules:
- Base every answer on this interview's evidence below (questions, the candidate's transcripts, grades and \
your report). Refer to questions as Q1, Q2, …
- Be specific and practical: explain why a grade was given, what a stronger answer would include, and how to \
practise it.
- When asked for a better or model answer, write one the candidate could say aloud in about 45-90 seconds, \
pitched at their experience level.
- Keep replies under about 180 words unless they ask for a full example answer.
- Plain text only: short paragraphs and "- " bullet points; **bold** for key terms is fine. No headings, \
tables, HTML or code fences.
- Be encouraging but honest. If a question has nothing to do with interview preparation, briefly steer back."""


def _friendly_error(e: Exception) -> dict:
    """Turn a Gemini/LangChain exception into a short message the candidate can act on."""
    msg = str(e)
    if "API key not valid" in msg or "API_KEY_INVALID" in msg or "PERMISSION_DENIED" in msg:
        return {"type": "error", "need_api_key": True,
                "message": "Google rejected your Gemini API key. Please check it on the API keys page."}
    if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
        return {"type": "error", "message": "Your Gemini API key has reached its usage limit for now. "
                                            "Please try again in a minute."}
    if "503" in msg or "UNAVAILABLE" in msg or "overloaded" in msg.lower() or "timed out" in msg.lower():
        return {"type": "error", "message": "Gemini is busy right now. Please try again in a moment."}
    log.warning("Report chat failed: %s", msg[:300])
    return {"type": "error", "message": "The coach couldn't answer just now. Please try again."}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_CHARS)


def _own_completed(db: Session, interview_id: str, user: User) -> Interview:
    iv = db.get(Interview, interview_id)
    if iv is None or iv.user_id != user.id:
        raise HTTPException(404, "Interview not found")
    if iv.status != "completed":
        raise HTTPException(409, "The report isn't ready yet.")
    return iv


def _html_to_text(html: str) -> str:
    text = re.sub(r"<\s*(br|/p|/h\d|/li)\s*>", "\n", html or "", flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _cut(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def interview_context(iv: Interview) -> str:
    """Everything the coach knows about this interview, as plain text for the prompt."""
    s = iv.summary or {}
    lines = [f"Role: {ROLE_LABELS.get(iv.role, iv.role)} | Interview type: {MODES.get(iv.mode, iv.mode)}"]
    if iv.seniority in SENIORITY:
        lines.append(f"Candidate level: {SENIORITY[iv.seniority]} ({SENIORITY_GUIDE[iv.seniority]})")
    if iv.focus_topics:
        lines.append(f"This interview practised weak topics: {', '.join(iv.focus_topics)}")
    lines.append(f"Overall score: {s.get('overall_score')}/10 | answers: {s.get('questions_answered')} "
                 f"| filler words: {s.get('total_fillers')} | pace: {s.get('avg_wpm')} WPM "
                 f"| eye contact: {s.get('avg_gaze_pct')}%")
    lines.append("")
    for a in iv.answers:
        r = a.result or {}
        g = r.get("grade") or {}
        label = f"Q{r.get('number', '?')}{' (follow-up)' if r.get('is_follow_up') else ''}"
        lines += [
            f"{label} [{r.get('difficulty') or '-'} | topic: {r.get('topic') or '-'}]: {r.get('q', a.question)}",
            f"Candidate's answer: {_cut(r.get('transcript') or '(no speech detected)', 1500)}",
            f"Grade: {g.get('overall')}/10 (accuracy {g.get('accuracy')}, depth {g.get('depth')}, "
            f"structure {g.get('structure')}, communication {g.get('communication')}; 1-5)",
            f"Covered: {g.get('covered_points') or '-'} | Missed: {g.get('missed_points') or '-'}",
            f"Strength: {g.get('strength') or '-'} | Improvement: {g.get('improvement') or '-'}",
        ]
        if g.get("star"):
            lines.append(f"STAR: {g['star']}")
        if r.get("ideal"):
            lines.append(f"Reference answer: {_cut(r['ideal'], 600)}")
        lines.append(f"Delivery: {r.get('wpm')} WPM, fillers {r.get('filler_count')}, eye contact {r.get('gaze_pct')}%")
        lines.append("---")
    lines += ["", "Your written report:", _cut(_html_to_text(iv.feedback_html or ""), 5000)]
    return "\n".join(lines)


@router.get("/interviews/{interview_id}/chat")
def chat_history(interview_id: str, user: User = Depends(require_user_api), db: Session = Depends(get_db)):
    _own_completed(db, interview_id, user)
    rows = (db.query(ReportChatMessage).filter_by(interview_id=interview_id)
            .order_by(ReportChatMessage.id).all())
    return {"messages": [{"role": m.role, "content": m.content} for m in rows]}


@router.post("/interviews/{interview_id}/chat")
def chat_ask(interview_id: str, body: ChatRequest, user: User = Depends(require_user_api),
             db: Session = Depends(get_db)):
    iv = _own_completed(db, interview_id, user)
    question = body.message.strip()
    if not question:
        raise HTTPException(422, "Type a question first.")

    since = datetime.now(timezone.utc) - timedelta(days=1)
    asked_today = (db.query(func.count(ReportChatMessage.id))
                   .join(Interview, ReportChatMessage.interview_id == Interview.id)
                   .filter(Interview.user_id == user.id, ReportChatMessage.role == "user",
                           ReportChatMessage.created_at >= since).scalar())
    if asked_today >= MAX_PER_DAY:
        raise HTTPException(429, f"You've asked {MAX_PER_DAY} questions in the last 24 hours. Please come back later.")

    history = (db.query(ReportChatMessage).filter_by(interview_id=interview_id)
               .order_by(ReportChatMessage.id.desc()).limit(HISTORY).all())[::-1]
    messages = [SystemMessage(CHAT_PROMPT + "\n\n=== INTERVIEW ===\n" + interview_context(iv))]
    messages += [HumanMessage(m.content) if m.role == "user" else AIMessage(m.content) for m in history]
    messages.append(HumanMessage(question))

    lock = acquire(f"chat-{interview_id}")   # one answer at a time per report

    def work(emit):
        parts: list[str] = []
        try:
            for chunk in get_fast_llm().stream(messages):
                text = chunk.text if isinstance(chunk.text, str) else chunk.text()
                if text:
                    parts.append(text)
                    emit({"type": "token", "text": text})
        except NoApiKey:
            raise   # run_streaming turns this into the "add your API key" event
        except Exception as e:
            emit(_friendly_error(e))
            return
        answer = "".join(parts).strip() or "Sorry, I couldn't come up with an answer. Please try rephrasing."
        # Saved only after a successful reply, so a failed attempt can simply be retried.
        with db_session() as s:
            s.add_all([ReportChatMessage(interview_id=interview_id, role="user", content=question),
                       ReportChatMessage(interview_id=interview_id, role="assistant", content=answer)])
        emit({"type": "done"})

    return run_streaming(f"chat-{interview_id}", lock, work, user.id)
