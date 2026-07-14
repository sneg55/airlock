from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from src.agent.idle_policy import IdlePolicyConfig, MonitoringSample
from src.agent.monitor import FakeReadMonitor, InstanceContext
from src.agent.orchestrator import (
    AirlockOrchestrator,
    ApprovalSubmission,
)
from src.agent.planner import FakePlannerModel
from src.checkpoint.service import CheckpointConfig, create_app
from src.checkpoint.signing import SignedApproval
from src.checkpoint.store import ApprovalStatus, InMemoryApprovalStore
from src.common.schemas import ActionName, ProposedAction
from src.executor.execute import (
    ExecutionResult,
    MockWriteClient,
    StateReader,
    execute_live,
)
from src.jury import FakeJury, JurorVote, Jury
from src.memory import (
    InMemoryMemoryStore,
    MemoryFact,
    MemoryProvenance,
    MemoryStatus,
    MemoryStore,
    ReverifyRecipe,
)

NOW = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
TOKEN = "operator-token-long-enough"  # noqa: S105


class InProcessCheckpoint:
    def __init__(self, client: TestClient, *, tamper: bool = False) -> None:
        self.client = client
        self.tamper = tamper
        self.submissions: list[ApprovalSubmission] = []

    def submit(self, action: ProposedAction) -> ApprovalSubmission:
        response = self.client.post("/proposals", json=action.model_dump(mode="json"))
        assert response.status_code == 201
        submission = ApprovalSubmission(**response.json())
        self.submissions.append(submission)
        if self.tamper:
            action.resource_id = "i-tampered"
            self.tamper = False
        return submission


class FakeHumanApproval:
    """Test-only operator that drives the authenticated service route."""

    def __init__(self, client: TestClient, decisions: list[bool]) -> None:
        self.client = client
        self.decisions = decisions.copy()
        self.seen: list[str] = []

    def wait_for_approval(self, submission: ApprovalSubmission) -> SignedApproval | None:
        self.seen.append(submission.approval_id)
        if not self.decisions.pop(0):
            return None
        response = self.client.post(
            f"/proposals/{submission.approval_id}/approve",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert response.status_code == 200
        return SignedApproval.model_validate_json(response.text)


class LiveGateway:
    def __init__(
        self,
        key: Ed25519PrivateKey,
        store: InMemoryApprovalStore,
        monitor: FakeReadMonitor,
    ) -> None:
        self.key = key
        self.store = store
        self.monitor = monitor
        self.writes = MockWriteClient()

    def execute(
        self,
        action: ProposedAction,
        token: SignedApproval,
        state_reader: StateReader,
    ) -> ExecutionResult:
        result = execute_live(
            action,
            token,
            public_key=self.key.public_key(),
            current_key_id="key-current",
            bound_account_id="account-a",
            store=self.store,
            write_client=self.writes,
            now=NOW,
            state_reader=state_reader,
        )
        if result.executed and action.action is ActionName.STOP_INSTANCES:
            self.monitor.set_status(action.resource_id, "Stopped")
        return result


FakeMemory = InMemoryMemoryStore


def _memory_fact(recipe: ReverifyRecipe) -> MemoryFact:
    return MemoryFact(
        resource_id="i-demo",
        fact="approved disposable test box",
        provenance=MemoryProvenance(who="operator", when=NOW, source="inventory review"),
        recipe=recipe,
        created_at=NOW - timedelta(days=2),
        last_verified=NOW - timedelta(days=1),
    )


def _actions(action_data: dict[str, Any]) -> tuple[ProposedAction, ProposedAction]:
    stop = ProposedAction.model_validate(action_data)
    delete_data = deepcopy(action_data)
    delete_data.update({"action": "DeleteInstances", "stage": "delete"})
    delete_data["precondition"] = {
        "expected_status": "Stopped",
        "observed_at": NOW.isoformat(),
    }
    return stop, ProposedAction.model_validate(delete_data)


def _parts(
    action_data: dict[str, Any],
    private_key: Ed25519PrivateKey,
    decisions: list[bool],
    *,
    tamper: bool = False,
    jury: Jury | None = None,
    memory: MemoryStore | None = None,
) -> tuple[AirlockOrchestrator, InProcessCheckpoint, LiveGateway, InMemoryApprovalStore]:
    store = InMemoryApprovalStore()
    config = CheckpointConfig(
        operator_token=TOKEN,
        operator_identity="operator@example.com",
        private_key=private_key,
        key_id="key-current",
        bound_account_id="account-a",
    )
    client = TestClient(create_app(config, store=store, clock=lambda: NOW))
    instance = InstanceContext("account-a", "ap-southeast-1", "i-demo", "Running", {})
    samples = [MonitoringSample(NOW - timedelta(hours=index), 1.0, 2.0) for index in range(3)]
    monitor = FakeReadMonitor([instance], {"i-demo": samples})
    checkpoint = InProcessCheckpoint(client, tamper=tamper)
    gateway = LiveGateway(private_key, store, monitor)
    stop, delete = _actions(action_data)
    orchestrator = AirlockOrchestrator(
        monitor=monitor,
        planner=FakePlannerModel([stop, delete]),
        checkpoint=checkpoint,
        approvals=FakeHumanApproval(client, decisions),
        executor=gateway,
        policy=IdlePolicyConfig(minimum_samples=3),
        jury=jury,
        memory=memory,
    )
    return orchestrator, checkpoint, gateway, store


def test_full_staged_stop_then_separately_approved_delete(
    action_data: dict[str, Any], private_key: Ed25519PrivateKey
) -> None:
    orchestrator, checkpoint, gateway, _store = _parts(action_data, private_key, [True, True])
    instance = InstanceContext("account-a", "ap-southeast-1", "i-demo", "Running", {})
    result = orchestrator.run_candidate(instance, now=NOW)
    assert result.status == "delete_executed"
    assert len(checkpoint.submissions) == 2
    assert checkpoint.submissions[0].approval_id != checkpoint.submissions[1].approval_id
    assert [call[0] for call in gateway.writes.calls] == [
        ActionName.STOP_INSTANCES,
        ActionName.DELETE_INSTANCES,
    ]


def test_delete_is_never_issued_without_its_own_approval(
    action_data: dict[str, Any], private_key: Ed25519PrivateKey
) -> None:
    orchestrator, checkpoint, gateway, store = _parts(action_data, private_key, [True, False])
    instance = InstanceContext("account-a", "ap-southeast-1", "i-demo", "Running", {})
    result = orchestrator.run_candidate(instance, now=NOW)
    assert result.status == "delete_awaiting_approval"
    assert len(gateway.writes.calls) == 1
    delete_record = store.get(checkpoint.submissions[1].approval_id)
    assert delete_record is not None
    assert delete_record.status is ApprovalStatus.PENDING


def test_tampered_action_is_refused_with_zero_writes(
    action_data: dict[str, Any], private_key: Ed25519PrivateKey
) -> None:
    orchestrator, _checkpoint, gateway, _store = _parts(
        action_data, private_key, [True], tamper=True
    )
    instance = InstanceContext("account-a", "ap-southeast-1", "i-demo", "Running", {})
    result = orchestrator.run_candidate(instance, now=NOW)
    assert result.status == "stop_refused"
    assert gateway.writes.calls == []


def test_jury_enriches_both_separately_approved_actions(
    action_data: dict[str, Any], private_key: Ed25519PrivateKey
) -> None:
    jury = FakeJury(
        [
            JurorVote(
                model="qwen",
                safe=False,
                confidence=0.9,
                rationale="destructive operation needs closer review",
            )
        ]
    )
    orchestrator, checkpoint, _gateway, store = _parts(
        action_data, private_key, [True, True], jury=jury
    )
    instance = InstanceContext("account-a", "ap-southeast-1", "i-demo", "Running", {})

    result = orchestrator.run_candidate(instance, now=NOW)

    assert result.status == "delete_executed"
    assert len(jury.actions) == 2
    for submission in checkpoint.submissions:
        record = store.get(submission.approval_id)
        assert record is not None and record.action is not None
        assert record.action.evidence.disagreement is not None
        assert record.action.evidence.disagreement["high_risk"] is True


def test_high_disagreement_still_waits_for_human_approval(
    action_data: dict[str, Any], private_key: Ed25519PrivateKey
) -> None:
    jury = FakeJury([JurorVote(model="glm", safe=False, confidence=1.0, rationale="unsafe")])
    orchestrator, checkpoint, gateway, store = _parts(action_data, private_key, [False], jury=jury)
    instance = InstanceContext("account-a", "ap-southeast-1", "i-demo", "Running", {})

    result = orchestrator.run_candidate(instance, now=NOW)

    assert result.status == "stop_awaiting_approval"
    assert gateway.writes.calls == []
    record = store.get(checkpoint.submissions[0].approval_id)
    assert record is not None
    assert record.status is ApprovalStatus.PENDING


def test_retracted_memory_abstains_before_planning_submission_or_write(
    action_data: dict[str, Any], private_key: Ed25519PrivateKey
) -> None:
    memory = FakeMemory()
    memory.put(_memory_fact(ReverifyRecipe(required_tags={"Env": "test"})))
    orchestrator, checkpoint, gateway, _store = _parts(
        action_data, private_key, [True], memory=memory
    )
    instance = InstanceContext("account-a", "ap-southeast-1", "i-demo", "Running", {})
    result = orchestrator.run_candidate(instance, now=NOW)

    assert result.status == "stop_memory_retracted"
    assert checkpoint.submissions == []
    assert gateway.writes.calls == []
    stored = memory.get("i-demo")
    assert stored is not None and stored.status is MemoryStatus.RETRACTED


def test_memory_and_jury_compose_without_auto_approval(
    action_data: dict[str, Any], private_key: Ed25519PrivateKey
) -> None:
    memory = FakeMemory()
    memory.put(_memory_fact(ReverifyRecipe(cpu_avg_upper_bound=5.0)))
    jury = FakeJury([JurorVote(model="glm", safe=True, confidence=0.9, rationale="safe")])
    orchestrator, checkpoint, gateway, store = _parts(
        action_data, private_key, [True, False], memory=memory, jury=jury
    )
    instance = InstanceContext("account-a", "ap-southeast-1", "i-demo", "Running", {})
    result = orchestrator.run_candidate(instance, now=NOW)

    assert result.status == "delete_awaiting_approval"
    assert len(gateway.writes.calls) == 1
    assert len(jury.actions) == 2
    for submission in checkpoint.submissions:
        record = store.get(submission.approval_id)
        assert record is not None and record.action is not None
        receipt = record.action.evidence.memory_receipt
        assert receipt is not None and receipt["status"] == "verified"
        assert record.action.evidence.disagreement is not None
    delete_record = store.get(checkpoint.submissions[1].approval_id)
    assert delete_record is not None and delete_record.status is ApprovalStatus.PENDING
