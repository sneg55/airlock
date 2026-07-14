"""Private-key checkpoint signing for approval envelopes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from src.common.canonicalize import action_hash, canonical_bytes
from src.common.schemas import ProposedAction


class KeyLoadError(ValueError):
    """Key material is neither a supported raw key nor a PEM key."""


class ApprovalEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    approval_id: str
    nonce: str
    action_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cloud_account_id: str
    issued_at: AwareDatetime
    expiry: AwareDatetime
    key_id: str
    audience: Literal["airlock-executor"] = "airlock-executor"


class SignedApproval(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    envelope: ApprovalEnvelope
    signature: bytes


def _key_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    try:
        return Path(source).read_bytes()
    except OSError as error:
        raise KeyLoadError(f"cannot read key path: {source}") from error


def load_private_key(source: str | Path | bytes) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from raw 32-byte or PEM material."""

    material = _key_bytes(source)
    if len(material) == 32:
        return Ed25519PrivateKey.from_private_bytes(material)
    try:
        key = serialization.load_pem_private_key(material, password=None)
    except (TypeError, ValueError) as error:
        raise KeyLoadError("invalid Ed25519 private key") from error
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyLoadError("private key is not Ed25519")
    return key


def load_public_key(source: str | Path | bytes) -> Ed25519PublicKey:
    """Load an Ed25519 public key from raw 32-byte or PEM material."""

    material = _key_bytes(source)
    if len(material) == 32:
        return Ed25519PublicKey.from_public_bytes(material)
    try:
        key = serialization.load_pem_public_key(material)
    except (TypeError, ValueError) as error:
        raise KeyLoadError("invalid Ed25519 public key") from error
    if not isinstance(key, Ed25519PublicKey):
        raise KeyLoadError("public key is not Ed25519")
    return key


def build_and_sign(
    action: ProposedAction,
    private_key: Ed25519PrivateKey,
    *,
    approval_id: str,
    nonce: str,
    issued_at: datetime,
    expiry: datetime,
    key_id: str,
) -> SignedApproval:
    """Bind one action to an envelope and sign it with the checkpoint key."""

    envelope = ApprovalEnvelope(
        approval_id=approval_id,
        nonce=nonce,
        action_hash=action_hash(action).hex(),
        cloud_account_id=action.cloud_account_id,
        issued_at=issued_at,
        expiry=expiry,
        key_id=key_id,
    )
    return SignedApproval(
        envelope=envelope,
        signature=private_key.sign(canonical_bytes(envelope)),
    )
