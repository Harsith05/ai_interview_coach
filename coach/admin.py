"""Admin panel at /admin (SQLAdmin: a Django-admin-style panel for SQLAlchemy).

Only admins can open it (users with is_admin, or listed in ADMIN_EMAILS). It shares the app's
own login session, so admins just log in normally and visit /admin.
"""
import json
from datetime import datetime, timedelta, timezone

from markupsafe import Markup, escape
from sqladmin import Admin, BaseView, ModelView, expose
from sqladmin.authentication import AuthenticationBackend
from sqladmin.filters import AllUniqueStringValuesFilter, BooleanFilter
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from wtforms import SelectField

from coach.config import ADMIN_EMAILS, BASE_DIR, ROLE_LABELS, SECRET_KEY
from coach.db import db_session, engine
from coach.models import (AnswerResult, ApiKey, DrillSession, Feedback, Interview, LoginEvent,
                          PracticeSession, QuestionSet, TalkSession, User)


def is_admin(user: User | None) -> bool:
    return bool(user) and (user.is_admin or user.email.lower() in ADMIN_EMAILS)


# ---------------------------------------------------------------- authentication

class AdminAuth(AuthenticationBackend):
    def __init__(self):
        super().__init__(secret_key=SECRET_KEY)
        # Use the app's own session cookie (no second cookie): admins log in on the normal /login page.
        self.middlewares = []

    async def login(self, request: Request) -> bool:
        return False   # never used: authenticate() sends people to the app's login page

    async def logout(self, request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    async def authenticate(self, request: Request):
        uid = request.session.get("user_id")
        if not uid:
            return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
        with db_session() as db:
            user = db.get(User, uid)
            if is_admin(user):
                return True
        if user is None:   # stale session (account deleted): log in again
            request.session.clear()
            return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
        return HTMLResponse("<h2 style='font-family:sans-serif'>403 · Admins only</h2>"
                            "<p style='font-family:sans-serif'><a href='/'>Back to the app</a></p>", status_code=403)


# ---------------------------------------------------------------- helpers

def _dt(value) -> str:
    return value.astimezone().strftime("%d %b %Y, %H:%M") if value else "–"


def _score(value) -> Markup:
    if value is None:
        return Markup('<span class="text-muted">–</span>')
    color = "red" if value < 4 else ("orange" if value < 7 else "green")
    return Markup(f'<span class="badge bg-{color}-lt">{value:.1f}/10</span>')


def _link(href: str, text: str) -> Markup:
    return Markup(f'<a href="{escape(href)}" target="_blank">{escape(text)}</a>')


def _json(value) -> Markup:
    return Markup(f"<pre style='white-space:pre-wrap;max-height:420px;overflow:auto'>"
                  f"{escape(json.dumps(value, indent=2, ensure_ascii=False, default=str))}</pre>")


# ---------------------------------------------------------------- model views

class UserAdmin(ModelView, model=User):
    name, name_plural, icon = "User", "Users", "fa-solid fa-users"
    category = "People"
    column_list = [User.id, User.name, User.email, User.created_at, User.last_login_at, User.login_count,
                   "completed_interviews", "avg_interview_score", User.is_admin]
    column_labels = {User.interviews: "Interviews", User.login_count: "Logins", User.last_login_at: "Last login",
                     User.created_at: "Joined", User.is_admin: "Admin", "completed_interviews": "Interviews",
                     "avg_interview_score": "Avg score"}
    column_searchable_list = [User.name, User.email]
    column_sortable_list = [User.id, User.name, User.created_at, User.last_login_at, User.login_count]
    column_default_sort = (User.created_at, True)
    column_filters = [BooleanFilter(User.is_admin)]
    column_formatters = {
        User.created_at: lambda m, a: _dt(m.created_at),
        User.last_login_at: lambda m, a: _dt(m.last_login_at),
        "avg_interview_score": lambda m, a: _score(m.avg_interview_score),
    }
    column_details_list = [User.id, User.name, User.email, User.created_at, User.last_login_at, User.login_count,
                           User.is_admin, User.interviews]
    column_formatters_detail = {
        User.created_at: lambda m, a: _dt(m.created_at),
        User.last_login_at: lambda m, a: _dt(m.last_login_at),
        User.interviews: lambda m, a: Markup("<br>".join(
            f'{escape(ROLE_LABELS.get(i.role, i.role))} · {escape(i.status)} · {_score(i.overall_score)} · '
            f'{_dt(i.completed_at or i.created_at)} · {_link(f"/staff/interview/{i.id}", "report")}'
            for i in sorted(m.interviews, key=lambda i: i.created_at, reverse=True)) or "–"),
    }
    form_columns = [User.name, User.is_admin]     # passwords/emails are never edited here

    def list_query(self, request: Request):
        return select(User).options(selectinload(User.interviews))   # for the interview count / average
    can_create = False
    column_export_list = [User.id, User.name, User.email, User.created_at, User.last_login_at, User.login_count, User.is_admin]


class LoginEventAdmin(ModelView, model=LoginEvent):
    name, name_plural, icon = "Login", "Login history", "fa-solid fa-right-to-bracket"
    category = "People"
    column_list = [LoginEvent.created_at, LoginEvent.email, LoginEvent.kind, LoginEvent.success, LoginEvent.ip,
                   LoginEvent.user_agent]
    column_labels = {LoginEvent.created_at: "When", LoginEvent.kind: "Type", LoginEvent.success: "Success",
                     LoginEvent.ip: "IP address", LoginEvent.user_agent: "Browser"}
    column_searchable_list = [LoginEvent.email, LoginEvent.ip]
    column_sortable_list = [LoginEvent.created_at, LoginEvent.email]
    column_default_sort = (LoginEvent.created_at, True)
    column_filters = [BooleanFilter(LoginEvent.success), AllUniqueStringValuesFilter(LoginEvent.kind)]
    column_formatters = {
        LoginEvent.created_at: lambda m, a: _dt(m.created_at),
        LoginEvent.success: lambda m, a: Markup('<span class="badge bg-green-lt">ok</span>' if m.success
                                                else '<span class="badge bg-red-lt">failed</span>'),
        LoginEvent.user_agent: lambda m, a: (m.user_agent or "")[:60],
    }
    can_create = can_edit = False
    page_size = 50


class InterviewAdmin(ModelView, model=Interview):
    name, name_plural, icon = "Interview", "Interviews", "fa-solid fa-briefcase"
    category = "Practice"
    column_list = [Interview.user, Interview.role, Interview.mode, Interview.status, Interview.overall_score,
                   Interview.num_questions, Interview.created_at, Interview.completed_at, Interview.id]
    column_labels = {Interview.overall_score: "Score", Interview.num_questions: "Questions",
                     Interview.created_at: "Started", Interview.completed_at: "Completed", Interview.id: "Report"}
    column_sortable_list = [Interview.created_at, Interview.completed_at, Interview.overall_score]
    column_default_sort = (Interview.created_at, True)
    column_filters = [AllUniqueStringValuesFilter(Interview.status), AllUniqueStringValuesFilter(Interview.role),
                      AllUniqueStringValuesFilter(Interview.mode)]
    column_formatters = {
        Interview.role: lambda m, a: ROLE_LABELS.get(m.role, m.role),
        Interview.overall_score: lambda m, a: _score(m.overall_score),
        Interview.created_at: lambda m, a: _dt(m.created_at),
        Interview.completed_at: lambda m, a: _dt(m.completed_at),
        Interview.id: lambda m, a: _link(f"/staff/interview/{m.id}", "Open report") if m.status == "completed" else "–",
    }
    column_details_list = [Interview.user, Interview.role, Interview.mode, Interview.status, Interview.overall_score,
                           Interview.created_at, Interview.completed_at, Interview.id, Interview.summary,
                           Interview.job_description, Interview.feedback_html]
    column_formatters_detail = {
        **column_formatters,
        Interview.summary: lambda m, a: _json(m.summary),
        Interview.feedback_html: lambda m, a: Markup(m.feedback_html or "–"),   # sanitized when generated
    }
    can_create = can_edit = False
    column_export_list = [Interview.id, Interview.user_id, Interview.role, Interview.mode, Interview.status,
                          Interview.overall_score, Interview.created_at, Interview.completed_at]


class AnswerAdmin(ModelView, model=AnswerResult):
    name, name_plural, icon = "Answer", "Interview answers", "fa-solid fa-comment-dots"
    category = "Practice"
    column_list = [AnswerResult.interview, AnswerResult.turn, AnswerResult.question, AnswerResult.difficulty,
                   AnswerResult.is_follow_up, AnswerResult.grade, AnswerResult.created_at]
    column_labels = {AnswerResult.turn: "#", AnswerResult.is_follow_up: "Follow-up", AnswerResult.created_at: "When"}
    column_searchable_list = [AnswerResult.question]
    column_sortable_list = [AnswerResult.created_at, AnswerResult.grade]
    column_default_sort = (AnswerResult.created_at, True)
    column_formatters = {
        AnswerResult.interview: lambda m, a: f"{m.interview.user.name} · {ROLE_LABELS.get(m.interview.role, m.interview.role)}",
        AnswerResult.turn: lambda m, a: m.turn + 1,
        AnswerResult.question: lambda m, a: (m.question or "")[:90],
        AnswerResult.grade: lambda m, a: _score(m.grade),
        AnswerResult.created_at: lambda m, a: _dt(m.created_at),
    }
    column_formatters_detail = {AnswerResult.result: lambda m, a: _json(m.result),
                                AnswerResult.grade: lambda m, a: _score(m.grade)}
    can_create = can_edit = False

    def list_query(self, request: Request):
        # Rows are rendered after the DB session closes: preload answer → interview → user.
        return select(AnswerResult).options(selectinload(AnswerResult.interview).selectinload(Interview.user))


class PracticeAdmin(ModelView, model=PracticeSession):
    name, name_plural, icon = "Speaking session", "Speaking practice", "fa-solid fa-comments"
    category = "Practice"
    column_list = [PracticeSession.user, PracticeSession.scenario, PracticeSession.level, PracticeSession.status,
                   PracticeSession.user_turns, PracticeSession.overall_score, PracticeSession.report,
                   PracticeSession.created_at, PracticeSession.id]
    column_labels = {PracticeSession.user_turns: "Turns", PracticeSession.overall_score: "Score",
                     PracticeSession.report: "CEFR", PracticeSession.created_at: "Started", PracticeSession.id: "Report"}
    column_sortable_list = [PracticeSession.created_at, PracticeSession.overall_score]
    column_default_sort = (PracticeSession.created_at, True)
    column_filters = [AllUniqueStringValuesFilter(PracticeSession.scenario), AllUniqueStringValuesFilter(PracticeSession.status)]
    column_formatters = {
        PracticeSession.overall_score: lambda m, a: _score(m.overall_score),
        PracticeSession.report: lambda m, a: (m.report or {}).get("cefr_level") or "–",
        PracticeSession.created_at: lambda m, a: _dt(m.created_at),
        PracticeSession.id: lambda m, a: _link(f"/staff/practice/{m.id}", "Open report") if m.status == "completed" else "–",
    }
    column_formatters_detail = {PracticeSession.report: lambda m, a: _json(m.report),
                                PracticeSession.transcript: lambda m, a: _json(m.transcript),
                                PracticeSession.overall_score: lambda m, a: _score(m.overall_score)}
    can_create = can_edit = False


class DrillAdmin(ModelView, model=DrillSession):
    name, name_plural, icon = "Question practice", "Question practice", "fa-solid fa-list-check"
    category = "Practice"
    column_list = [DrillSession.user, DrillSession.title, DrillSession.feedback_mode, DrillSession.status,
                   DrillSession.overall_score, DrillSession.created_at, DrillSession.id]
    column_labels = {DrillSession.title: "Question set", DrillSession.feedback_mode: "Feedback",
                     DrillSession.overall_score: "Score", DrillSession.created_at: "Started", DrillSession.id: "Report"}
    column_sortable_list = [DrillSession.created_at, DrillSession.overall_score]
    column_default_sort = (DrillSession.created_at, True)
    column_formatters = {
        DrillSession.overall_score: lambda m, a: _score(m.overall_score),
        DrillSession.created_at: lambda m, a: _dt(m.created_at),
        DrillSession.id: lambda m, a: _link(f"/staff/drill/{m.id}", "Open report") if m.status == "completed" else "–",
    }
    column_formatters_detail = {DrillSession.results: lambda m, a: _json(m.results),
                                DrillSession.report: lambda m, a: _json(m.report),
                                DrillSession.items: lambda m, a: _json(m.items)}
    can_create = can_edit = False


class TalkAdmin(ModelView, model=TalkSession):
    name, name_plural, icon = "Talk session", "Talk sessions", "fa-solid fa-microphone"
    category = "Practice"
    column_list = [TalkSession.user, TalkSession.status, TalkSession.user_turns, TalkSession.summary,
                   TalkSession.created_at, TalkSession.last_active_at]
    column_labels = {TalkSession.user_turns: "Turns", TalkSession.created_at: "Started", TalkSession.last_active_at: "Last active"}
    column_sortable_list = [TalkSession.created_at, TalkSession.last_active_at, TalkSession.user_turns]
    column_default_sort = (TalkSession.last_active_at, True)
    column_formatters = {TalkSession.summary: lambda m, a: (m.summary or "")[:100],
                         TalkSession.created_at: lambda m, a: _dt(m.created_at),
                         TalkSession.last_active_at: lambda m, a: _dt(m.last_active_at)}
    can_create = can_edit = False


class QuestionSetAdmin(ModelView, model=QuestionSet):
    name, name_plural, icon = "Question set", "Question sets", "fa-solid fa-clipboard-question"
    category = "Practice"
    column_list = [QuestionSet.user, QuestionSet.title, QuestionSet.items, QuestionSet.updated_at]
    column_labels = {QuestionSet.items: "Questions", QuestionSet.updated_at: "Updated"}
    column_formatters = {QuestionSet.items: lambda m, a: len(m.items or []),
                         QuestionSet.updated_at: lambda m, a: _dt(m.updated_at)}
    column_formatters_detail = {QuestionSet.items: lambda m, a: _json(m.items)}
    column_default_sort = (QuestionSet.updated_at, True)
    can_create = can_edit = False


class ApiKeyAdmin(ModelView, model=ApiKey):
    name, name_plural, icon = "API key", "Gemini API keys", "fa-solid fa-key"
    category = "People"
    # The encrypted key itself is never shown.
    column_list = [ApiKey.user, ApiKey.label, ApiKey.last4, ApiKey.is_default, ApiKey.status, ApiKey.created_at,
                   ApiKey.last_checked_at]
    column_details_list = column_list + [ApiKey.last_error]
    column_labels = {ApiKey.last4: "Ends with", ApiKey.is_default: "Default"}
    column_formatters = {ApiKey.created_at: lambda m, a: _dt(m.created_at),
                         ApiKey.last_checked_at: lambda m, a: _dt(m.last_checked_at)}
    can_create = can_edit = can_export = False


_KIND_BADGE = {"bug": ("red", "🐞 Bug"), "idea": ("blue", "💡 Idea"), "praise": ("green", "❤️ Praise"),
               "other": ("secondary", "Other")}
_STATUS_BADGE = {"new": "orange", "read": "blue", "done": "green"}


class FeedbackAdmin(ModelView, model=Feedback):
    name, name_plural, icon = "Feedback", "Feedback", "fa-solid fa-comment-dots"
    category = "Support"
    column_list = [Feedback.created_at, Feedback.user, Feedback.kind, Feedback.rating, Feedback.message,
                   Feedback.status]
    column_details_list = column_list + [Feedback.page, Feedback.interview_id, Feedback.user_agent]
    column_labels = {Feedback.created_at: "When", Feedback.user: "From", Feedback.kind: "Type", Feedback.rating: "Rating",
                     Feedback.page: "Sent from", Feedback.interview_id: "Interview", Feedback.user_agent: "Browser"}
    column_searchable_list = [Feedback.message]
    column_sortable_list = [Feedback.created_at, Feedback.rating, Feedback.status]
    column_default_sort = (Feedback.created_at, True)
    column_filters = [AllUniqueStringValuesFilter(Feedback.kind), AllUniqueStringValuesFilter(Feedback.status)]
    column_formatters = {
        Feedback.created_at: lambda m, a: _dt(m.created_at),
        Feedback.kind: lambda m, a: Markup('<span class="badge bg-{}-lt">{}</span>'.format(
            *_KIND_BADGE.get(m.kind, ("secondary", escape(m.kind))))),
        Feedback.rating: lambda m, a: "★" * m.rating + "☆" * (5 - m.rating) if m.rating else "–",
        Feedback.message: lambda m, a: (m.message[:120] + "…") if len(m.message or "") > 120 else (m.message or "–"),
        Feedback.status: lambda m, a: Markup('<span class="badge bg-{}-lt">{}</span>'.format(
            _STATUS_BADGE.get(m.status, "secondary"), escape(m.status))),
    }
    column_formatters_detail = {Feedback.created_at: lambda m, a: _dt(m.created_at),
                                Feedback.kind: column_formatters[Feedback.kind],
                                Feedback.rating: column_formatters[Feedback.rating],
                                Feedback.status: column_formatters[Feedback.status]}
    # Admins only change the status (new → read → done); what users wrote stays as sent.
    form_columns = [Feedback.status]
    form_overrides = {"status": SelectField}
    form_args = {"status": {"label": "Status",
                            "choices": [("new", "New"), ("read", "Read"), ("done", "Done")]}}
    can_create = can_export = False


# ---------------------------------------------------------------- overview page

class OverviewView(BaseView):
    name, icon = "Overview", "fa-solid fa-gauge-high"

    @expose("/overview", methods=["GET"])
    async def overview(self, request: Request):
        now = datetime.now(timezone.utc)
        day, week = now - timedelta(days=1), now - timedelta(days=7)
        with db_session() as db:
            done = db.query(Interview).filter(Interview.status == "completed")
            stats = {
                "users": db.query(func.count(User.id)).scalar(),
                "new_users_7d": db.query(func.count(User.id)).filter(User.created_at >= week).scalar(),
                "active_24h": db.query(func.count(distinct(LoginEvent.user_id)))
                                .filter(LoginEvent.success.is_(True), LoginEvent.created_at >= day).scalar(),
                "active_7d": db.query(func.count(distinct(LoginEvent.user_id)))
                               .filter(LoginEvent.success.is_(True), LoginEvent.created_at >= week).scalar(),
                "failed_24h": db.query(func.count(LoginEvent.id))
                                .filter(LoginEvent.success.is_(False), LoginEvent.created_at >= day).scalar(),
                "interviews": done.count(),
                "avg_score": db.query(func.avg(Interview.overall_score)).filter(Interview.status == "completed").scalar(),
                "speaking": db.query(func.count(PracticeSession.id)).filter(PracticeSession.status == "completed").scalar(),
                "drills": db.query(func.count(DrillSession.id)).filter(DrillSession.status == "completed").scalar(),
                "talks": db.query(func.count(TalkSession.id)).scalar(),
                "with_key": db.query(func.count(distinct(ApiKey.user_id))).scalar(),
            }
            recent_logins = [{"when": _dt(e.created_at), "email": e.email, "ok": e.success, "kind": e.kind, "ip": e.ip}
                             for e in db.query(LoginEvent).order_by(LoginEvent.created_at.desc()).limit(10)]
            recent_interviews = [{"id": i.id, "who": i.user.name, "email": i.user.email,
                                  "role": ROLE_LABELS.get(i.role, i.role), "score": i.overall_score,
                                  "when": _dt(i.completed_at)}
                                 for i in done.order_by(Interview.completed_at.desc()).limit(10)]
            leaders = [{"name": n, "email": e, "count": c, "avg": a}
                       for n, e, c, a in db.query(User.name, User.email, func.count(Interview.id), func.avg(Interview.overall_score))
                       .join(Interview, Interview.user_id == User.id).filter(Interview.status == "completed")
                       .group_by(User.id).order_by(func.count(Interview.id).desc()).limit(10)]
        return await self.templates.TemplateResponse(request, "overview.html", context={
            "title": "Overview", "subtitle": "Users, logins and practice activity",
            "stats": stats, "recent_logins": recent_logins, "recent_interviews": recent_interviews, "leaders": leaders})


def setup_admin(app) -> Admin:
    admin = Admin(app, engine, title="AI Interview Coach · Admin", base_url="/admin",
                  templates_dir=str(BASE_DIR / "templates" / "admin"), authentication_backend=AdminAuth())
    for view in (OverviewView, UserAdmin, LoginEventAdmin, ApiKeyAdmin, InterviewAdmin, AnswerAdmin,
                 PracticeAdmin, DrillAdmin, TalkAdmin, QuestionSetAdmin, FeedbackAdmin):
        admin.add_view(view)
    return admin
