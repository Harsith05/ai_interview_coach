"""Nodes of the interview graph (see builder.py for the wiring)."""
import logging
import math
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.types import Send, interrupt

from coach.config import MAX_FOLLOW_UPS, ROLE_LABELS
from coach.db import db_session
from coach.graph import jobs
from coach.graph.llm_schemas import BatchGrades, FollowUp, QuestionPlan
from coach.graph.state import AnswerJob, InterviewState, QuestionItem
from coach.models import AnswerResult, Interview
from coach.services import questions, scoring
from coach.services.llm import clean_html, get_llm, structured

log = logging.getLogger(__name__)

LEVELS = ["easy", "medium", "hard"]


def _progress(text: str) -> None:
    try:
        get_stream_writer()({"type": "status", "text": text})
    except Exception:
        pass  # not streaming


# ---------------------------------------------------------------- planning

PLANNER_PROMPT = """You are an experienced interviewer preparing a mock interview.
Write interview questions the interviewer will read ALOUD, so keep each one to one or two sentences, \
no code blocks, no lists inside the question. Questions within a level must cover different topics.
- easy: fundamentals and definitions
- medium: applied understanding, trade-offs, "how would you..."
- hard: deep dives, edge cases, system/design-level reasoning
If a resume is given, tailor several questions to the candidate's actual projects and skills. \
If a job description is given, focus on the skills it asks for."""


def plan_questions(state: InterviewState) -> dict:
    role, mode = state["role"], state.get("mode", "technical")
    per_level = max(2, math.ceil(state.get("num_questions", 5) * 0.7))
    _progress("Preparing questions tailored to you…")

    role_label = ROLE_LABELS.get(role, role)
    behavioral = (
        "behavioral (questions answerable with the STAR method; key points should be the STAR elements "
        f"specific to the question). Set most questions in situations a {role_label} typically faces at work "
        "(for example their kind of deadlines, incidents, trade-offs, stakeholders and technical disagreements), "
        "not generic office scenarios; at most one question per level may be general (teamwork, conflict, feedback). "
        "Do not ask technical knowledge questions."
    )
    parts = [
        f"Role: {role_label}",
        f"Interview type: {behavioral if mode == 'behavioral' else 'technical'}",
        f"Write exactly {per_level} questions for each level: easy, medium, hard.",
    ]
    if state.get("resume_text"):
        parts.append(f"Candidate resume:\n{state['resume_text']}")
    if state.get("job_description"):
        parts.append(f"Job description:\n{state['job_description']}")

    bank = None
    try:
        planner = structured(QuestionPlan, fast=True)
        plan: QuestionPlan = planner.invoke([SystemMessage(PLANNER_PROMPT), HumanMessage("\n\n".join(parts))])
        bank = {lvl: [{**q.model_dump(), "difficulty": lvl} for q in getattr(plan, lvl)] for lvl in LEVELS}
        if not any(bank.values()):
            bank = None
    except Exception as e:
        log.warning("Question planning failed, using the static bank: %s", e)

    if bank is None:
        bank = questions.fallback_plan(role, mode)
        for lvl in LEVELS:
            for q in bank[lvl]:
                q["difficulty"] = lvl

    return {"bank": bank, "asked": [], "difficulty": "medium", "turn": 0, "main_asked": 0,
            "follow_ups_used": 0, "answers": [], "results": []}


def pick_question(state: InterviewState) -> dict:
    """Next unused question at the current difficulty (or the nearest level that has one)."""
    bank, asked = state["bank"], set(state.get("asked", []))
    lvl = state.get("difficulty", "medium")
    order = sorted(LEVELS, key=lambda l: abs(LEVELS.index(l) - LEVELS.index(lvl)))
    for level in order:
        for q in bank.get(level, []):
            if q["question"] not in asked:
                item: QuestionItem = {**q, "difficulty": level, "turn": state.get("turn", 0),
                                      "number": state.get("main_asked", 0) + 1, "is_follow_up": False}
                return {"current": item, "next_action": "ask"}
    return {"next_action": "finish"}  # bank exhausted


# ---------------------------------------------------------------- asking

def ask_question(state: InterviewState) -> dict:
    """Pause until the API resumes the graph with the candidate's answer.

    The answer's full analysis is already running in the background (coach.graph.jobs);
    `quick_transcript` in the submission is what we use to adapt the interview right now.
    """
    cur = state["current"]
    submission = interrupt({
        "type": "question",
        "turn": cur["turn"],
        "number": cur["number"],
        "total": state["num_questions"],
        "question": cur["question"],
        "topic": cur.get("topic", ""),
        "difficulty": cur.get("difficulty"),
        "is_follow_up": cur.get("is_follow_up", False),
        "key_points": cur.get("key_points", []),
    }) or {}
    job: AnswerJob = {"interview_id": state["interview_id"], "mode": state.get("mode", "technical"),
                      "item": cur, "submission": submission}
    return {
        "answers": [job],
        "asked": state.get("asked", []) + [cur["question"]],
        "turn": state.get("turn", 0) + 1,
        "main_asked": state.get("main_asked", 0) + (0 if cur.get("is_follow_up") else 1),
    }


def decide_next(state: InterviewState) -> dict:
    """Follow up, go harder/easier, or finish — based on a quick look at the answer."""
    cur = state["current"]
    text = (state["answers"][-1]["submission"].get("quick_transcript") or "").strip()
    n_words = len(text.split())
    behavioral = state.get("mode") == "behavioral"

    if behavioral:
        # Similarity to STAR bullet points says little; use answer length as the signal.
        quick = min(1.0, n_words / 120)
        partial = 8 <= n_words < 70
        strong, weak = n_words >= 90, n_words < 30
    elif cur.get("alt_answers"):
        # Static-bank question: "key points" are alternative full answers -> best-match similarity.
        quick, _, _ = scoring.similarity_score(text, cur.get("key_points", [])) if text else (0.0, "", [])
        on_topic = quick >= 0.45
        strong = quick >= 0.75
        weak = not on_topic or n_words < 8
        partial = on_topic and not strong and n_words >= 8
    else:
        # quick = share of key points covered; best = closest match to any single key point.
        quick, best = scoring.key_point_coverage(text, cur.get("key_points", [])) if text else (0.0, 0.0)
        on_topic = best >= 0.45  # off-topic answers still reach ~0.4 with this embedding model
        strong = on_topic and quick >= 0.6
        weak = not on_topic or n_words < 8
        partial = on_topic and not strong and n_words >= 8

    update: dict = {"last_quick_score": round(quick, 3)}
    if not cur.get("is_follow_up"):
        i = LEVELS.index(state.get("difficulty", "medium"))
        if strong:
            i = min(i + 1, 2)
        elif weak:
            i = max(i - 1, 0)
        update["difficulty"] = LEVELS[i]

    if not cur.get("is_follow_up") and partial and state.get("follow_ups_used", 0) < MAX_FOLLOW_UPS:
        update["next_action"] = "follow_up"
    elif state.get("main_asked", 0) >= state["num_questions"]:
        update["next_action"] = "finish"
    else:
        update["next_action"] = "next"
    log.info("Turn %d: quick score %.2f (%d words) -> %s, next difficulty %s", cur["turn"] + 1, quick,
             n_words, update["next_action"], update.get("difficulty", state.get("difficulty")))
    return update


FOLLOW_UP_PROMPT = """You are a friendly but probing interviewer. The candidate's answer was partly right. \
Ask ONE short, natural follow-up question (one sentence, said aloud) that gives them a chance to cover the \
most important thing they missed. Do not reveal the answer. Do not repeat the original question."""


def generate_follow_up(state: InterviewState) -> dict:
    cur = state["current"]
    transcript = state["answers"][-1]["submission"].get("quick_transcript", "")
    try:
        fu: FollowUp = structured(FollowUp, fast=True).invoke([
            SystemMessage(FOLLOW_UP_PROMPT),
            HumanMessage(f"Original question: {cur['question']}\n"
                         f"Key points of a strong answer: {'; '.join(cur.get('key_points', []))}\n"
                         f"Candidate's answer: {transcript}"),
        ])
    except Exception as e:
        log.warning("Follow-up generation failed: %s", e)
        done = state.get("main_asked", 0) >= state["num_questions"]
        return {"next_action": "finish" if done else "next"}
    item: QuestionItem = {"question": fu.question, "topic": cur.get("topic", ""), "key_points": fu.key_points,
                          "ideal_answer": "", "difficulty": cur.get("difficulty"), "turn": state["turn"],
                          "number": cur["number"], "is_follow_up": True}
    return {"current": item, "follow_ups_used": state.get("follow_ups_used", 0) + 1, "next_action": "ask"}


# ---------------------------------------------------------------- routing

def _analyze_all(state: InterviewState):
    jobs_ = state.get("answers", [])
    return [Send("analyze_answer", j) for j in jobs_] if jobs_ else "summarize"


def route_after_pick(state: InterviewState):
    return "ask_question" if state.get("next_action") == "ask" else _analyze_all(state)


def route_after_decide(state: InterviewState):
    action = state.get("next_action")
    if action == "follow_up":
        return "generate_follow_up"
    if action == "next":
        return "pick_question"
    return _analyze_all(state)


def route_after_follow_up(state: InterviewState):
    action = state.get("next_action")
    if action == "ask":
        return "ask_question"
    if action == "next":
        return "pick_question"
    return _analyze_all(state)


# ---------------------------------------------------------------- report

def analyze_answer(job: AnswerJob) -> dict:
    """Pick up the background result (waiting only if it's still running)."""
    result = jobs.collect(job["interview_id"], job["mode"], job["item"], job["submission"])
    return {"results": [result]}


GRADER_PROMPT = """You are a strict but fair interview grader. Grade each of the candidate's spoken \
answers below (automatic transcripts, so ignore minor transcription errors and filler words). Base \
each grade only on what the candidate actually said in that answer. Off-topic answers get 1s and an \
overall score of 0-1. For behavioral answers, fill the `star` field; for technical answers leave it empty. \
Return exactly one grade per answer, with its turn number."""

_EMPTY_GRADE = {"overall": 0.0, "accuracy": 1, "depth": 1, "structure": 1, "communication": 1,
                "covered_points": [], "strength": "", "improvement": "Give a spoken answer, even a partial one."}


def grade_answers(state: InterviewState) -> dict:
    """Grade all answers in ONE LLM call (Gemini's free tier allows very few requests per day)."""
    _progress("Grading your answers…")
    results = state.get("results", [])
    behavioral = state.get("mode") == "behavioral"
    graded: dict[int, dict] = {}
    blocks = []
    for r in results:
        if not (r.get("transcript") or "").strip():
            graded[r["turn"]] = {**_EMPTY_GRADE, "missed_points": r.get("key_points", []), "star": None}
            continue
        blocks.append(
            f"### turn {r['turn']} ({'behavioral' if behavioral else 'technical'}"
            f"{', follow-up question' if r.get('is_follow_up') else ''})\n"
            f"Question: {r['q']}\nKey points of a strong answer: {'; '.join(r.get('key_points', []))}\n"
            f"Model answer: {r.get('ideal', '')}\nCandidate's answer: {r['transcript']}"
        )
    if blocks:
        try:
            out: BatchGrades = structured(BatchGrades).invoke(
                [SystemMessage(GRADER_PROMPT), HumanMessage("\n\n".join(blocks))])
            for g in out.grades:
                d = g.model_dump()
                turn = d.pop("turn")
                if not behavioral:
                    d["star"] = None
                graded[turn] = d
        except Exception as e:
            log.warning("LLM grading failed: %s", e)

    updated = [{**r, "grade": graded.get(r["turn"], r.get("grade") or {})} for r in results]
    with db_session() as db:  # keep the per-answer rows in sync for the dashboard
        for r in updated:
            row = db.query(AnswerResult).filter_by(interview_id=state["interview_id"], turn=r["turn"]).first()
            if row is not None:
                row.result = r
                row.grade = (r.get("grade") or {}).get("overall")
    return {"results": updated}


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


def summarize(state: InterviewState) -> dict:
    _progress("Summarizing your interview…")
    results = state.get("results", [])
    grades = [r.get("grade") or {} for r in results]
    spoken = [r for r in results if r.get("transcript")]
    topic_scores: dict[str, list[float]] = {}
    for r in results:
        g = (r.get("grade") or {}).get("overall")
        if g is not None and r.get("topic"):
            topic_scores.setdefault(r["topic"], []).append(g)
    weakest = sorted(((t, _avg(s)) for t, s in topic_scores.items()), key=lambda x: x[1])[:3]
    return {"summary": {
        "questions_answered": len(results),
        "follow_ups": sum(1 for r in results if r.get("is_follow_up")),
        "overall_score": _avg([g.get("overall") for g in grades]),
        "accuracy": _avg([g.get("accuracy") for g in grades]),
        "depth": _avg([g.get("depth") for g in grades]),
        "structure": _avg([g.get("structure") for g in grades]),
        "communication": _avg([g.get("communication") for g in grades]),
        "avg_similarity": _avg([r.get("score") for r in results]),
        "avg_wpm": _avg([r.get("wpm") for r in spoken if r.get("wpm")]),
        "filler_per_min": _avg([r.get("filler_per_min") for r in spoken if r.get("wpm")]),
        "total_fillers": sum(r.get("filler_count", 0) for r in results),
        "long_pauses": sum(r.get("long_pauses", 0) for r in results),
        "avg_gaze_pct": _avg([r.get("gaze_pct") for r in results]),
        "avg_posture_pct": _avg([r.get("posture_pct") for r in results]),
        "final_difficulty": state.get("difficulty"),
        "weakest_topics": [{"topic": t, "score": s} for t, s in weakest],
    }}


REPORT_PROMPT = """You are an AI Interview Coach. Write a detailed, encouraging but honest interview report \
as clean HTML (use only <h3>, <h4>, <p>, <ul>, <li>, <strong>, <em>; no <html>/<body>, no markdown, no code fences).

Use the per-answer grades (0-10 and 1-5 rubric) as the main evidence. For every question: answer quality, \
what was covered / missed, delivery (pace, filler words, pauses, tone), and body language. Mention when \
a follow-up question was asked and how the candidate handled it. For behavioral questions, comment on STAR structure.
Then give: an overall rating out of 10 (consistent with the grades), strengths, weaknesses, and a 5-step \
improvement plan.
If the audio capture status shows a technical problem (no audio, silent recording), say so clearly at the top \
as a technical issue rather than judging the candidate's knowledge."""


def generate_feedback(state: InterviewState) -> dict:
    results = state.get("results", [])
    if not results:
        return {"feedback": "<p>No answers were recorded.</p>"}
    _progress("Writing your feedback report…")
    s = state.get("summary", {})
    lines = [f"Role: {ROLE_LABELS.get(state.get('role'), state.get('role'))} | Interview type: {state.get('mode')}",
             f"Summary metrics: {s}", ""]
    for r in results:
        g = r.get("grade") or {}
        label = f"Q{r.get('number')}{' (follow-up)' if r.get('is_follow_up') else ''} [{r.get('difficulty')}]"
        lines += [
            f"{label}: {r.get('q')}",
            f"Answer: {r.get('transcript') or '(no speech detected)'}",
            f"Audio capture status: {r.get('audio_status')}",
            f"Grade: overall {g.get('overall')}/10; accuracy {g.get('accuracy')}, depth {g.get('depth')}, "
            f"structure {g.get('structure')}, communication {g.get('communication')} (1-5)",
            f"Covered: {g.get('covered_points')} | Missed: {g.get('missed_points')}",
            f"STAR: {g.get('star')}" if g.get("star") else "",
            f"Delivery: {r.get('wpm')} WPM, fillers {r.get('fillers')}, long pauses {r.get('long_pauses')}, "
            f"took {r.get('response_delay')}s to start; tone: {r.get('tone_notes')}",
            f"Camera: {r.get('camera_notes')}",
            "----------------------------",
        ]
    try:
        resp = get_llm().invoke([SystemMessage(REPORT_PROMPT), HumanMessage("\n".join(l for l in lines if l))])
        feedback = clean_html(resp.text)
    except Exception as e:
        log.exception("LLM feedback failed")
        feedback = REPORT_FAILED_HTML + (
            "<p>Gemini's usage limit was reached (the free tier allows only a few requests per day).</p>"
            if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e) else f"<p>Error: {e}</p>")
    return {"feedback": feedback}


REPORT_FAILED_HTML = ('<p data-report-failed><strong>The written report could not be generated.</strong> '
                      'Your answers and metrics are saved. Use <em>Regenerate report</em> to try again later.</p>')


def regenerate_report(interview_id: str) -> None:
    """Re-run grading (for ungraded answers) and the written report for a finished interview."""
    with db_session() as db:
        iv = db.get(Interview, interview_id)
        state = {"interview_id": iv.id, "role": iv.role, "mode": iv.mode,
                 "results": [a.result for a in iv.answers], "difficulty": (iv.summary or {}).get("final_difficulty")}
    if any(not (r.get("grade") or {}).get("accuracy") and r.get("transcript") for r in state["results"]):
        state["results"] = grade_answers(state)["results"]
    state.update(summarize(state))
    state.update(generate_feedback(state))
    with db_session() as db:
        iv = db.get(Interview, interview_id)
        iv.summary = state["summary"]
        iv.overall_score = state["summary"].get("overall_score")
        iv.feedback_html = state["feedback"]


def save_report(state: InterviewState) -> dict:
    s = state.get("summary", {})
    with db_session() as db:
        iv = db.get(Interview, state["interview_id"])
        if iv is not None:
            iv.status = "completed"
            iv.overall_score = s.get("overall_score")
            iv.summary = s
            iv.feedback_html = state.get("feedback", "")
            iv.completed_at = datetime.now(timezone.utc)
    jobs.forget(state["interview_id"])
    return {}
