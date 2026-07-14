from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from src.common.canonicalize import action_hash
from src.common.schemas import ProposedAction
from src.jury import (
    FakeJuror,
    JurorVote,
    MultiModelJury,
    enrich_action,
    score_disagreement,
)


def _vote(model: str, *, safe: bool) -> JurorVote:
    return JurorVote(
        model=model,
        safe=safe,
        confidence=0.9,
        rationale=f"{model} says {'safe' if safe else 'unsafe'}",
    )


@pytest.mark.parametrize(
    ("votes", "expected_score", "expected_high_risk"),
    [
        ([_vote("a", safe=True), _vote("b", safe=True)], 0.0, False),
        ([_vote("a", safe=False), _vote("b", safe=False)], 1.0, True),
        ([_vote("a", safe=True), _vote("b", safe=False)], 0.5, True),
    ],
)
def test_scoring_is_deterministic_code(
    votes: list[JurorVote], expected_score: float, expected_high_risk: bool
) -> None:
    first = score_disagreement(votes)
    second = score_disagreement(list(votes))
    assert first == second
    assert first.score == pytest.approx(expected_score)
    assert first.high_risk is expected_high_risk


def test_enrichment_binds_web_readable_shape_before_hashing(action: ProposedAction) -> None:
    jury = MultiModelJury(
        models=["qwen", "deepseek"],
        client=_FakeOpenAI(
            [
                '{"safe":true,"confidence":0.8,"rationale":"metrics support stop"}',
                '{"safe":false,"confidence":0.7,"rationale":"memory is inconclusive"}',
            ]
        ),
    )
    assessment = jury.assess(action)
    enriched = enrich_action(action, assessment)

    assert enriched is not action
    assert action_hash(enriched) != action_hash(action)
    disagreement = enriched.evidence.disagreement
    assert disagreement is not None
    assert set(disagreement) >= {"score", "threshold", "high_risk", "votes"}
    assert disagreement["score"] == 0.5
    assert disagreement["threshold"] == 0.5
    assert disagreement["high_risk"] is True
    votes = disagreement["votes"]
    assert isinstance(votes, list)
    assert votes[1] == {
        "model": "deepseek",
        "safe": False,
        "confidence": 0.7,
        "rationale": "memory is inconclusive",
    }


def test_fake_juror_returns_configured_structured_vote(action: ProposedAction) -> None:
    expected = _vote("fake", safe=True)
    assert FakeJuror(expected).vote(action) == expected


def test_malformed_juror_fails_closed_and_is_logged(
    action: ProposedAction, caplog: pytest.LogCaptureFixture
) -> None:
    client = _FakeOpenAI(
        [
            "not-json",
            '{"safe":true,"confidence":0.9,"rationale":"evidence is sufficient"}',
        ]
    )
    jury = MultiModelJury(models=["broken", "healthy"], client=client)

    with caplog.at_level(logging.ERROR, logger="src.jury.jury"):
        assessment = jury.assess(action)

    assert assessment.score == 0.5
    assert assessment.high_risk is True
    assert assessment.votes[0].model == "broken"
    assert assessment.votes[0].safe is False
    assert "failed closed" in assessment.votes[0].rationale
    assert "jury model broken failed" in caplog.text
    assert [call["temperature"] for call in client.calls] == [0, 0]


class _FakeCompletions:
    def __init__(self, owner: _FakeOpenAI) -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        self._owner.calls.append(kwargs)
        content = self._owner.outputs.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class _FakeOpenAI:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs.copy()
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=_FakeCompletions(self))
