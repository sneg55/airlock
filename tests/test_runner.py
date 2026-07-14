from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.agent.idle_policy import MonitoringSample
from src.agent.monitor import FakeReadMonitor, InstanceContext
from src.agent.orchestrator import ApprovalSubmission
from src.agent.planner import DeterministicPlanner
from src.agent.runner import AgentRunner
from src.common.schemas import ProposedAction
from src.jury import FakeJury
from src.jury.models import JurorVote

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


class FakeCheckpoint:
    def __init__(self) -> None:
        self.submitted: list[ProposedAction] = []

    def submit(self, action: ProposedAction) -> ApprovalSubmission:
        self.submitted.append(action)
        return ApprovalSubmission(approval_id=f"a{len(self.submitted)}", action_hash="hash")


def _runner(monitor: FakeReadMonitor, checkpoint: FakeCheckpoint) -> AgentRunner:
    return AgentRunner(
        monitor=monitor,
        planner=DeterministicPlanner(idle_window_days=14),
        checkpoint=checkpoint,
        region="ap-southeast-1",
    )


def test_agent_proposes_stop_for_idle_running_instance() -> None:
    instance = InstanceContext("account-a", "ap-southeast-1", "i-idle", "Running", {})
    samples = [MonitoringSample(NOW - timedelta(hours=i * 6), 1.0, 10.0) for i in range(30)]
    monitor = FakeReadMonitor([instance], {"i-idle": samples})
    checkpoint = FakeCheckpoint()

    results = _runner(monitor, checkpoint).run_once(now=NOW)

    assert len(results) == 1
    assert results[0].status == "submitted"
    assert len(checkpoint.submitted) == 1
    submitted = checkpoint.submitted[0]
    assert submitted.resource_id == "i-idle"
    assert submitted.precondition.expected_status == "Running"


def test_agent_binds_jury_disagreement_into_proposal() -> None:
    instance = InstanceContext("account-a", "ap-southeast-1", "i-idle", "Running", {})
    samples = [MonitoringSample(NOW - timedelta(hours=i * 6), 1.0, 10.0) for i in range(30)]
    monitor = FakeReadMonitor([instance], {"i-idle": samples})
    checkpoint = FakeCheckpoint()
    jury = FakeJury(
        [
            JurorVote(model="qwen3.7-plus", safe=True, confidence=0.9, rationale="looks idle"),
            JurorVote(model="deepseek-v4-pro", safe=False, confidence=0.8, rationale="too recent"),
        ]
    )
    runner = AgentRunner(
        monitor=monitor,
        planner=DeterministicPlanner(idle_window_days=14),
        checkpoint=checkpoint,
        region="ap-southeast-1",
        jury=jury,
    )

    results = runner.run_once(now=NOW)

    assert results[0].status == "submitted"
    disagreement = checkpoint.submitted[0].evidence.disagreement
    assert disagreement is not None
    assert disagreement["score"] == 0.5  # one of two jurors dissented
    assert disagreement["high_risk"] is True


def test_agent_abstains_for_stopped_instance() -> None:
    instance = InstanceContext("account-a", "ap-southeast-1", "i-stopped", "Stopped", {})
    monitor = FakeReadMonitor([instance], {})
    checkpoint = FakeCheckpoint()

    results = _runner(monitor, checkpoint).run_once(now=NOW)

    assert results[0].status == "abstained"
    assert checkpoint.submitted == []


def test_agent_abstains_when_not_enough_idle_samples() -> None:
    instance = InstanceContext("account-a", "ap-southeast-1", "i-busy", "Running", {})
    samples = [MonitoringSample(NOW - timedelta(hours=i), 1.0, 10.0) for i in range(3)]
    monitor = FakeReadMonitor([instance], {"i-busy": samples})
    checkpoint = FakeCheckpoint()

    results = _runner(monitor, checkpoint).run_once(now=NOW)

    assert results[0].status == "abstained"
    assert checkpoint.submitted == []
