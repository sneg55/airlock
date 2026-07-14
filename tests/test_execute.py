from __future__ import annotations

from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.checkpoint.signing import SignedApproval
from src.checkpoint.store import InMemoryApprovalStore
from src.common.schemas import ActionName, ProposedAction
from src.executor.execute import MockWriteClient, execute


def test_write_arguments_use_only_trusted_fields(
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
    )
    assert result.executed
    assert client.calls == [
        (
            ActionName.STOP_INSTANCES,
            {"RegionId": "ap-southeast-1", "InstanceId": ["i-demo"]},
        )
    ]
