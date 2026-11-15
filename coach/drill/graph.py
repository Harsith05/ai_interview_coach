"""Question practice ("drill"): the AI asks the user's own questions, one by one, by voice.

    START -> intro -> ask (interrupt: waits for the answer)
          -> evaluate (feedback_mode "each": spoken feedback + score per answer, 1 LLM call)
          -> ask (next question) ... -> finish (mode "end": all answers graded in 1 LLM call)
          -> save -> END

The user can skip a question, or end early; unanswered questions are left out of the score.
"""
import logging
import operator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.config import get_stream_writer
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from coach.db import checkpoint_pool, db_session
from coach.models import DrillSession
from coach.services.llm import structured

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- LLM schemas

class AnswerFeedback(BaseModel):
    spoken_feedback: str = Field(description="1-2 short, warm, specific sentences said aloud right after the "
                                             "answer: what was good and the single most useful improvement. "
                                             "No lists, no markdown.")
    score: float = Field(ge=0, le=10, description="Answer quality 0-10 (content, structure, clarity).")
    covered: list[str] = Field(default_factory=list, description="Key points the answer covered.")
    missed: list[str] = Field(default_factory=list, description="Important points the answer missed.")
    better_answer: str = Field(description="A concise model answer (2-4 sentences) the user could give.")
    language_tip: str | None = Field(default=None, description="Optional: one English grammar/phrasing fix "
                                                               "from the answer, as 'X → Y'. Omit if none.")


class GradedAnswer(AnswerFeedback):
    number: int = Field(description="The question number, copied from the input.")


class BatchFeedback(BaseModel):
    answers: list[GradedAnswer]


GRADER = """You are a supportive interview and speaking coach. The user is practising answers to their own \
question list by speaking (automatic transcript: ignore filler words and small recognition errors). \
If a reference answer is given, use it as the target; otherwise use your own knowledge. Judge content \
first, then structure and clarity. Be encouraging but honest."""


# ---------------------------------------------------------------- state

class DrillState(TypedDict, total=False):
    session_id: str
    title: str
    feedback_mode: str               # each | end
    items: list[dict]                # [{"question", "answer"}] in asking order
    idx: int                         # index of the current question
    results: Annotated[list[dict[str, Any]], operator.add]
    pending: dict[str, Any]
    lead_in: str                     # what to say before the next question (feedback / "let's skip")
    report: dict[str, Any]


def _progress(text: str) -> None:
    try:
        get_stream_writer()({"type": "status", "text": text})
    except Exception:
        pass


# ---------------------------------------------------------------- nodes

def intro(state: DrillState) -> dict:
    n = len(state["items"])
    return {"idx": 0, "lead_in": f"Let's practise {state['title']}. I'll ask you {n} question{'s' if n != 1 else ''}. "
                                 "Take your time, and pause when you're done. First question.",
            "results": []}


def ask(state: DrillState) -> dict:
    i = state["idx"]
    q = state["items"][i]["question"]
    last = state["results"][-1] if state.get("results") else None
    value = interrupt({
        "type": "question",
        "number": i + 1,
        "total": len(state["items"]),
        "question": q,
        "speech": f"{state.get('lead_in', '')} {q}".strip(),
        "feedback": last if last and last.get("number") == i else None,   # feedback on the previous question
    })
    return {"pending": value or {}}


def route_after_ask(state: DrillState) -> str:
    p = state.get("pending", {})
    if p.get("end"):
        return "finish"
    if p.get("skip"):
        return "skip"
    if not (p.get("text") or "").strip():
        return "not_heard"
    return "evaluate"


def not_heard(state: DrillState) -> dict:
    return {"lead_in": "Sorry, I didn't catch that. Here's the question again."}


def skip(state: DrillState) -> dict:
    i = state["idx"]
    return {"results": [{"number": i + 1, "question": state["items"][i]["question"], "skipped": True}],
            "idx": i + 1, "lead_in": "Okay, let's skip that one. Next question."}


def evaluate(state: DrillState) -> dict:
    i = state["idx"]
    item = state["items"][i]
    p = state["pending"]
    result = {"number": i + 1, "question": item["question"], "reference": item.get("answer", ""),
              "answer": p["text"].strip(), "fluency": p.get("fluency") or {}}
    lead_in = "Thank you. Next question." if i + 1 < len(state["items"]) else "Thank you."
    if state.get("feedback_mode", "each") == "each":
        try:
            fb: AnswerFeedback = structured(AnswerFeedback, speed="realtime").invoke([
                SystemMessage(GRADER),
                HumanMessage(f"Question: {item['question']}\n"
                             f"Reference answer: {item.get('answer') or '(none given)'}\n\n"
                             f"User's spoken answer: {result['answer']}"),
            ])
            result.update(fb.model_dump())
            lead_in = fb.spoken_feedback + (" Next question." if i + 1 < len(state["items"]) else "")
        except Exception as e:
            log.warning("Drill feedback failed: %s", e)
    return {"results": [result], "idx": i + 1, "lead_in": lead_in}


def route_next(state: DrillState) -> str:
    return "ask" if state["idx"] < len(state["items"]) else "finish"


def finish(state: DrillState) -> dict:
    results = [dict(r) for r in state.get("results", [])]
    answered = [r for r in results if not r.get("skipped")]

    # "end" mode (or answers whose live feedback failed): grade everything in one call.
    ungraded = [r for r in answered if r.get("score") is None]
    if ungraded:
        _progress("Reviewing your answers…")
        blocks = "\n\n".join(f"### Question {r['number']}: {r['question']}\nReference answer: "
                             f"{r.get('reference') or '(none given)'}\nUser's spoken answer: {r['answer']}"
                             for r in ungraded)
        try:
            out: BatchFeedback = structured(BatchFeedback, fast=True).invoke(
                [SystemMessage(GRADER + " Grade every answer below and return one entry per question."),
                 HumanMessage(blocks)])
            by_num = {g.number: g.model_dump() for g in out.answers}
            for r in ungraded:
                g = by_num.get(r["number"])
                if g:
                    g.pop("number", None)
                    r.update(g)
        except Exception as e:
            log.warning("Drill batch grading failed: %s", e)

    scored = [r for r in answered if r.get("score") is not None]
    wpms = [r["fluency"].get("wpm") for r in answered if r.get("fluency", {}).get("wpm")]
    report = {
        "asked": len(results),
        "answered": len(answered),
        "skipped": len(results) - len(answered),
        "total": len(state["items"]),
        "overall_score": round(sum(r["score"] for r in scored) / len(scored), 1) if scored else None,
        "avg_wpm": round(sum(wpms) / len(wpms)) if wpms else None,
        "filler_count": sum(r.get("fluency", {}).get("filler_count", 0) for r in answered),
        "strongest": [r["number"] for r in sorted(scored, key=lambda r: -r["score"])[:2]],
        "review": [r["number"] for r in sorted(scored, key=lambda r: r["score"]) if r["score"] < 6][:5],
    }
    closing = "That's the end of the set. Well done!" if report["answered"] else "Okay, we'll stop here."
    if report["overall_score"] is not None:
        closing += f" Your average score was {report['overall_score']} out of 10."
    report["closing"] = closing
    return {"results": [], "report": {**report, "results": results}}


def save(state: DrillState) -> dict:
    rep = state.get("report", {})
    with db_session() as db:
        ds = db.get(DrillSession, state["session_id"])
        if ds is not None:
            ds.status = "completed"
            ds.results = rep.get("results", [])
            ds.report = {k: v for k, v in rep.items() if k != "results"}
            ds.overall_score = rep.get("overall_score")
            ds.completed_at = datetime.now(timezone.utc)
    return {}


# ---------------------------------------------------------------- graph

def build_drill_graph():
    g = StateGraph(DrillState)
    for name, fn in [("intro", intro), ("ask", ask), ("not_heard", not_heard), ("skip", skip),
                     ("evaluate", evaluate), ("finish", finish), ("save", save)]:
        g.add_node(name, fn)
    g.add_edge(START, "intro")
    g.add_edge("intro", "ask")
    g.add_conditional_edges("ask", route_after_ask, ["evaluate", "skip", "not_heard", "finish"])
    g.add_edge("not_heard", "ask")
    g.add_conditional_edges("skip", route_next, ["ask", "finish"])
    g.add_conditional_edges("evaluate", route_next, ["ask", "finish"])
    g.add_edge("finish", "save")
    g.add_edge("save", END)
    return g.compile(checkpointer=PostgresSaver(checkpoint_pool))


drill_graph = build_drill_graph()
