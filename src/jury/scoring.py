"""Deterministic jury scoring and proposed-action enrichment."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.common.schemas import Evidence, ProposedAction
from src.jury.models import JurorVote, JuryAssessment


@dataclass(frozen=True, slots=True)
class DisagreementScore:
    score: float
    high_risk: bool


def score_disagreement(votes: Sequence[JurorVote], *, threshold: float = 0.5) -> DisagreementScore:
    """Score the fraction of recorded votes that mark the action unsafe."""

    if not 0.0 <= threshold <= 1.0:
        raise ValueError("disagreement threshold must be between 0 and 1")
    score = sum(not vote.safe for vote in votes) / len(votes) if votes else 1.0
    return DisagreementScore(score=score, high_risk=score >= threshold)


def build_assessment(votes: Sequence[JurorVote], *, threshold: float = 0.5) -> JuryAssessment:
    result = score_disagreement(votes, threshold=threshold)
    return JuryAssessment(
        score=result.score,
        threshold=threshold,
        high_risk=result.high_risk,
        votes=list(votes),
    )


def enrich_action(action: ProposedAction, assessment: JuryAssessment) -> ProposedAction:
    """Return a validated copy with the jury result bound into its evidence."""

    disagreement = assessment.model_dump(mode="json")
    evidence = Evidence.model_validate(
        {**action.evidence.model_dump(mode="json"), "disagreement": disagreement}
    )
    return ProposedAction.model_validate(
        {**action.model_dump(mode="json"), "evidence": evidence.model_dump(mode="json")}
    )
