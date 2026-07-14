"""Validated planner loop: a reliable deterministic default and an optional Qwen arbiter."""

from __future__ import annotations

import importlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from pydantic import ValidationError

from src.agent.idle_policy import IdleDecision
from src.agent.monitor import InstanceContext
from src.common.env import Env, env
from src.common.schemas import (
    ActionName,
    ActionStage,
    Evidence,
    Precondition,
    ProposedAction,
)

_ACTION_FOR_STAGE = {
    ActionStage.STOP: ActionName.STOP_INSTANCES,
    ActionStage.DELETE: ActionName.DELETE_INSTANCES,
}


@dataclass(frozen=True, slots=True)
class PlannerRequest:
    instance: InstanceContext
    evidence: IdleDecision
    stage: ActionStage
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class PlannerOutcome:
    action: ProposedAction | None
    reason: str

    @property
    def proposed(self) -> bool:
        return self.action is not None


class PlannerModel(Protocol):
    def propose(self, request: PlannerRequest) -> ProposedAction | dict[str, Any] | None: ...


class FakePlannerModel:
    def __init__(self, outputs: list[ProposedAction | dict[str, Any] | None]) -> None:
        self.outputs = outputs.copy()
        self.requests: list[PlannerRequest] = []

    def propose(self, request: PlannerRequest) -> ProposedAction | dict[str, Any] | None:
        self.requests.append(request)
        return self.outputs.pop(0)


class DeterministicPlanner:
    """Assemble a ProposedAction directly from the idle decision, with no LLM.

    This is the reliable default. The idle policy has already made the judgment, so the
    planner only formats it into the canonical action. It needs no API key and cannot
    hallucinate a different target. Layer QwenPlanner on top only to add LLM arbitration.
    """

    def __init__(self, *, idle_window_days: int, schema_version: str = "1") -> None:
        self._idle_window_days = idle_window_days
        self._schema_version = schema_version

    def propose(self, request: PlannerRequest) -> ProposedAction:
        instance = request.instance
        idle = request.evidence
        return ProposedAction(
            schema_version=self._schema_version,
            cloud_account_id=instance.cloud_account_id,
            region=instance.region,
            resource_id=instance.resource_id,
            action=_ACTION_FOR_STAGE[request.stage],
            precondition=Precondition(
                expected_status=instance.status, observed_at=request.observed_at
            ),
            evidence=Evidence(
                idle_window_days=self._idle_window_days,
                cpu_avg=idle.cpu_avg or 0.0,
                cpu_max=idle.cpu_max or 0.0,
                mem_avg=idle.memory_avg or 0.0,
                samples=idle.samples,
                collected_at=request.observed_at,
                monitor_provenance=[{"source": "airlock-monitor", "samples": idle.samples}],
            ),
            policy_reason=idle.reason,
            stage=request.stage,
            created_at=request.observed_at,
        )


class QwenPlanner:
    """Optional Qwen arbiter over DashScope's OpenAI-compatible Chat Completions API.

    DashScope supports chat.completions + JSON mode (`json_object`), not the Responses API
    or `json_schema` strict output, so this uses that path. The idle evidence is passed in
    the prompt (the read MCP has already run), so no hosted tool call is needed. Output is
    re-validated by plan_action, and the executor re-checks trusted fields regardless.
    """

    _SYSTEM = (
        "You arbitrate one Airlock cleanup action. You are given an instance, an idle "
        "decision, and a stage. Return a JSON object matching the ProposedAction schema for "
        'exactly that instance and stage, or {"abstain": true} to decline. Never change the '
        "target, region, account, or action. Never request a write tool."
    )

    def __init__(
        self, *, api_key: str, base_url: str, model: str, client: Any | None = None
    ) -> None:
        if client is None:
            openai = importlib.import_module("openai")
            client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._client: Any = client
        self._model = model

    @classmethod
    def from_env(cls, settings: Env = env) -> QwenPlanner:
        return cls(
            api_key=settings.qwen_api_key.get_secret_value(),
            base_url=settings.qwen_base_url,
            model=settings.qwen_planner_model,
        )

    def propose(self, request: PlannerRequest) -> ProposedAction | dict[str, Any] | None:
        response = self._client.chat.completions.create(
            model=self._model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self._SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"request": asdict(request), "schema": ProposedAction.model_json_schema()},
                        default=str,
                    ),
                },
            ],
        )
        content = response.choices[0].message.content
        if not isinstance(content, str):
            return None
        decoded = json.loads(content)
        if not isinstance(decoded, dict):
            return None
        obj = cast(dict[str, Any], decoded)
        return None if obj.get("abstain") is True else obj


def plan_action(model: PlannerModel, request: PlannerRequest) -> PlannerOutcome:
    """Validate model output and refuse any target, stage, or action mismatch."""

    if not request.evidence.should_propose:
        return PlannerOutcome(None, f"policy abstained: {request.evidence.reason}")
    try:
        raw = model.propose(request)
        action = raw if isinstance(raw, ProposedAction) else ProposedAction.model_validate(raw)
    except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as error:
        return PlannerOutcome(None, f"malformed planner output: {type(error).__name__}")
    expected_action = {
        ActionStage.STOP: ActionName.STOP_INSTANCES,
        ActionStage.DELETE: ActionName.DELETE_INSTANCES,
    }[request.stage]
    instance = request.instance
    if (
        action.stage is not request.stage
        or action.action is not expected_action
        or action.resource_id != instance.resource_id
        or action.region != instance.region
        or action.cloud_account_id != instance.cloud_account_id
    ):
        return PlannerOutcome(None, "planner output changed the requested operation or target")
    if action.precondition.expected_status != instance.status:
        return PlannerOutcome(None, "planner precondition does not match observed state")
    return PlannerOutcome(action, "valid proposed action")
