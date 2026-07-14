from __future__ import annotations

from datetime import datetime
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.checkpoint.signing import SignedApproval
from src.checkpoint.store import InMemoryApprovalStore
from src.common.error_ids import ErrorIds
from src.common.schemas import ProposedAction
from src.executor.execute import MockWriteClient, StateReader, execute_live


def _kwargs(
    private_key: Ed25519PrivateKey,
    store: InMemoryApprovalStore,
    client: MockWriteClient,
    now: datetime,
) -> dict[str, object]:
    return {
        "public_key": private_key.public_key(),
        "current_key_id": "key-current",
        "bound_account_id": "account-a",
        "store": store,
        "write_client": client,
        "now": now,
    }


def test_execute_live_requires_state_reader(
    action: ProposedAction,
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    token, store, now = issued_token
    with pytest.raises(TypeError, match="state_reader"):
        execute_live(
            action,
            token,
            **_kwargs(private_key, store, MockWriteClient(), now),  # type: ignore[arg-type]
            state_reader=cast(StateReader, None),
        )


def test_execute_live_refuses_drift_with_zero_writes(
    action: ProposedAction,
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    token, store, now = issued_token
    client = MockWriteClient()
    result = execute_live(
        action,
        token,
        **_kwargs(private_key, store, client, now),  # type: ignore[arg-type]
        state_reader=lambda _region, _resource: "Stopped",
    )
    assert result.error_id is ErrorIds.EXEC_PRECONDITION
    assert client.calls == []


def test_execute_live_happy_path_writes_exactly_once(
    action: ProposedAction,
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    token, store, now = issued_token
    client = MockWriteClient()
    result = execute_live(
        action,
        token,
        **_kwargs(private_key, store, client, now),  # type: ignore[arg-type]
        state_reader=lambda _region, _resource: "Running",
    )
    assert result.executed
    assert len(client.calls) == 1
