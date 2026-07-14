from __future__ import annotations

from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.checkpoint.signing import SignedApproval
from src.checkpoint.store import InMemoryApprovalStore
from src.common.error_ids import ErrorIds
from src.common.schemas import ProposedAction
from src.executor.execute import MockWriteClient, execute


def test_tampered_signature_is_refused(
    action: ProposedAction,
    issued_token: tuple[SignedApproval, InMemoryApprovalStore, datetime],
    private_key: Ed25519PrivateKey,
) -> None:
    token, store, now = issued_token
    forged = SignedApproval(envelope=token.envelope, signature=b"0" * 64)
    client = MockWriteClient()
    result = execute(
        action,
        forged,
        public_key=private_key.public_key(),
        current_key_id="key-current",
        bound_account_id="account-a",
        store=store,
        write_client=client,
        now=now,
    )
    assert result.error_id == ErrorIds.VERIFY_SIGNATURE
    assert client.calls == []
