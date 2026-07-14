from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from src.agent.idle_policy import IdleDecision, IdleDisposition
from src.agent.monitor import InstanceContext
from src.agent.planner import (
    DeterministicPlanner,
    FakePlannerModel,
    PlannerRequest,
    QwenPlanner,
    plan_action,
)
from src.common.schemas import ActionName, ActionStage, ProposedAction

NOW = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)


def _request() -> PlannerRequest:
    return PlannerRequest(
        InstanceContext("account-a", "ap-southeast-1", "i-demo", "Running", {}),
        IdleDecision(IdleDisposition.PROPOSE, "idle", 1.0, 2.0, 3.0, 24),
        ActionStage.STOP,
        NOW,
    )


def test_fake_planner_returns_valid_action(action: ProposedAction) -> None:
    model = FakePlannerModel([action])
    outcome = plan_action(model, _request())
    assert outcome.action == action
    assert len(model.requests) == 1


def test_malformed_output_abstains_without_write(action_data: dict[str, Any]) -> None:
    malformed = {**action_data, "resource_id": ["i-demo", "i-other"]}
    model = FakePlannerModel([malformed])
    outcome = plan_action(model, _request())
    assert not outcome.proposed
    assert "malformed" in outcome.reason
    assert not hasattr(model, "call")


def test_deterministic_planner_formats_the_idle_decision() -> None:
    outcome = plan_action(DeterministicPlanner(idle_window_days=14), _request())
    assert outcome.proposed
    assert outcome.action is not None
    assert outcome.action.resource_id == "i-demo"
    assert outcome.action.action is ActionName.STOP_INSTANCES
    assert outcome.action.evidence.cpu_avg == 1.0
    assert outcome.action.evidence.mem_avg == 3.0
    assert outcome.action.evidence.samples == 24


class _ChatCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.kwargs = kwargs
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_qwen_planner_uses_chat_completions_json_object(action: ProposedAction) -> None:
    completions = _ChatCompletions(action.model_dump_json())
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    model = QwenPlanner(
        api_key="fake", base_url="https://example.invalid/v1", model="qwen3.7-plus", client=client
    )
    outcome = plan_action(model, _request())
    assert outcome.proposed
    assert completions.kwargs["response_format"] == {"type": "json_object"}
    assert "tools" not in completions.kwargs  # no hosted MCP / write tool is offered


def test_qwen_planner_abstains_on_sentinel() -> None:
    completions = _ChatCompletions('{"abstain": true}')
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    model = QwenPlanner(
        api_key="fake", base_url="https://example.invalid/v1", model="qwen3.7-plus", client=client
    )
    assert not plan_action(model, _request()).proposed
