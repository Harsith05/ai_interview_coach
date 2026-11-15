from pydantic import BaseModel, field_validator


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
