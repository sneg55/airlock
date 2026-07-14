"""Trusted-field reconstruction and offline write execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from src.checkpoint.signing import SignedApproval
from src.checkpoint.store import ApprovalStore
from src.common.error_ids import ErrorIds
from src.common.schemas import ActionName, ProposedAction
from src.executor.verify import verify

StateReader = Callable[[str, str], str]


class WriteClientError(RuntimeError):
    """A write client reported that the cloud operation failed."""


class WriteClient(Protocol):
    def call(self, action: ActionName, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    approval_id: str
    action: ActionName
    resource_id: str
    region: str
    status: str
    response: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    executed: bool
    receipt: ExecutionReceipt | None = None
    error_id: ErrorIds | None = None


class MockWriteClient:
    """Thread-safe zero-cloud write client used by the proof harness."""

    def __init__(self) -> None:
        self.calls: list[tuple[ActionName, dict[str, Any]]] = []
        self._lock = Lock()

    def call(self, action: ActionName, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.calls.append((action, arguments.copy()))
        return {"request_id": f"mock-{len(self.calls)}"}


def _trusted_arguments(action: ProposedAction) -> dict[str, Any]:
    """Reconstruct write arguments from trusted fields only; single target.

    The deployed Alibaba Cloud Ops MCP server types the ECS ``InstanceId`` parameter as an
    array, so a single-target stop or delete is sent as a one-element list (never a wider
    batch). RDS takes a singular ``DBInstanceId`` string. No model-produced ``params`` blob
    is ever consulted here; both values come from the signed, trusted fields.
    """

    if action.action is ActionName.STOP_DB_INSTANCES:
        return {"RegionId": action.region, "DBInstanceId": action.resource_id}
    return {"RegionId": action.region, "InstanceId": [action.resource_id]}


def execute(
    action_value: ProposedAction | dict[str, Any],
    token: SignedApproval | None,
    *,
    public_key: Ed25519PublicKey,
    current_key_id: str,
    bound_account_id: str,
    store: ApprovalStore,
    write_client: WriteClient,
    now: datetime,
    state_reader: StateReader | None = None,
) -> ExecutionResult:
    """Verify, re-read state, reconstruct arguments, then perform one write."""

    verification = verify(
        action_value,
        token,
        public_key=public_key,
        current_key_id=current_key_id,
        bound_account_id=bound_account_id,
        store=store,
        now=now,
    )
    if not verification.approved or verification.action is None or token is None:
        return ExecutionResult(executed=False, error_id=verification.error_id)
    action = verification.action
    if state_reader is not None:
        live_status = state_reader(action.region, action.resource_id)
        if live_status != action.precondition.expected_status:
            return ExecutionResult(executed=False, error_id=ErrorIds.EXEC_PRECONDITION)
    receipt = ExecutionReceipt(
        approval_id=token.envelope.approval_id,
        action=action.action,
        resource_id=action.resource_id,
        region=action.region,
        status="succeeded",
    )
    try:
        response = write_client.call(action.action, _trusted_arguments(action))
    except WriteClientError:
        failed = ExecutionReceipt(
            approval_id=receipt.approval_id,
            action=receipt.action,
            resource_id=receipt.resource_id,
            region=receipt.region,
            status="failed",
        )
        return ExecutionResult(
            executed=False,
            receipt=failed,
            error_id=ErrorIds.EXEC_WRITE_FAILED,
        )
    return ExecutionResult(
        executed=True,
        receipt=ExecutionReceipt(
            approval_id=receipt.approval_id,
            action=receipt.action,
            resource_id=receipt.resource_id,
            region=receipt.region,
            status=receipt.status,
            response=response,
        ),
    )


def execute_live(
    action_value: ProposedAction | dict[str, Any],
    token: SignedApproval | None,
    *,
    public_key: Ed25519PublicKey,
    current_key_id: str,
    bound_account_id: str,
    store: ApprovalStore,
    write_client: WriteClient,
    now: datetime,
    state_reader: StateReader,
) -> ExecutionResult:
    """Production entrypoint that makes the pre-write state re-read mandatory."""

    if state_reader is None:
        raise TypeError("execute_live requires a state_reader")
    return execute(
        action_value,
        token,
        public_key=public_key,
        current_key_id=current_key_id,
        bound_account_id=bound_account_id,
        store=store,
        write_client=write_client,
        now=now,
        state_reader=state_reader,
    )
