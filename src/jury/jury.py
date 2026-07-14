"""Live and fake cross-model jury implementations."""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Sequence
from typing import Any, Protocol

from src.common.env import Env, env
from src.common.schemas import ProposedAction
from src.jury.models import JurorDecision, JurorVote, JuryAssessment
from src.jury.scoring import build_assessment

logger = logging.getLogger(__name__)


class JurorModel(Protocol):
    @property
    def model(self) -> str: ...

    def vote(self, action: ProposedAction) -> JurorVote: ...


class Jury(Protocol):
    def assess(self, action: ProposedAction) -> JuryAssessment: ...


class LiveJuror:
    """One OpenAI-compatible chat-completions jury model."""

    def __init__(self, model: str, *, client: Any) -> None:
        self._model = model
        self._client = client

    @property
    def model(self) -> str:
        return self._model

    def vote(self, action: ProposedAction) -> JurorVote:
        operation = action.model_dump(mode="json", exclude={"evidence"})
        payload = {
            "proposed_operation": operation,
            "evidence": action.evidence.model_dump(mode="json"),
        }
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Judge whether the proposed cloud operation is correct and safe given "
                        "only the supplied evidence. Reply with a JSON object of exactly this "
                        'shape: {"safe": boolean, "confidence": number between 0 and 1, '
                        '"rationale": string}.'
                    ),
                },
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        content = response.choices[0].message.content
        if not isinstance(content, str):
            raise ValueError("juror returned no structured content")
        decision = JurorDecision.model_validate_json(content)
        return JurorVote(model=self._model, **decision.model_dump())


class MultiModelJury:
    def __init__(
        self,
        *,
        models: Sequence[str],
        threshold: float = 0.5,
        client: Any,
    ) -> None:
        if not models or any(not model.strip() for model in models):
            raise ValueError("jury models must contain at least one non-empty model name")
        self._jurors: list[JurorModel] = [LiveJuror(model, client=client) for model in models]
        self._threshold = threshold

    @classmethod
    def from_env(cls, settings: Env = env, *, client: Any | None = None) -> MultiModelJury:
        if client is None:
            openai = importlib.import_module("openai")
            client = openai.OpenAI(
                api_key=settings.qwen_api_key.get_secret_value(),
                base_url=settings.qwen_base_url,
            )
        return cls(
            models=settings.jury_models,
            threshold=settings.jury_disagreement_threshold,
            client=client,
        )

    def assess(self, action: ProposedAction) -> JuryAssessment:
        votes: list[JurorVote] = []
        for juror in self._jurors:
            try:
                votes.append(juror.vote(action))
            except Exception as error:
                logger.exception("jury model %s failed; recording unsafe vote", juror.model)
                votes.append(
                    JurorVote(
                        model=juror.model,
                        safe=False,
                        confidence=1.0,
                        rationale=f"Juror failed closed after {type(error).__name__}",
                    )
                )
        return build_assessment(votes, threshold=self._threshold)


class FakeJuror:
    def __init__(self, vote: JurorVote | None = None, *, error: Exception | None = None) -> None:
        if vote is None and error is None:
            raise ValueError("fake juror needs a vote or error")
        self._vote = vote
        self._error = error

    @property
    def model(self) -> str:
        return self._vote.model if self._vote is not None else "failed-fake-juror"

    def vote(self, action: ProposedAction) -> JurorVote:
        del action
        if self._error is not None:
            raise self._error
        if self._vote is None:
            raise RuntimeError("fake juror has no configured vote")
        return self._vote


class FakeJury:
    def __init__(self, votes: Sequence[JurorVote], *, threshold: float = 0.5) -> None:
        self._votes = list(votes)
        self._threshold = threshold
        self.actions: list[ProposedAction] = []

    def assess(self, action: ProposedAction) -> JuryAssessment:
        self.actions.append(action)
        return build_assessment(self._votes, threshold=self._threshold)
