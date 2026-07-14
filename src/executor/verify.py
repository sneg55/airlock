"""Offline verification of a signed, single-use Airlock approval."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from src.checkpoint.signing import SignedApproval
from src.checkpoint.store import ApprovalStore, ClaimStatus
from src.common.canonicalize import CanonicalizationError, action_hash, canonical_bytes
from src.common.error_ids import ErrorIds
from src.common.schemas import ProposedAction


@dataclass(frozen=True, slots=True)
class VerificationResult:
    approved: bool
    action: ProposedAction | None = None
    error_id: ErrorIds | None = None

    @classmethod
    def refuse(cls, error_id: ErrorIds) -> VerificationResult:
        return cls(approved=False, error_id=error_id)


def _validated_action(value: ProposedAction | dict[str, Any]) -> ProposedAction | None:
    if isinstance(value, ProposedAction):
        return value
    try:
        return ProposedAction.model_validate(value)
    except ValidationError:
        return None


def _claim_error(status: ClaimStatus) -> ErrorIds | None:
    return {
        ClaimStatus.CLAIMED: None,
        ClaimStatus.MISSING: ErrorIds.VERIFY_MISSING,
        ClaimStatus.CONSUMED: ErrorIds.VERIFY_CONSUMED,
        ClaimStatus.EXPIRED: ErrorIds.VERIFY_EXPIRED,
    }[status]


def verify(
    action_value: ProposedAction | dict[str, Any],
    token: SignedApproval | None,
    *,
    public_key: Ed25519PublicKey,
    current_key_id: str,
    bound_account_id: str,
    store: ApprovalStore,
    now: datetime,
) -> VerificationResult:
    """Verify signature, binding, tenant, target shape, expiry, and single use."""

    if token is None:
        return VerificationResult.refuse(ErrorIds.VERIFY_UNAUTHORIZED)
    action = _validated_action(action_value)
    if action is None:
        return VerificationResult.refuse(ErrorIds.VERIFY_SCHEMA)
    envelope = token.envelope
    try:
        public_key.verify(token.signature, canonical_bytes(envelope))
    except InvalidSignature:
        return VerificationResult.refuse(ErrorIds.VERIFY_SIGNATURE)
    except CanonicalizationError:
        return VerificationResult.refuse(ErrorIds.VERIFY_SCHEMA)
    if envelope.key_id != current_key_id:
        return VerificationResult.refuse(ErrorIds.VERIFY_KEY_ID)
    if envelope.audience != "airlock-executor":
        return VerificationResult.refuse(ErrorIds.VERIFY_AUDIENCE)
    try:
        computed_hash = action_hash(action).hex()
    except CanonicalizationError:
        return VerificationResult.refuse(ErrorIds.VERIFY_SCHEMA)
    if not hmac.compare_digest(computed_hash, envelope.action_hash):
        return VerificationResult.refuse(ErrorIds.VERIFY_ACTION_HASH)
    if not (action.cloud_account_id == envelope.cloud_account_id == bound_account_id):
        return VerificationResult.refuse(ErrorIds.VERIFY_ACCOUNT)
    claim_error = _claim_error(store.claim(envelope.approval_id, now))
    if claim_error is not None:
        return VerificationResult.refuse(claim_error)
    return VerificationResult(approved=True, action=action)
