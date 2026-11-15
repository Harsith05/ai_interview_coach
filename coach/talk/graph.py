"""Talk companion: an open-ended daily voice conversation with long-term memory.

    START -> greet (personalized from memory) -> listen (interrupt) -> respond -> listen -> ...
          -> wrap_up (summary + memories saved) -> END    (user says goodbye or presses End)

Memory lives in LangGraph's store (coach.memory), injected into nodes via `store`. Every reply
call also extracts new facts about the user and tags corrections with a mistake type, so the
companion learns without extra LLM calls. One call per user turn, plus one per session
(greeting) and one at the end (summary).
"""
import logging
import operator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from coach import memory
from coach.db import checkpoint_pool, db_session
from coach.models import TalkSession, User
from coach.practice.scenarios import LEVELS
from coach.services.llm import structured

log = logging.getLogger(__name__)

NOT_HEARD = "Sorry, I didn't catch that. Could you say it again?"
HISTORY = 20  # messages of the current session sent to the model


# ---------------------------------------------------------------- LLM schemas

class TalkCorrection(BaseModel):
    original: str = Field(description="The user's exact words containing the mistake.")
    corrected: str = Field(description="Corrected version.")
    explanation: str = Field(description="Very short friendly reason (max 15 words).")
    mistake_type: str = Field(description="Short category, e.g. 'past tense', 'subject-verb agreement', "
                                          "'articles', 'prepositions', 'word choice', 'plurals'.")


class TalkReply(BaseModel):
    reply: str = Field(description="Your spoken reply: 1-3 short, natural sentences. Usually end with a "
                                   "question or comment that keeps the conversation going.")
    correction: TalkCorrection | None = Field(
        default=None, description="Only for a clear grammar or word-choice error in the user's last message. "
                                  "Ignore filler words and speech-recognition glitches.")
    better_way: str | None = Field(default=None, description="Optional more natural way to say the user's "
                                                             "main sentence; omit if it was already natural.")
    new_facts: list[str] = Field(
        default_factory=list,
        description="New, lasting facts the user just revealed about themselves (name, studies, job, family, "
                    "hobbies, goals, plans, preferences, upcoming events), each as a short third-person "
                    "sentence like 'Has a Python exam on Friday'. Empty if nothing new.")
    user_said_goodbye: bool = Field(default=False, description="True if the user is ending the conversation.")


class Greeting(BaseModel):
    text: str = Field(description="A warm, personal 1-2 sentence greeting that refers to something you "
                                  "remember (e.g. ask how a mentioned event went), ending with a question.")


class SessionSummary(BaseModel):
    summary: str = Field(description="1-2 sentence summary of what you talked about, in the third person.")
    topics: list[str] = Field(description="1-4 short topic labels.")
    new_facts: list[str] = Field(default_factory=list, description="Lasting facts about the user not already known.")


# ---------------------------------------------------------------- state

class TalkState(TypedDict, total=False):
    session_id: str
    user_id: int
    messages: Annotated[list[dict[str, str]], operator.add]
    turns: Annotated[list[dict[str, Any]], operator.add]
    user_turns: int
    pending: dict[str, Any]
    goodbye: bool


def _persona(name: str, user_name: str, level: str) -> str:
    style = LEVELS.get(level, LEVELS["intermediate"])["style"]
    return (
        f"You are {name}, {user_name}'s friendly AI companion for daily spoken English practice, like a "
        "supportive friend who is always available. Have natural, interesting conversations about their day, "
        "life, interests, plans and ideas; share brief opinions and ask follow-up questions; refer to things "
        "you remember about them when relevant (naturally, not all at once). Keep replies short so they "
        f"speak more than you. {style} Never use lists, markdown, emojis or stage directions: replies are "
        "read aloud. The user's words come from speech recognition, so ignore filler words and small "
        "recognition errors."
    )


def _user_name(user_id: int) -> str:
    with db_session() as db:
        u = db.get(User, user_id)
        return u.name.split()[0] if u else "the user"


# ---------------------------------------------------------------- nodes

def greet(state: TalkState, *, store: BaseStore) -> dict:
    ctx = memory.load_context(state["user_id"], store)
    s = ctx["settings"]
    user = _user_name(state["user_id"])
    if not ctx["facts"] and not ctx["recent_sessions"]:  # first meeting: no LLM call needed
        text = (f"Hi {user}! I'm {s['name']}, your English practice buddy. You can talk to me about anything. "
                "So, tell me a little about yourself. What do you do?")
    else:
        try:
            g: Greeting = structured(Greeting, speed="realtime").invoke([
                SystemMessage(_persona(s["name"], user, s["level"]) + "\n\n" + memory.context_text(ctx)),
                HumanMessage(f"(Start a new conversation now. Today is {datetime.now():%A, %d %B}.)"),
            ])
            text = g.text
        except Exception as e:
            log.warning("Greeting failed: %s", e)
            text = f"Hey {user}, good to hear from you again! What's new today?"
    return {"messages": [{"role": "ai", "text": text}], "user_turns": 0, "goodbye": False}


def listen(state: TalkState) -> dict:
    last_ai = next(m["text"] for m in reversed(state["messages"]) if m["role"] == "ai")
    last_turn = state["turns"][-1] if state.get("turns") else None
    value = interrupt({
        "type": "ai",
        "text": last_ai,
        "turn": state.get("user_turns", 0) + 1,
        "feedback": {k: last_turn.get(k) for k in ("index", "correction", "better_way")} if last_turn else None,
        "learned": last_turn.get("new_facts", []) if last_turn else [],
    })
    return {"pending": value or {}}


def route_after_listen(state: TalkState) -> str:
    p = state.get("pending", {})
    if p.get("end"):
        return "wrap_up"
    return "respond" if (p.get("text") or "").strip() else "not_heard"


def not_heard(state: TalkState) -> dict:
    return {"messages": [{"role": "ai", "text": NOT_HEARD}]}


def respond(state: TalkState, *, store: BaseStore) -> dict:
    uid = state["user_id"]
    ctx = memory.load_context(uid, store)
    s = ctx["settings"]
    text = state["pending"]["text"].strip()
    turn_no = state.get("user_turns", 0) + 1

    msgs = [SystemMessage(_persona(s["name"], _user_name(uid), s["level"]) + "\n\n" + memory.context_text(ctx))]
    for m in state["messages"][-HISTORY:]:
        msgs.append(AIMessage(m["text"]) if m["role"] == "ai" else HumanMessage(m["text"]))
    msgs.append(HumanMessage(text))
    try:
        out: TalkReply = structured(TalkReply, speed="realtime").invoke(msgs)
    except Exception as e:
        log.warning("Talk reply failed: %s", e)
        out = TalkReply(reply="Sorry, my brain glitched for a second. Could you say that again?")

    # Learn immediately, so nothing is lost if the tab is simply closed.
    if out.new_facts:
        memory.add_facts(uid, out.new_facts, store)
    if out.correction:
        memory.record_mistake(uid, out.correction.mistake_type, out.correction.original,
                              out.correction.corrected, store)

    turn = {"index": turn_no, "text": text, "new_facts": out.new_facts,
            "correction": out.correction.model_dump() if out.correction and s["corrections"] else None,
            "better_way": out.better_way if s["corrections"] else None,
            **(state["pending"].get("fluency") or {})}
    with db_session() as db:
        ts = db.get(TalkSession, state["session_id"])
        if ts is not None:
            ts.user_turns = turn_no
            ts.last_active_at = datetime.now(timezone.utc)
    return {"messages": [{"role": "user", "text": text}, {"role": "ai", "text": out.reply.strip()}],
            "turns": [turn], "user_turns": turn_no, "goodbye": out.user_said_goodbye}


def route_after_respond(state: TalkState) -> str:
    return "wrap_up" if state.get("goodbye") else "listen"


def wrap_up(state: TalkState, *, store: BaseStore) -> dict:
    summarize_session(state["session_id"], state["user_id"], state.get("messages", []), store)
    return {}


def summarize_session(session_id: str, user_id: int, messages: list[dict], store=None) -> None:
    """Save a summary of the session to long-term memory and close it."""
    store = store or memory.get_store()
    summary = None
    if any(m["role"] == "user" for m in messages):
        convo = "\n".join(f"{'Assistant' if m['role'] == 'ai' else 'User'}: {m['text']}" for m in messages[-40:])
        known = memory.load_context(user_id, store)["facts"]
        try:
            out: SessionSummary = structured(SessionSummary, fast=True).invoke([
                SystemMessage("Summarize this English-practice conversation for your long-term memory."),
                HumanMessage(f"Already known facts: {known}\n\nConversation:\n{convo}"),
            ])
            memory.add_session_summary(user_id, session_id, out.summary, out.topics, store)
            memory.add_facts(user_id, out.new_facts, store)
            summary = out.summary
        except Exception as e:
            log.warning("Session summary failed: %s", e)
    with db_session() as db:
        ts = db.get(TalkSession, session_id)
        if ts is not None:
            ts.status = "closed"
            ts.summary = summary


# ---------------------------------------------------------------- graph

def build_talk_graph():
    g = StateGraph(TalkState)
    g.add_node("greet", greet)
    g.add_node("listen", listen)
    g.add_node("not_heard", not_heard)
    g.add_node("respond", respond)
    g.add_node("wrap_up", wrap_up)
    g.add_edge(START, "greet")
    g.add_edge("greet", "listen")
    g.add_conditional_edges("listen", route_after_listen, ["respond", "not_heard", "wrap_up"])
    g.add_edge("not_heard", "listen")
    g.add_conditional_edges("respond", route_after_respond, ["listen", "wrap_up"])
    g.add_edge("wrap_up", END)
    return g.compile(checkpointer=PostgresSaver(checkpoint_pool), store=memory.get_store())


talk_graph = build_talk_graph()
