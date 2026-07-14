from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.checkpoint.signing import build_and_sign
from src.checkpoint.store import ApprovalRecord, InMemoryApprovalStore
from src.common.schemas import ProposedAction
from src.executor.execute import MockWriteClient
from src.executor.service import ExecutorService

NOW = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)


def _issued_store(action: ProposedAction, private_key: Ed25519PrivateKey) -> InMemoryApprovalStore:
    token = build_and_sign(
        action,
        private_key,
        approval_id="approval-1",
        nonce="nonce-1",
        issued_at=NOW,
        expiry=NOW + timedelta(minutes=5),
        key_id="key-current",
    )
    store = InMemoryApprovalStore()
    store.create_pending(ApprovalRecord.pending("approval-1", action))
    store.issue_pending("approval-1", token, approver="operator@example.com")
    return store


def _service(
    store: InMemoryApprovalStore,
    write: MockWriteClient,
    private_key: Ed25519PrivateKey,
    live_status: str,
) -> ExecutorService:
    return ExecutorService(
        store=store,
        write_client=write,
        state_reader=lambda region, resource_id: live_status,
        public_key=private_key.public_key(),
        key_id="key-current",
        bound_account_id="account-a",
        clock=lambda: NOW,
    )


def test_executor_executes_issued_then_consumes(
    action: ProposedAction, private_key: Ed25519PrivateKey
) -> None:
    store = _issued_store(action, private_key)
    write = MockWriteClient()
    service = _service(store, write, private_key, "Running")

    results = service.run_once()

    assert len(results) == 1
    assert results[0].executed
    assert len(write.calls) == 1
    # single-use: the approval is consumed, so a second pass writes nothing (replay refused).
    assert service.run_once() == []
    assert len(write.calls) == 1


def test_executor_refuses_when_live_state_drifted(
    action: ProposedAction, private_key: Ed25519PrivateKey
) -> None:
    store = _issued_store(action, private_key)
    write = MockWriteClient()
    service = _service(store, write, private_key, "Stopped")  # precondition expects "Running"

    results = service.run_once()

    assert len(results) == 1
    assert not results[0].executed
    assert write.calls == []  # pre-write re-read blocked the write
