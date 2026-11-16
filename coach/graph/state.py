import operator
from typing import Annotated, Any, TypedDict


class QuestionItem(TypedDict, total=False):
    question: str
    topic: str
    key_points: list[str]
    ideal_answer: str
    difficulty: str        # easy | medium | hard
    turn: int              # position in the interview, counting follow-ups
    number: int            # main-question number shown to the candidate (1-based)
    is_follow_up: bool


class AnswerSubmission(TypedDict, total=False):
    """What the client sends back when the candidate finishes an answer."""
    webm_path: str | None        # raw browser recording saved by the API layer
    audio_bytes: int
    live_transcript: str         # browser Web Speech transcript (fallback)
    camera_samples: list[dict]   # per-frame results from /api/analyze_frame
    quick_transcript: str        # transcript available at submit time (for adapting the interview)


class AnswerJob(TypedDict):
    """One answer waiting to be analyzed."""
    interview_id: str
    mode: str
    item: QuestionItem
    submission: AnswerSubmission


def merge_results(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    """Reducer: parallel analyses finish in any order; keep one result per turn, sorted."""
    by_turn = {r["turn"]: r for r in (left or []) + (right or [])}
    return [by_turn[i] for i in sorted(by_turn)]


class InterviewState(TypedDict, total=False):
    interview_id: str
    role: str
    mode: str                     # technical | behavioral
    num_questions: int            # main questions (follow-ups come on top)
    resume_text: str
    job_description: str
    seniority: str                # fresher | junior | mid | senior (missing on older interviews)
    focus_topics: list[str]       # weak topics this interview should concentrate on

    bank: dict[str, list[QuestionItem]]   # planned questions by difficulty
    asked: list[str]                      # question texts already used
    difficulty: str                       # level for the next main question
    current: QuestionItem                 # question currently being asked
    turn: int                             # turns asked so far (incl. follow-ups)
    main_asked: int
    follow_ups_used: int
    last_quick_score: float
    next_action: str                      # follow_up | next | finish

    answers: Annotated[list[AnswerJob], operator.add]
    results: Annotated[list[dict[str, Any]], merge_results]

    summary: dict[str, Any]
    feedback: str


class AnswerState(TypedDict, total=False):
    """State of the per-answer analysis subgraph."""
    interview_id: str
    mode: str
    item: QuestionItem
    submission: AnswerSubmission

    wav_path: str | None
    audio_duration: float
    audio_peak_dbfs: float | None
    transcript: str
    words: list
    audio_status: str

    # The analysis branches run in parallel, so each writes its own key.
    content_eval: dict[str, Any]
    grade: dict[str, Any]
    delivery_eval: dict[str, Any]
    body_eval: dict[str, Any]

    result: dict[str, Any]
