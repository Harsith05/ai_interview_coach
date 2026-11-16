from typing import Literal

from pydantic import BaseModel, Field, field_validator


class FeedbackRequest(BaseModel):
    kind: Literal["bug", "idea", "praise", "other"] = "other"
    rating: int | None = Field(default=None, ge=1, le=5)
    message: str = Field(default="", max_length=2000)
    page: str | None = Field(default=None, max_length=300)
    interview_id: str | None = Field(default=None, max_length=64)

    @field_validator("message")
    @classmethod
    def strip_message(cls, v: str) -> str:
        return v.strip()


class ScoreRequest(BaseModel):
    user_answer: str = ""
    ideal_answers: list[str] = []

    @field_validator("ideal_answers", mode="before")
    @classmethod
    def split_pipe_string(cls, v):
        if isinstance(v, str):
            return [a.strip() for a in v.split("|") if a.strip()]
        return v


class ScoreResponse(BaseModel):
    score: float
    best_match: str
    missing_keywords: list[str]


class FrameRequest(BaseModel):
    image: str  # data URL


class FrameResponse(BaseModel):
    gaze: str
    gaze_ok: bool
    posture_ok: bool
    emotion: str
