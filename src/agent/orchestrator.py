"""Staged idle detection, approval, stop, verification, and delete flow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import httpx

from src.agent.idle_policy import DEFAULT_IDLE_POLICY, IdleDecision, IdlePolicyConfig, evaluate_idle
from src.agent.monitor import InstanceContext, ReadMonitor
from src.agent.planner import PlannerModel, PlannerRequest, plan_action
from src.checkpoint.signing import SignedApproval
from src.common.env import Env, env
from src.common.schemas import ActionStage, ProposedAction
from src.executor.execute import ExecutionResult, StateReader
from src.jury import Jury, enrich_action
from src.memory import (
    MemoryDisposition,
    MemoryOutcome,
    MemoryStore,
    consult,
    enrich_with_receipt,
)


@dataclass(frozen=True, slots=True)
class ApprovalSubmission:
    approval_id: str
    action_hash: str


class CheckpointClient(Protocol):
    def submit(self, action: ProposedAction) -> ApprovalSubmission: ...


class ApprovalBoundary(Protocol):
    """Out-of-band human decision source. It has no approve operation."""

    def wait_for_approval(self, submission: ApprovalSubmission) -> SignedApproval | None: ...


class ExecutorGateway(Protocol):
    def execute(
        self,
        action: ProposedAction,
        token: SignedApproval,
        state_reader: StateReader,
    ) -> ExecutionResult: ...


class HttpCheckpointClient:
    def __init__(self, base_url: str, *, client: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=10.0)

    @classmethod
    def from_env(cls, settings: Env = env) -> HttpCheckpointClient:
        return cls(settings.checkpoint_url)

    def submit(self, action: ProposedAction) -> ApprovalSubmission:
        response = self._client.post(
            f"{self._base_url}/proposals",
            json=action.model_dump(mode="json"),
        )
        response.raise_for_status()
        return ApprovalSubmission(**response.json())


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    status: str
    idle_decision: IdleDecision
    stop_submission: ApprovalSubmission | None = None
    stop_execution: ExecutionResult | None = None
    delete_submission: ApprovalSubmission | None = None
    delete_execution: ExecutionResult | None = None


class AirlockOrchestrator:
    def __init__(
        self,
        *,
        monitor: ReadMonitor,
        planner: PlannerModel,
        checkpoint: CheckpointClient,
        approvals: ApprovalBoundary,
        executor: ExecutorGateway,
        policy: IdlePolicyConfig = DEFAULT_IDLE_POLICY,
        jury: Jury | None = None,
        memory: MemoryStore | None = None,
        memory_max_receipt_age: timedelta | None = None,
    ) -> None:
        self._monitor = monitor
        self._planner = planner
        self._checkpoint = checkpoint
        self._approvals = approvals
        self._executor = executor
        self._policy = policy
        self._jury = jury
        self._memory = memory
        self._memory_max_receipt_age = memory_max_receipt_age or timedelta(
            seconds=env.memory_max_receipt_age_seconds
        )

    def _enrich_action(self, action: ProposedAction, memory: MemoryOutcome) -> ProposedAction:
        enriched = action
        if memory.receipt is not None:
            enriched = enrich_with_receipt(enriched, memory.receipt)
        if self._jury is not None:
            enriched = enrich_action(enriched, self._jury.assess(enriched))
        return enriched

    def _consult_memory(self, instance: InstanceContext, *, now: datetime) -> MemoryOutcome:
        if self._memory is None:
            return MemoryOutcome(MemoryDisposition.NONE)
        return consult(
            instance.resource_id,
            self._monitor,
            now,
            region=instance.region,
            store=self._memory,
            max_receipt_age=self._memory_max_receipt_age,
        )

    def run_candidate(self, instance: InstanceContext, *, now: datetime) -> OrchestrationResult:
        start = now - timedelta(days=self._policy.window_days)
        samples = self._monitor.monitoring_samples(
            instance.region, instance.resource_id, start=start, end=now
        )
        idle = evaluate_idle(samples, instance.tags, now=now, config=self._policy)
        if not idle.should_propose:
            return OrchestrationResult("idle_policy_abstained", idle)

        stop_memory = self._consult_memory(instance, now=now)
        if stop_memory.should_abstain:
            return OrchestrationResult(f"stop_memory_{stop_memory.disposition}", idle)
        stop = plan_action(
            self._planner,
            PlannerRequest(instance, idle, ActionStage.STOP, now),
        )
        if stop.action is None:
            return OrchestrationResult("stop_planner_abstained", idle)
        stop_action = self._enrich_action(stop.action, stop_memory)
        stop_submission = self._checkpoint.submit(stop_action)
        stop_token = self._approvals.wait_for_approval(stop_submission)
        if stop_token is None:
            return OrchestrationResult(
                "stop_awaiting_approval", idle, stop_submission=stop_submission
            )
        stop_result = self._executor.execute(stop_action, stop_token, self._monitor.read_state)
        if not stop_result.executed:
            return OrchestrationResult(
                "stop_refused",
                idle,
                stop_submission=stop_submission,
                stop_execution=stop_result,
            )

        observed = self._monitor.describe_instance(instance.region, instance.resource_id)
        if observed.status != "Stopped":
            return OrchestrationResult(
                "stop_not_verified",
                idle,
                stop_submission=stop_submission,
                stop_execution=stop_result,
            )
        delete_memory = self._consult_memory(observed, now=now)
        if delete_memory.should_abstain:
            return OrchestrationResult(
                f"delete_memory_{delete_memory.disposition}",
                idle,
                stop_submission=stop_submission,
                stop_execution=stop_result,
            )
        delete = plan_action(
            self._planner,
            PlannerRequest(observed, idle, ActionStage.DELETE, now),
        )
        if delete.action is None:
            return OrchestrationResult(
                "delete_planner_abstained",
                idle,
                stop_submission=stop_submission,
                stop_execution=stop_result,
            )
        delete_action = self._enrich_action(delete.action, delete_memory)
        delete_submission = self._checkpoint.submit(delete_action)
        delete_token = self._approvals.wait_for_approval(delete_submission)
        if delete_token is None:
            return OrchestrationResult(
                "delete_awaiting_approval",
                idle,
                stop_submission=stop_submission,
                stop_execution=stop_result,
                delete_submission=delete_submission,
            )
        delete_result = self._executor.execute(
            delete_action, delete_token, self._monitor.read_state
        )
        return OrchestrationResult(
            "delete_executed" if delete_result.executed else "delete_refused",
            idle,
            stop_submission=stop_submission,
            stop_execution=stop_result,
            delete_submission=delete_submission,
            delete_execution=delete_result,
        )
