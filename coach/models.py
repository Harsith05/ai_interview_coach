from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from coach.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)  # stored lower-case
    password_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    is_admin: Mapped[bool] = mapped_column(default=False, server_default="false")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    login_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    interviews: Mapped[list["Interview"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    def __str__(self) -> str:   # how the admin panel shows a user in related rows
        return f"{self.name} <{self.email}>"

    # Admin panel columns (the admin preloads `interviews` for these).
    @property
    def completed_interviews(self) -> int:
        return sum(1 for i in self.interviews if i.status == "completed")

    @property
    def avg_interview_score(self) -> float | None:
        scores = [i.overall_score for i in self.interviews if i.status == "completed" and i.overall_score is not None]
        return round(sum(scores) / len(scores), 1) if scores else None


class Interview(Base):
    """One interview session. `id` is also the LangGraph thread id."""
    __tablename__ = "interviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(50))
    mode: Mapped[str] = mapped_column(String(20), default="technical")
    status: Mapped[str] = mapped_column(String(20), default="in_progress")  # in_progress|completed|abandoned
    num_questions: Mapped[int] = mapped_column(Integer)
    resume_text: Mapped[str | None] = mapped_column(Text)
    job_description: Mapped[str | None] = mapped_column(Text)

    overall_score: Mapped[float | None] = mapped_column(Float)   # 0-10 average of LLM grades
    summary: Mapped[dict | None] = mapped_column(JSON)
    feedback_html: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="interviews")
    answers: Mapped[list["AnswerResult"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan", order_by="AnswerResult.turn"
    )


class AnswerResult(Base):
    """Analysis of one answer, saved as soon as the background job finishes."""
    __tablename__ = "answer_results"
    __table_args__ = (UniqueConstraint("interview_id", "turn"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    turn: Mapped[int] = mapped_column(Integer)   # 0-based position in the interview (incl. follow-ups)
    question: Mapped[str] = mapped_column(Text)
    is_follow_up: Mapped[bool] = mapped_column(default=False)
    difficulty: Mapped[str | None] = mapped_column(String(10))
    grade: Mapped[float | None] = mapped_column(Float)   # 0-10
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    interview: Mapped[Interview] = relationship(back_populates="answers")


class PracticeSession(Base):
    """An English speaking-practice conversation. `id` is also the LangGraph thread id."""
    __tablename__ = "practice_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scenario: Mapped[str] = mapped_column(String(40))
    level: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="in_progress")  # in_progress|completed
    user_turns: Mapped[int] = mapped_column(Integer, default=0)
    overall_score: Mapped[float | None] = mapped_column(Float)   # 0-10
    report: Mapped[dict | None] = mapped_column(JSON)
    transcript: Mapped[dict | None] = mapped_column(JSON)       # {"messages": [...], "turns": [...]}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(viewonly=True)


class TalkSession(Base):
    """One sitting with the Talk companion. Memory carries across sessions via the LangGraph store."""
    __tablename__ = "talk_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | closed
    user_turns: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(viewonly=True)


class QuestionSet(Base):
    """A user's own list of questions for the AI to ask them."""
    __tablename__ = "question_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    items: Mapped[list] = mapped_column(JSON)   # [{"question": str, "answer": str | ""}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(viewonly=True)


class DrillSession(Base):
    """One run through a question set. `id` is also the LangGraph thread id."""
    __tablename__ = "drill_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    set_id: Mapped[int | None] = mapped_column(ForeignKey("question_sets.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(120))
    feedback_mode: Mapped[str] = mapped_column(String(20), default="each")   # each | end
    items: Mapped[list] = mapped_column(JSON)     # questions in asking order (snapshot of the set)
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    overall_score: Mapped[float | None] = mapped_column(Float)
    results: Mapped[list | None] = mapped_column(JSON)
    report: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(viewonly=True)


class ApiKey(Base):
    """A user's own Gemini API key, encrypted at rest (see coach/apikeys.py)."""
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(60))
    key_encrypted: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(8))
    is_default: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="unchecked")   # ok | invalid | unchecked
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(viewonly=True)


class Feedback(Base):
    """Feedback users send from the app (bug reports, ideas, ratings), read in the admin panel."""
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="other")    # bug | idea | praise | other
    rating: Mapped[int | None] = mapped_column(Integer)                # 1-5 stars, optional
    message: Mapped[str] = mapped_column(Text)
    page: Mapped[str | None] = mapped_column(String(300))              # where it was sent from
    interview_id: Mapped[str | None] = mapped_column(String(64))       # set when rating an interview
    user_agent: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="new")     # new | read | done
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    user: Mapped["User | None"] = relationship(viewonly=True)


class LoginEvent(Base):
    """Every login attempt (for the admin panel's login history)."""
    __tablename__ = "login_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    success: Mapped[bool] = mapped_column(default=True)
    kind: Mapped[str] = mapped_column(String(20), default="login")   # login | register | reset
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    user: Mapped["User | None"] = relationship(viewonly=True)
