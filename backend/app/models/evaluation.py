from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.profile import ProfileResult


class MatchExplanation(BaseModel):
    match_score: int = Field(ge=0, le=100)
    match_reasons: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    summary: str = ""


class EvaluatedProfile(BaseModel):
    profile: ProfileResult
    evaluation: MatchExplanation
    rank: int = 0
