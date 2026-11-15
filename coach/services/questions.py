"""Fallback question bank (used when the LLM question planner is unavailable)."""
import csv
import logging
import random

from coach.config import DATA_DIR, DOMAIN_FILES

log = logging.getLogger(__name__)

BEHAVIORAL = [
    ("Tell me about a time you faced a conflict with a teammate. How did you handle it?", "Conflict resolution"),
    ("Describe a project that failed or didn't go as planned. What did you learn?", "Learning from failure"),
    ("Tell me about a time you had to meet a very tight deadline.", "Time management"),
    ("Give an example of when you took initiative beyond your assigned responsibilities.", "Initiative"),
    ("Describe a situation where you had to explain a complex topic to a non-technical person.", "Communication"),
    ("Tell me about a time you received critical feedback. How did you respond?", "Receiving feedback"),
    ("Describe a time you had to make a decision with incomplete information.", "Decision making"),
    ("Tell me about your proudest professional or academic achievement.", "Achievement"),
]
STAR_POINTS = ["Situation: sets the context", "Task: states their responsibility",
               "Action: describes the specific steps they took", "Result: shares a measurable outcome or lesson"]


def _csv_questions(role: str) -> list[dict]:
    path = DOMAIN_FILES.get(role.lower())
    qa: list[dict] = []
    if path is None:
        return qa
    try:
        with open(path, "r", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) < 2 or row[0].strip().lower() == "question":
                    continue
                answers = [a.strip() for a in row[1].split("|") if a.strip()]
                if row[0].strip() and answers:
                    # The CSV holds alternative full answers, not key points.
                    qa.append({"question": row[0].strip(), "topic": "General", "key_points": answers,
                               "ideal_answer": answers[0], "alt_answers": True})
    except FileNotFoundError:
        log.warning("Question file not found: %s", path)
    return qa


def _role_behavioral(role: str) -> list[dict]:
    """Role-specific STAR questions from data/behavioral/<role>.csv (question, topic, good_answer)."""
    path = DATA_DIR / "behavioral" / f"{role.lower()}.csv"
    qs: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                q, good = (row.get("question") or "").strip(), (row.get("good_answer") or "").strip()
                if q:
                    qs.append({"question": q, "topic": (row.get("topic") or "").strip() or "Behavioral",
                               "key_points": STAR_POINTS,
                               "ideal_answer": f"A structured STAR answer. {good}".strip()})
    except FileNotFoundError:
        log.info("No role-specific behavioral questions for %s", role)
    return qs


def fallback_plan(role: str, mode: str) -> dict[str, list[dict]]:
    """Same shape as the LLM plan; the static banks have no difficulty, so all go to 'medium'."""
    if mode == "behavioral":
        # Role-specific questions are asked first (questions are picked in order), then general ones.
        specific = _role_behavioral(role)
        seen = {q["question"].lower() for q in specific}
        general = [{"question": q, "topic": t, "key_points": STAR_POINTS,
                    "ideal_answer": "A structured STAR answer with a concrete, measurable result."}
                   for q, t in BEHAVIORAL if q.lower() not in seen]
        random.shuffle(specific)
        random.shuffle(general)
        qs = specific + general
    else:
        qs = _csv_questions(role)
        random.shuffle(qs)
    return {"easy": [], "medium": qs, "hard": []}
