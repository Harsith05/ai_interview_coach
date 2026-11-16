"""Weak-topic tracker: groups a user's graded interview answers by topic across all interviews.

Each planned question carries a short topic label ("Regularization", "React state"); every graded
answer is stored in answer_results with that topic and a 0-10 grade. Here they are grouped by
topic (case-insensitive) into an average, a trend (recent answers vs. earlier ones) and a status.
"""
from collections import Counter
from statistics import mean

from sqlalchemy.orm import Session

from coach.models import AnswerResult, Interview

SKIP = {"", "general"}          # fallback-bank questions have no real topic
MAX_FOCUS_TOPICS = 3

STATUS = {   # key: (label, badge colour, sort order)
    "weak": ("Weak", "red", 0),
    "needs_work": ("Needs work", "orange", 1),
    "improving": ("Improving", "indigo", 2),
    "strong": ("Strong", "green", 3),
}


def norm(topic: str) -> str:
    return " ".join((topic or "").lower().split())


def topic_stats(db: Session, user_id: int) -> list[dict]:
    """All topics the user has answered, weakest first."""
    rows = (db.query(AnswerResult, Interview.id, Interview.role, Interview.mode)
            .join(Interview, AnswerResult.interview_id == Interview.id)
            .filter(Interview.user_id == user_id, Interview.status == "completed",
                    AnswerResult.grade.isnot(None))
            .order_by(AnswerResult.created_at).all())

    groups: dict[str, dict] = {}
    for ans, interview_id, role, mode in rows:
        r = ans.result or {}
        label = (r.get("topic") or "").strip()
        key = norm(label)
        if key in SKIP:
            continue
        g = groups.setdefault(key, {"scores": [], "roles": Counter(), "modes": Counter(), "interviews": set(),
                                    "missed": [], "improvement": ""})
        g["topic"] = label                       # keep the most recent spelling
        g["scores"].append(float(ans.grade))
        g["roles"][role] += 1
        g["modes"][mode] += 1
        g["interviews"].add(interview_id)
        g["last_seen"] = ans.created_at
        grade = r.get("grade") or {}
        if grade.get("missed_points"):
            g["missed"] = list(grade["missed_points"])[:2]
        if grade.get("improvement"):
            g["improvement"] = grade["improvement"]

    out = []
    for key, g in groups.items():
        s = g["scores"]
        avg = round(mean(s), 1)
        trend = round(mean(s[-2:]) - mean(s[:-2]), 1) if len(s) >= 3 else None
        status = "weak" if avg < 5 else "needs_work" if avg < 7 else "strong"
        if status != "strong" and trend is not None and trend >= 1.5:
            status = "improving"
        out.append({
            "key": key, "topic": g["topic"], "avg": avg, "last": round(s[-1], 1), "count": len(s),
            "trend": trend, "status": status, "status_label": STATUS[status][0], "badge": STATUS[status][1],
            "role": g["roles"].most_common(1)[0][0], "mode": g["modes"].most_common(1)[0][0],
            "interviews": len(g["interviews"]), "last_seen": g["last_seen"],
            "missed": g["missed"], "improvement": g["improvement"],
        })
    return sorted(out, key=lambda t: (STATUS[t["status"]][2], t["avg"], -t["count"]))


def to_practise(stats: list[dict]) -> list[dict]:
    """Topics worth practising (everything that isn't strong), weakest first."""
    return [t for t in stats if t["status"] != "strong"]


def focus_plan(stats: list[dict], keys: list[str]) -> dict | None:
    """Role, mode and topic labels for a practice interview on the chosen topics (max 3)."""
    chosen = [t for t in stats if t["key"] in {norm(k) for k in keys}][:MAX_FOCUS_TOPICS]
    if not chosen:
        return None
    roles = Counter(t["role"] for t in chosen)
    modes = Counter(t["mode"] for t in chosen)
    return {"topics": [t["topic"] for t in chosen], "role": roles.most_common(1)[0][0],
            "mode": modes.most_common(1)[0][0]}
