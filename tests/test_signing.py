from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from src.checkpoint.signing import build_and_sign, load_private_key, load_public_key
from src.common.canonicalize import canonical_bytes
from src.common.schemas import ProposedAction


def test_signing_round_trip(action: ProposedAction, private_key: Ed25519PrivateKey) -> None:
    now = datetime(2026, 7, 12, tzinfo=UTC)
    token = build_and_sign(
        action,
        private_key,
        approval_id="approval-1",
        nonce="nonce-1",
        issued_at=now,
        expiry=now + timedelta(minutes=5),
        key_id="key-current",
    )
    private_key.public_key().verify(token.signature, canonical_bytes(token.envelope))


def test_key_loading_from_raw_bytes_and_path(
    tmp_path: Path, private_key: Ed25519PrivateKey
) -> None:
    private_raw = private_key.private_bytes_raw()
    public_raw = private_key.public_key().public_bytes_raw()
    public_path = tmp_path / "public.key"
    public_path.write_bytes(public_raw)
    assert load_private_key(private_raw).private_bytes_raw() == private_raw
    assert load_public_key(public_path).public_bytes_raw() == public_raw

    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    assert load_private_key(pem).private_bytes_raw() == private_raw
