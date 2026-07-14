"""Read-only agent pass: discover idle instances, plan, and submit proposals.

This process holds only the read capability (a read monitor and a planner that never calls
a write tool). It submits proposals to the checkpoint and stops. Any write happens later, in
the separate executor daemon, and only after an operator approves. The agent never holds a
write credential, so a compromised or misbehaving planner still cannot mutate the cloud.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.agent.idle_policy import DEFAULT_IDLE_POLICY, IdlePolicyConfig, evaluate_idle
from src.agent.monitor import AlibabaReadMonitor, InstanceContext, ReadMonitor
from src.agent.orchestrator import ApprovalSubmission, CheckpointClient, HttpCheckpointClient
from src.agent.planner import (
    DeterministicPlanner,
    PlannerModel,
    PlannerRequest,
    QwenPlanner,
    plan_action,
)
from src.common.env import Env, env
from src.common.schemas import ActionStage, ProposedAction
from src.jury import Jury, MultiModelJury, enrich_action

logger = logging.getLogger("airlock.agent")


def _build_planner_and_jury(settings: Env) -> tuple[PlannerModel, Jury | None]:
    """Use Qwen (planner + multi-model jury) when a key is configured; else deterministic.

    Qwen arbitration and the disagreement jury are the intelligence layer. The gate makes
    that autonomy safe, so we lead with Qwen whenever it is available and fall back to a
    deterministic planner (no key, no jury) so the agent always runs.
    """

    if settings.qwen_api_key.get_secret_value():
        return QwenPlanner.from_env(settings), MultiModelJury.from_env(settings)
    return DeterministicPlanner(idle_window_days=DEFAULT_IDLE_POLICY.window_days), None


@dataclass(frozen=True, slots=True)
class ProposalResult:
    resource_id: str
    status: str  # "submitted" | "abstained"
    reason: str
    submission: ApprovalSubmission | None = None


@dataclass(frozen=True, slots=True)
class AgentRunner:
    monitor: ReadMonitor
    planner: PlannerModel
    checkpoint: CheckpointClient
    region: str
    jury: Jury | None = None
    policy: IdlePolicyConfig = DEFAULT_IDLE_POLICY

    @classmethod
    def from_env(cls, settings: Env = env) -> AgentRunner:
        planner, jury = _build_planner_and_jury(settings)
        return cls(
            monitor=AlibabaReadMonitor.from_env(settings),
            planner=planner,
            checkpoint=HttpCheckpointClient.from_env(settings),
            region=settings.cloud_region,
            jury=jury,
        )

    def run_once(self, *, now: datetime | None = None) -> list[ProposalResult]:
        moment = now or datetime.now(UTC)
        return [
            self._consider(instance, moment)
            for instance in self.monitor.list_instances(self.region)
        ]

    def _consider(self, instance: InstanceContext, moment: datetime) -> ProposalResult:
        if instance.status != "Running":
            return ProposalResult(
                instance.resource_id, "abstained", f"not running: {instance.status}"
            )
        start = moment - timedelta(days=self.policy.window_days)
        samples = self.monitor.monitoring_samples(
            instance.region, instance.resource_id, start=start, end=moment
        )
        idle = evaluate_idle(samples, instance.tags, now=moment, config=self.policy)
        if not idle.should_propose:
            return ProposalResult(instance.resource_id, "abstained", idle.reason)
        outcome = plan_action(
            self.planner, PlannerRequest(instance, idle, ActionStage.STOP, moment)
        )
        if outcome.action is None:
            return ProposalResult(instance.resource_id, "abstained", outcome.reason)
        action = self._with_jury(outcome.action)
        submission = self.checkpoint.submit(action)
        logger.info(
            "proposed stop for %s (approval %s)", instance.resource_id, submission.approval_id
        )
        return ProposalResult(instance.resource_id, "submitted", "idle stop proposed", submission)

    def _with_jury(self, action: ProposedAction) -> ProposedAction:
        """Bind the Qwen multi-model disagreement score into the evidence, if a jury is set."""

        if self.jury is None:
            return action
        return enrich_action(action, self.jury.assess(action))
