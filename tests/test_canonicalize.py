from __future__ import annotations

import math

import pytest
from src.common.canonicalize import CanonicalizationError, action_hash, canonical_bytes


def test_canonical_hash_is_sha256() -> None:
    assert action_hash({"b": 1, "a": 2}).hex() == (
        "d3626ac30a87e6f7a6428233b3c68299976865fa5508e4267c5415c76af7a772"
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(CanonicalizationError):
        canonical_bytes({"value": value})


def test_rejects_duplicate_keys() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_bytes(b'{"target":"i-one","target":"i-two"}')


def test_rejects_non_utf8() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_bytes(b'{"value":"\xff"}')
