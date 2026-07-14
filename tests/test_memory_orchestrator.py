from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.agent.idle_policy import IdlePolicyConfig, MonitoringSample
from src.agent.monitor import InstanceContext
from src.agent.orchestrator import AirlockOrchestrator, ApprovalSubmission
from src.agent.planner import FakePlannerModel
from src.checkpoint.signing import SignedApproval
from src.common.schemas import ProposedAction
from src.executor.execute import ExecutionResult, StateReader
from src.memory import (
    InMemoryMemoryStore,
    MemoryFact,
    MemoryProvenance,
    ReverifyRecipe,
)

NOW = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
INSTANCE = InstanceContext("account-a", "ap-southeast-1", "i-demo", "Running", {})


class MemoryReadFailureMonitor:
    """Allows idle reads, then fails the distinct memory instance read."""

    def monitoring_samples(
        self, region: str, resource_id: str, *, start: datetime, end: datetime
    ) -> list[MonitoringSample]:
        return [MonitoringSample(NOW - timedelta(hours=index), 1.0, 2.0) for index in range(3)]

    def describe_instance(self, region: str, resource_id: str) -> InstanceContext:
        raise RuntimeError("read endpoint unavailable")

    def list_instances(self, region: str) -> list[InstanceContext]:
        return [INSTANCE]

    def read_state(self, region: str, resource_id: str) -> str:
        raise AssertionError("executor state read must not run")


class CountingCheckpoint:
    def __init__(self) -> None:
        self.actions: list[ProposedAction] = []

    def submit(self, action: ProposedAction) -> ApprovalSubmission:
        self.actions.append(action)
        return ApprovalSubmission("approval-1", "hash-1")


class NoApproval:
    def wait_for_approval(self, submission: ApprovalSubmission) -> SignedApproval | None:
        return None


class CountingExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        action: ProposedAction,
        token: SignedApproval,
        state_reader: StateReader,
    ) -> ExecutionResult:
        self.calls += 1
        raise AssertionError("executor must not run without approval")


def _orchestrator(
    action_data: dict[str, Any], *, memory: InMemoryMemoryStore | None
) -> tuple[AirlockOrchestrator, FakePlannerModel, CountingCheckpoint, CountingExecutor]:
    planner = FakePlannerModel([ProposedAction.model_validate(action_data)])
    checkpoint = CountingCheckpoint()
    executor = CountingExecutor()
    orchestrator = AirlockOrchestrator(
        monitor=MemoryReadFailureMonitor(),
        planner=planner,
        checkpoint=checkpoint,
        approvals=NoApproval(),
        executor=executor,
        policy=IdlePolicyConfig(minimum_samples=3),
        memory=memory,
    )
    return orchestrator, planner, checkpoint, executor


def test_active_memory_read_failure_abstains_before_submit_or_write(
    action_data: dict[str, Any],
) -> None:
    memory = InMemoryMemoryStore()
    memory.put(
        MemoryFact(
            resource_id="i-demo",
            fact="disposable test box",
            provenance=MemoryProvenance(who="operator", when=NOW, source="review"),
            recipe=ReverifyRecipe(expected_status="Running"),
            created_at=NOW,
            last_verified=NOW,
        )
    )
    orchestrator, planner, checkpoint, executor = _orchestrator(action_data, memory=memory)

    result = orchestrator.run_candidate(INSTANCE, now=NOW)

    assert result.status == "stop_memory_unverified"
    assert planner.requests == []
    assert checkpoint.actions == []
    assert executor.calls == 0


def test_no_memory_injection_preserves_pre_memory_control_flow(
    action_data: dict[str, Any],
) -> None:
    orchestrator, planner, checkpoint, executor = _orchestrator(action_data, memory=None)

    result = orchestrator.run_candidate(INSTANCE, now=NOW)

    assert result.status == "stop_awaiting_approval"
    assert len(planner.requests) == 1
    assert len(checkpoint.actions) == 1
    assert executor.calls == 0
