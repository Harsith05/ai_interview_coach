"""Structured-output schemas for the Gemini calls inside the graphs."""
from typing import Literal

from pydantic import BaseModel, Field


class PlannedQuestion(BaseModel):
    question: str = Field(description="The interview question, phrased as the interviewer would say it aloud.")
    topic: str = Field(description="Short topic label, 1-4 words, e.g. 'Regularization' or 'Conflict resolution'.")
    key_points: list[str] = Field(description="3-5 key points a strong answer should cover.")
    ideal_answer: str = Field(description="A concise model answer (2-4 sentences).")


class QuestionPlan(BaseModel):
    easy: list[PlannedQuestion]
    medium: list[PlannedQuestion]
    hard: list[PlannedQuestion]


class FollowUp(BaseModel):
    question: str = Field(description="One short, natural follow-up question probing what the candidate missed.")
    key_points: list[str] = Field(description="2-4 points a good answer to the follow-up should cover.")


class StarCheck(BaseModel):
    situation: bool
    task: bool
    action: bool
    result: bool


class AnswerGrade(BaseModel):
    accuracy: int = Field(ge=1, le=5, description="Technical/factual correctness (1-5).")
    depth: int = Field(ge=1, le=5, description="Depth and use of examples (1-5).")
    structure: int = Field(ge=1, le=5, description="Logical organization (1-5).")
    communication: int = Field(ge=1, le=5, description="Clarity and conciseness of wording (1-5).")
    overall: float = Field(ge=0, le=10, description="Overall answer score 0-10.")
    covered_points: list[str] = Field(description="Key points the candidate covered.")
    missed_points: list[str] = Field(description="Important key points the candidate missed.")
    strength: str = Field(description="One sentence: what was good.")
    improvement: str = Field(description="One or two sentences: the most useful concrete improvement.")
    star: StarCheck | None = Field(default=None, description="Behavioral questions only: which STAR parts were present.")


class TurnGrade(AnswerGrade):
    turn: int = Field(description="The turn number of the answer being graded, copied from the input.")


class BatchGrades(BaseModel):
    grades: list[TurnGrade] = Field(description="One grade per answer in the input.")


Difficulty = Literal["easy", "medium", "hard"]
