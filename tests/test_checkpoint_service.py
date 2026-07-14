from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from src.checkpoint.service import CheckpointConfig, create_app
from src.checkpoint.signing import SignedApproval
from src.checkpoint.store import ApprovalStatus, InMemoryApprovalStore
from src.common.canonicalize import action_hash, canonical_bytes
from src.common.error_ids import ErrorIds
from src.common.schemas import ProposedAction
from src.executor.execute import MockWriteClient, execute
from src.executor.verify import verify

NOW = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
TOKEN = "operator-token-long-enough"  # noqa: S105
ServiceParts = tuple[TestClient, InMemoryApprovalStore]


@pytest.fixture
def service_parts(private_key: Ed25519PrivateKey) -> ServiceParts:
    store = InMemoryApprovalStore()
    config = CheckpointConfig(
        operator_token=TOKEN,
        operator_identity="operator@example.com",
        private_key=private_key,
        key_id="key-current",
        bound_account_id="account-a",
    )
    app = create_app(config, store=store, clock=lambda: NOW)
    return TestClient(app), store


def _submit(client: TestClient, action_data: dict[str, Any]) -> str:
    response = client.post("/proposals", json=action_data)
    assert response.status_code == 201
    return response.json()["approval_id"]


@pytest.mark.parametrize("route", ["approve", "reject"])
@pytest.mark.parametrize("authorization", [None, "Bearer wrong-token"])
def test_operator_mutations_require_valid_credential(
    route: str,
    authorization: str | None,
    action_data: dict[str, Any],
    service_parts: ServiceParts,
) -> None:
    client, store = service_parts
    approval_id = _submit(client, action_data)
    headers = {"Authorization": authorization} if authorization else {}
    response = client.post(f"/proposals/{approval_id}/{route}", headers=headers)
    assert response.status_code == 401
    record = store.get(approval_id)
    assert record is not None
    assert record.status is ApprovalStatus.PENDING
    assert record.approval is None


def test_authorized_approval_is_signed_bound_and_verifiable(
    action_data: dict[str, Any],
    private_key: Ed25519PrivateKey,
    service_parts: ServiceParts,
) -> None:
    client, store = service_parts
    approval_id = _submit(client, action_data)
    response = client.post(
        f"/proposals/{approval_id}/approve",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
    token = SignedApproval.model_validate_json(response.text)
    action = ProposedAction.model_validate(action_data)
    private_key.public_key().verify(token.signature, canonical_bytes(token.envelope))
    assert token.envelope.action_hash == action_hash(action).hex()
    record = store.get(approval_id)
    assert record is not None
    assert record.status is ApprovalStatus.ISSUED
    assert record.approver == "operator@example.com"

    result = verify(
        action,
        token,
        public_key=private_key.public_key(),
        current_key_id="key-current",
        bound_account_id="account-a",
        store=store,
        now=NOW,
    )
    assert result.approved


def test_reject_is_terminal_and_never_issues_token(
    action_data: dict[str, Any], service_parts: ServiceParts
) -> None:
    client, store = service_parts
    approval_id = _submit(client, action_data)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    rejected = client.post(f"/proposals/{approval_id}/reject", headers=headers)
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    second = client.post(f"/proposals/{approval_id}/approve", headers=headers)
    assert second.status_code == 409
    record = store.get(approval_id)
    assert record is not None
    assert record.status is ApprovalStatus.REJECTED
    assert record.approval is None


def test_approve_twice_refuses_second_issue(
    action_data: dict[str, Any], service_parts: ServiceParts
) -> None:
    client, store = service_parts
    approval_id = _submit(client, action_data)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post(f"/proposals/{approval_id}/approve", headers=headers).status_code == 200
    assert client.post(f"/proposals/{approval_id}/approve", headers=headers).status_code == 409
    record = store.get(approval_id)
    assert record is not None
    assert record.status is ApprovalStatus.ISSUED


def test_full_service_to_executor_pipeline_and_tamper_refusal(
    action_data: dict[str, Any],
    private_key: Ed25519PrivateKey,
    service_parts: ServiceParts,
) -> None:
    client, store = service_parts
    approval_id = _submit(client, action_data)
    response = client.post(
        f"/proposals/{approval_id}/approve",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    token = SignedApproval.model_validate_json(response.text)
    write_client = MockWriteClient()
    result = execute(
        action_data,
        token,
        public_key=private_key.public_key(),
        current_key_id="key-current",
        bound_account_id="account-a",
        store=store,
        write_client=write_client,
        now=NOW,
        state_reader=lambda _region, _resource: "Running",
    )
    assert result.executed
    assert len(write_client.calls) == 1

    tampered = deepcopy(action_data)
    tampered["resource_id"] = "i-other"
    tamper_client = MockWriteClient()
    refused = execute(
        tampered,
        token,
        public_key=private_key.public_key(),
        current_key_id="key-current",
        bound_account_id="account-a",
        store=store,
        write_client=tamper_client,
        now=NOW,
    )
    assert refused.error_id is ErrorIds.VERIFY_ACTION_HASH
    assert tamper_client.calls == []


def test_pending_views_render_action_evidence_and_hash(
    action_data: dict[str, Any], service_parts: ServiceParts
) -> None:
    client, _store = service_parts
    approval_id = _submit(client, action_data)
    listing = client.get("/proposals/pending").json()
    shown = client.get(f"/proposals/{approval_id}").json()
    assert listing == [shown]
    assert shown["action"]["evidence"] == action_data["evidence"]
    assert len(shown["action_hash"]) == 64
