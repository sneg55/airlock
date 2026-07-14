from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.checkpoint.signing import SignedApproval, build_and_sign
from src.checkpoint.store import ApprovalRecord, InMemoryApprovalStore
from src.common.schemas import ProposedAction


@pytest.fixture
def action_data() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "cloud_account_id": "account-a",
        "region": "ap-southeast-1",
        "resource_id": "i-demo",
        "action": "StopInstances",
        "precondition": {
            "expected_status": "Running",
            "observed_at": "2026-07-12T12:00:00Z",
        },
        "evidence": {
            "idle_window_days": 14,
            "cpu_avg": 0.4,
            "cpu_max": 3.1,
            "mem_avg": 8.0,
            "samples": 336,
            "collected_at": "2026-07-12T12:00:00Z",
            "monitor_provenance": [{"tool": "GetCpuUsageData", "request_id": "req-1"}],
            "disagreement": {"score": 0.0},
            "memory_receipt": {"receipt_id": "mem-1"},
        },
        "policy_reason": "idle >14d under thresholds",
        "stage": "stop",
        "created_at": "2026-07-12T12:01:00Z",
    }


@pytest.fixture
def action(action_data: dict[str, Any]) -> ProposedAction:
    return ProposedAction.model_validate(action_data)


@pytest.fixture
def private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def issued_token(
    action: ProposedAction, private_key: Ed25519PrivateKey
) -> tuple[SignedApproval, InMemoryApprovalStore, datetime]:
    now = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
    token = build_and_sign(
        action,
        private_key,
        approval_id="approval-1",
        nonce="nonce-1",
        issued_at=now,
        expiry=now + timedelta(minutes=5),
        key_id="key-current",
    )
    store = InMemoryApprovalStore()
    store.issue(ApprovalRecord.from_signed_approval(token, approver="operator@example.com"))
    return token, store, now
