from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from src.checkpoint.signing import SignedApproval, build_and_sign
from src.checkpoint.store import ApprovalRecord, InMemoryApprovalStore
from src.common.canonicalize import canonical_bytes
from src.common.error_ids import ErrorIds
from src.common.schemas import ProposedAction
from src.executor.execute import ExecutionResult, MockWriteClient, execute

Mutation = Callable[[dict[str, Any]], None]


def _set(path: tuple[str, ...], value: object) -> Mutation:
    def mutate(data: dict[str, Any]) -> None:
        target: dict[str, Any] = data
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value

    return mutate


MUTATIONS: list[tuple[str, Mutation]] = [
    ("schema_version", _set(("schema_version",), "2")),
    ("cloud_account_id", _set(("cloud_account_id",), "account-b")),
    ("region", _set(("region",), "us-east-1")),
    ("resource_id", _set(("resource_id",), "i-other")),
    ("action", _set(("action",), "DeleteInstances")),
    ("expected_status", _set(("precondition", "expected_status"), "Stopped")),
    ("observed_at", _set(("precondition", "observed_at"), "2026-07-12T12:00:01Z")),
    ("idle_window_days", _set(("evidence", "idle_window_days"), 15)),
    ("cpu_avg", _set(("evidence", "cpu_avg"), 0.5)),
    ("cpu_max", _set(("evidence", "cpu_max"), 3.2)),
    ("mem_avg", _set(("evidence", "mem_avg"), 8.1)),
    ("samples", _set(("evidence", "samples"), 337)),
    ("collected_at", _set(("evidence", "collected_at"), "2026-07-12T12:00:01Z")),
    (
        "monitor_provenance",
        _set(
            ("evidence", "monitor_provenance"),
            [{"tool": "GetCpuUsageData", "request_id": "req-2"}],
        ),
    ),
    ("disagreement", _set(("evidence", "disagreement"), {"score": 0.5})),
    ("memory_receipt", _set(("evidence", "memory_receipt"), {"receipt_id": "mem-2"})),
    ("policy_reason", _set(("policy_reason",), "different policy")),
    ("stage", _set(("stage",), "delete")),
    ("created_at", _set(("created_at",), "2026-07-12T12:01:01Z")),
]


@given(mutation=st.sampled_from(MUTATIONS))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_every_field_flip_is_refused(
    mutation: tuple[str, Mutation],
    action_data: dict[str, Any],
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    _name, mutate = mutation
    token, store, now = issued_token
    changed = deepcopy(action_data)
    mutate(changed)
    client = MockWriteClient()
    result = execute(
        changed,
        token,
        public_key=private_key.public_key(),
        current_key_id="key-current",
        bound_account_id="account-a",
        store=store,
        write_client=client,
        now=now,
    )
    assert not result.executed
    assert result.error_id == ErrorIds.VERIFY_ACTION_HASH
    assert client.calls == []


def test_batch_target_is_refused_before_write(
    action_data: dict[str, Any],
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    token, store, now = issued_token
    action_data["resource_id"] = ["i-demo", "i-other"]
    client = MockWriteClient()
    result = execute(
        action_data,
        token,
        public_key=private_key.public_key(),
        current_key_id="key-current",
        bound_account_id="account-a",
        store=store,
        write_client=client,
        now=now,
    )
    assert result.error_id == ErrorIds.VERIFY_SCHEMA
    assert client.calls == []


def test_replay_is_refused(
    action: ProposedAction,
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    token, store, now = issued_token
    client = MockWriteClient()

    def run_once() -> ExecutionResult:
        return execute(
            action,
            token,
            public_key=private_key.public_key(),
            current_key_id="key-current",
            bound_account_id="account-a",
            store=store,
            write_client=client,
            now=now,
        )

    assert run_once().executed
    second = run_once()
    assert second.error_id == ErrorIds.VERIFY_CONSUMED
    assert len(client.calls) == 1


def test_concurrent_double_spend_writes_once(
    action: ProposedAction,
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    token, store, now = issued_token
    client = MockWriteClient()

    def run_once() -> bool:
        return execute(
            action,
            token,
            public_key=private_key.public_key(),
            current_key_id="key-current",
            bound_account_id="account-a",
            store=store,
            write_client=client,
            now=now,
        ).executed

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_once) for _index in range(2)]
    results = [future.result() for future in futures]
    assert results.count(True) == 1
    assert len(client.calls) == 1


def test_expired_token_is_refused(
    action: ProposedAction,
    private_key: Ed25519PrivateKey,
) -> None:
    now = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
    token = build_and_sign(
        action,
        private_key,
        approval_id="expired",
        nonce="nonce",
        issued_at=now - timedelta(minutes=10),
        expiry=now - timedelta(seconds=1),
        key_id="key-current",
    )
    store = InMemoryApprovalStore()
    store.issue(ApprovalRecord.from_signed_approval(token, approver="operator"))
    client = MockWriteClient()
    result = execute(
        action,
        token,
        public_key=private_key.public_key(),
        current_key_id="key-current",
        bound_account_id="account-a",
        store=store,
        write_client=client,
        now=now,
    )
    assert result.error_id == ErrorIds.VERIFY_EXPIRED
    assert client.calls == []


@pytest.mark.parametrize("fault", ["wrong-key", "stale-key", "wrong-audience"])
def test_invalid_envelopes_are_refused(
    fault: str,
    action: ProposedAction,
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    original, store, now = issued_token
    signing_key = private_key
    envelope = original.envelope
    if fault == "wrong-key":
        signing_key = Ed25519PrivateKey.generate()
    elif fault == "stale-key":
        envelope = envelope.model_copy(update={"key_id": "key-stale"})
    else:
        envelope = envelope.model_copy(update={"audience": "other-executor"})
    token = SignedApproval(
        envelope=envelope,
        signature=signing_key.sign(canonical_bytes(envelope)),
    )
    client = MockWriteClient()
    result = execute(
        action,
        token,
        public_key=private_key.public_key(),
        current_key_id="key-current",
        bound_account_id="account-a",
        store=store,
        write_client=client,
        now=now,
    )
    assert not result.executed
    assert client.calls == []


def test_no_signed_approval_is_unauthorized(
    action: ProposedAction,
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    _token, store, now = issued_token
    client = MockWriteClient()
    result = execute(
        action,
        None,
        public_key=private_key.public_key(),
        current_key_id="key-current",
        bound_account_id="account-a",
        store=store,
        write_client=client,
        now=now,
    )
    assert result.error_id == ErrorIds.VERIFY_UNAUTHORIZED
    assert client.calls == []


def test_wrong_executor_account_is_refused(
    action: ProposedAction,
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    token, store, now = issued_token
    client = MockWriteClient()
    result = execute(
        action,
        token,
        public_key=private_key.public_key(),
        current_key_id="key-current",
        bound_account_id="account-b",
        store=store,
        write_client=client,
        now=now,
    )
    assert result.error_id == ErrorIds.VERIFY_ACCOUNT
    assert client.calls == []


def test_precondition_drift_is_refused(
    action: ProposedAction,
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    token, store, now = issued_token
    client = MockWriteClient()
    result = execute(
        action,
        token,
        public_key=private_key.public_key(),
        current_key_id="key-current",
        bound_account_id="account-a",
        store=store,
        write_client=client,
        now=now,
        state_reader=lambda _region, _resource_id: "Stopped",
    )
    assert result.error_id == ErrorIds.EXEC_PRECONDITION
    assert client.calls == []
