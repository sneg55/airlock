"""Strict data models shared by jury implementations and scoring."""

from __future__ import annotations

from pydantic import Field

from src.common.schemas import StrictModel


class JurorDecision(StrictModel):
    safe: bool
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class JurorVote(JurorDecision):
    model: str = Field(min_length=1)


class JuryAssessment(StrictModel):
    score: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    high_risk: bool
    votes: list[JurorVote]
