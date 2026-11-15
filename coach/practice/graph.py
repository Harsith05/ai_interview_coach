"""Speaking-practice conversation graph.

    START -> open_conversation -> listen (interrupt: waits for the learner to speak)
          -> respond (AI reply + gentle correction) -> listen -> ...
          -> write_report -> save -> END        (learner ends it, or MAX_TURNS reached)

`listen` is resumed by the API with the learner's transcribed turn. Each learner turn costs
exactly one LLM call (reply and correction come from the same structured call); the final
report is one more.
"""
import logging
import operator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from coach.db import checkpoint_pool, db_session
from coach.models import PracticeSession
from coach.practice.scenarios import LEVELS, MAX_TURNS, SCENARIOS, opener
from coach.services.llm import structured

log = logging.getLogger(__name__)

NOT_HEARD = "Sorry, I didn't quite catch that. Could you say it again?"


# ---------------------------------------------------------------- LLM schemas

class Correction(BaseModel):
    original: str = Field(description="The learner's exact words that contain the mistake.")
    corrected: str = Field(description="The corrected version.")
    explanation: str = Field(description="A very short, friendly reason (max 15 words).")


class TurnReply(BaseModel):
    reply: str = Field(description="Your spoken reply: 1-3 short sentences, natural and warm. Usually end "
                                   "with a question or prompt that keeps the conversation going.")
    correction: Correction | None = Field(
        default=None, description="Only if the learner made a clear grammar or word-choice error. Ignore "
                                  "filler words, minor slips and transcription glitches.")
    better_way: str | None = Field(
        default=None, description="Optional: a more natural or idiomatic way a fluent speaker would say "
                                  "the learner's main sentence. Omit if theirs was already natural.")


class PracticeReport(BaseModel):
    overall_score: float = Field(ge=0, le=10, description="Overall speaking performance 0-10.")
    cefr_level: str = Field(description="Estimated CEFR level, e.g. 'B1'.")
    summary: str = Field(description="2-3 encouraging sentences summarizing the conversation.")
    fluency: str = Field(description="One or two sentences on fluency (pace, pauses, filler words).")
    grammar: str = Field(description="One or two sentences on grammar accuracy.")
    vocabulary: str = Field(description="One or two sentences on vocabulary range.")
    strengths: list[str] = Field(description="2-3 specific strengths.")
    improvements: list[str] = Field(description="2-3 specific, actionable tips.")
    useful_phrases: list[str] = Field(description="3-5 natural phrases for this scenario the learner could use next time.")


# ---------------------------------------------------------------- state

class PracticeState(TypedDict, total=False):
    session_id: str
    scenario: str
    level: str
    messages: Annotated[list[dict[str, str]], operator.add]   # {"role": "ai"|"user", "text"}
    turns: Annotated[list[dict[str, Any]], operator.add]      # analytics per learner turn
    user_turns: int
    pending: dict[str, Any]                                   # last resume value from the API
    report: dict[str, Any]


def _progress(text: str) -> None:
    try:
        get_stream_writer()({"type": "status", "text": text})
    except Exception:
        pass


# ---------------------------------------------------------------- nodes

def open_conversation(state: PracticeState) -> dict:
    return {"messages": [{"role": "ai", "text": opener(state["scenario"])}], "user_turns": 0}


def listen(state: PracticeState) -> dict:
    last_ai = next(m["text"] for m in reversed(state["messages"]) if m["role"] == "ai")
    last_turn = state["turns"][-1] if state.get("turns") else None
    value = interrupt({
        "type": "ai",
        "text": last_ai,
        "turn": state.get("user_turns", 0) + 1,
        "max_turns": MAX_TURNS,
        # feedback on the learner's previous sentence, shown next to it
        "feedback": {k: last_turn.get(k) for k in ("index", "correction", "better_way")} if last_turn else None,
    })
    return {"pending": value or {}}


def route_after_listen(state: PracticeState) -> str:
    p = state.get("pending", {})
    if p.get("end"):
        return "write_report"
    if not (p.get("text") or "").strip():
        return "not_heard"
    return "respond"


def not_heard(state: PracticeState) -> dict:
    return {"messages": [{"role": "ai", "text": NOT_HEARD}]}


def _system_prompt(state: PracticeState, final: bool) -> str:
    sc, lv = SCENARIOS[state["scenario"]], LEVELS[state["level"]]
    prompt = (
        f"You are {sc['role']}. You are helping someone practice spoken English through a natural "
        f"conversation (scenario: {sc['title']}). Stay in character and keep it natural and friendly. "
        f"{lv['style']} Never use lists, markdown, emojis or stage directions: your reply is read aloud. "
        "The learner's words come from speech recognition, so ignore filler words and small recognition errors."
    )
    if final:
        prompt += " This is the last turn: reply briefly and close the conversation warmly, without asking a new question."
    return prompt


def respond(state: PracticeState) -> dict:
    p = state["pending"]
    text = p["text"].strip()
    turn_no = state.get("user_turns", 0) + 1
    final = turn_no >= MAX_TURNS

    history = []
    for m in state["messages"][-16:]:
        history.append(AIMessage(m["text"]) if m["role"] == "ai" else HumanMessage(m["text"]))
    history.append(HumanMessage(text))
    try:
        out: TurnReply = structured(TurnReply, speed="realtime").invoke([SystemMessage(_system_prompt(state, final))] + history)
        reply = out.reply.strip()
        correction = out.correction.model_dump() if out.correction else None
        better_way = out.better_way
    except Exception as e:
        log.warning("Practice reply failed: %s", e)
        reply = "Sorry, I had a little trouble there. Could you tell me more?"
        correction, better_way = None, None

    turn = {"index": turn_no, "text": text, "correction": correction, "better_way": better_way,
            **(p.get("fluency") or {})}
    return {"messages": [{"role": "user", "text": text}, {"role": "ai", "text": reply}],
            "turns": [turn], "user_turns": turn_no}


def route_after_respond(state: PracticeState) -> str:
    return "write_report" if state.get("user_turns", 0) >= MAX_TURNS else "listen"


REPORT_PROMPT = """You are an encouraging English speaking coach. Evaluate the learner's spoken English in \
the conversation below (their turns come from speech recognition, so don't penalize filler words in the \
text itself; use the fluency numbers instead). Be specific and refer to what they actually said."""


def _avg(xs):
    xs = [x for x in xs if x]
    return round(sum(xs) / len(xs), 1) if xs else None


def write_report(state: PracticeState) -> dict:
    turns = state.get("turns", [])
    stats = {
        "turns": len(turns),
        "words": sum(len(t["text"].split()) for t in turns),
        "avg_wpm": _avg([t.get("wpm") for t in turns]),
        "filler_count": sum(t.get("filler_count", 0) for t in turns),
        "filler_per_min": _avg([t.get("filler_per_min") for t in turns if t.get("wpm")]),
        "long_pauses": sum(t.get("long_pauses", 0) for t in turns),
        "corrections": sum(1 for t in turns if t.get("correction")),
    }
    if not turns:
        return {"report": {"stats": stats, "overall_score": None, "summary": "The conversation ended before you spoke."}}

    _progress("Writing your speaking report…")
    convo = "\n".join(f"{'Coach' if m['role'] == 'ai' else 'Learner'}: {m['text']}" for m in state["messages"])
    mistakes = [t["correction"] for t in turns if t.get("correction")]
    try:
        rep: PracticeReport = structured(PracticeReport, fast=True).invoke([
            SystemMessage(REPORT_PROMPT),
            HumanMessage(f"Scenario: {SCENARIOS[state['scenario']]['title']} | Level chosen: {state['level']}\n"
                         f"Fluency stats: {stats}\nCorrections noted during the chat: {mistakes}\n\n"
                         f"Conversation:\n{convo}"),
        ])
        report = rep.model_dump()
    except Exception as e:
        log.warning("Practice report failed: %s", e)
        report = {"overall_score": None, "summary": "The written feedback could not be generated "
                                                    "(Gemini limit or error). Your conversation and stats are saved."}
    report["stats"] = stats
    report["mistakes"] = mistakes
    return {"report": report}


def save(state: PracticeState) -> dict:
    with db_session() as db:
        ps = db.get(PracticeSession, state["session_id"])
        if ps is not None:
            ps.status = "completed"
            ps.user_turns = state.get("user_turns", 0)
            ps.report = state.get("report")
            ps.overall_score = (state.get("report") or {}).get("overall_score")
            ps.transcript = {"messages": state.get("messages", []), "turns": state.get("turns", [])}
            ps.completed_at = datetime.now(timezone.utc)
    return {}


# ---------------------------------------------------------------- graph

def build_practice_graph(checkpointer=None):
    g = StateGraph(PracticeState)
    g.add_node("open_conversation", open_conversation)
    g.add_node("listen", listen)
    g.add_node("not_heard", not_heard)
    g.add_node("respond", respond)
    g.add_node("write_report", write_report)
    g.add_node("save", save)

    g.add_edge(START, "open_conversation")
    g.add_edge("open_conversation", "listen")
    g.add_conditional_edges("listen", route_after_listen, ["respond", "not_heard", "write_report"])
    g.add_edge("not_heard", "listen")
    g.add_conditional_edges("respond", route_after_respond, ["listen", "write_report"])
    g.add_edge("write_report", "save")
    g.add_edge("save", END)
    return g.compile(checkpointer=checkpointer or PostgresSaver(checkpoint_pool))


practice_graph = build_practice_graph()
