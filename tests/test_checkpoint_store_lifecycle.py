from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.checkpoint.signing import build_and_sign
from src.checkpoint.sqlite_store import SQLiteApprovalStore
from src.checkpoint.store import (
    ApprovalRecord,
    ApprovalStatus,
    ApprovalStore,
    ApprovalStoreError,
    ClaimStatus,
    InMemoryApprovalStore,
)
from src.common.schemas import ProposedAction

StoreFactory = Callable[[], ApprovalStore]


@pytest.fixture(params=["memory", "sqlite"])
def store_factory(request: pytest.FixtureRequest, tmp_path: Path) -> StoreFactory:
    if request.param == "memory":
        return InMemoryApprovalStore
    path = tmp_path / "approvals.sqlite3"
    return lambda: SQLiteApprovalStore(path)


def _token(action: ProposedAction, key: Ed25519PrivateKey, approval_id: str):
    now = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
    return build_and_sign(
        action,
        key,
        approval_id=approval_id,
        nonce="nonce",
        issued_at=now,
        expiry=now + timedelta(minutes=5),
        key_id="key-current",
    )


def test_pending_issue_consume_lifecycle_matches_backends(
    store_factory: StoreFactory,
    action: ProposedAction,
    private_key: Ed25519PrivateKey,
) -> None:
    store = store_factory()
    pending = ApprovalRecord.pending("approval-lifecycle", action)
    store.create_pending(pending)
    assert store.list_pending() == [pending]

    token = _token(action, private_key, pending.approval_id)
    issued = store.issue_pending(pending.approval_id, token, approver="operator@example.com")
    assert issued.status is ApprovalStatus.ISSUED
    assert issued.approver == "operator@example.com"
    assert store.list_pending() == []

    now = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
    assert store.claim(pending.approval_id, now) is ClaimStatus.CLAIMED
    assert store.claim(pending.approval_id, now) is ClaimStatus.CONSUMED
    record = store.get(pending.approval_id)
    assert record is not None
    assert record.status is ApprovalStatus.CONSUMED


def test_rejection_is_terminal_for_both_backends(
    store_factory: StoreFactory,
    action: ProposedAction,
    private_key: Ed25519PrivateKey,
) -> None:
    store = store_factory()
    pending = ApprovalRecord.pending("approval-rejected", action)
    store.create_pending(pending)
    rejected = store.reject(pending.approval_id, approver="operator@example.com")
    assert rejected.status is ApprovalStatus.REJECTED
    assert rejected.approval is None

    with pytest.raises(ApprovalStoreError, match="not pending"):
        store.issue_pending(
            pending.approval_id,
            _token(action, private_key, pending.approval_id),
            approver="operator@example.com",
        )


def test_claim_is_atomic_for_both_backends(
    store_factory: StoreFactory,
    action: ProposedAction,
    private_key: Ed25519PrivateKey,
) -> None:
    store = store_factory()
    pending = ApprovalRecord.pending("approval-atomic", action)
    store.create_pending(pending)
    store.issue_pending(
        pending.approval_id,
        _token(action, private_key, pending.approval_id),
        approver="operator@example.com",
    )
    now = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(store.claim, pending.approval_id, now) for _ in range(8)]
    results = [future.result() for future in futures]
    assert results.count(ClaimStatus.CLAIMED) == 1
    assert results.count(ClaimStatus.CONSUMED) == 7


def test_sqlite_records_are_visible_after_reopening(tmp_path: Path, action: ProposedAction) -> None:
    path = tmp_path / "durable.sqlite3"
    first = SQLiteApprovalStore(path)
    pending = ApprovalRecord.pending("durable", action)
    first.create_pending(pending)
    second = SQLiteApprovalStore(path)
    assert second.get("durable") == pending
