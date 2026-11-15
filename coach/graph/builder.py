"""Builds the interview graph.

    START -> plan_questions -> pick_question -> ask_question (interrupt: waits for the answer)
          -> decide_next ─┬─ follow_up   -> generate_follow_up -> ask_question
                          ├─ next        -> pick_question       -> ask_question
                          └─ finish      -> analyze_answer x N (parallel)
                                         -> grade_answers (one LLM call) -> summarize -> generate_feedback -> save_report -> END

Each answer's analysis (transcribe, fluency, body language) is its own subgraph that
starts in the background the moment the answer is submitted (analysis.py, jobs.py).
Checkpoints are stored in Postgres, so an interview can be resumed after a restart.
"""
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph

from coach.db import checkpoint_pool
from coach.graph import nodes
from coach.graph.state import InterviewState


def build_graph(checkpointer=None):
    g = StateGraph(InterviewState)

    g.add_node("plan_questions", nodes.plan_questions)
    g.add_node("pick_question", nodes.pick_question)
    g.add_node("ask_question", nodes.ask_question)
    g.add_node("decide_next", nodes.decide_next)
    g.add_node("generate_follow_up", nodes.generate_follow_up)
    g.add_node("analyze_answer", nodes.analyze_answer)
    g.add_node("grade_answers", nodes.grade_answers)
    g.add_node("summarize", nodes.summarize)
    g.add_node("generate_feedback", nodes.generate_feedback)
    g.add_node("save_report", nodes.save_report)

    g.add_edge(START, "plan_questions")
    g.add_edge("plan_questions", "pick_question")
    g.add_conditional_edges("pick_question", nodes.route_after_pick,
                            ["ask_question", "analyze_answer", "summarize"])
    g.add_edge("ask_question", "decide_next")
    g.add_conditional_edges("decide_next", nodes.route_after_decide,
                            ["generate_follow_up", "pick_question", "analyze_answer", "summarize"])
    g.add_conditional_edges("generate_follow_up", nodes.route_after_follow_up,
                            ["ask_question", "pick_question", "analyze_answer", "summarize"])
    g.add_edge("analyze_answer", "grade_answers")
    g.add_edge("grade_answers", "summarize")
    g.add_edge("summarize", "generate_feedback")
    g.add_edge("generate_feedback", "save_report")
    g.add_edge("save_report", END)

    return g.compile(checkpointer=checkpointer or PostgresSaver(checkpoint_pool))


interview_graph = build_graph()
