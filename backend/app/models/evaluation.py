from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.profile import ProfileResult


class SubScore(BaseModel):
    """A single scoring dimension (e.g. 'Role Fit': 85)."""

    name: str
    score: int = Field(ge=0, le=100)


class MatchExplanation(BaseModel):
    match_score: int = Field(ge=0, le=100)
    sub_scores: list[SubScore] = Field(default_factory=list)
    match_reasons: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    summary: str = ""


class EvaluatedProfile(BaseModel):
    profile: ProfileResult
    evaluation: MatchExplanation
    rank: int = 0
