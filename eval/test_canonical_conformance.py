from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

import pytest
from src.common.canonicalize import CanonicalizationError, canonical_bytes


class Vector(TypedDict):
    name: str
    input: str
    expected_hex: str


VECTORS_PATH = Path(__file__).with_name("canonical_vectors.json")
VECTORS = cast(list[Vector], json.loads(VECTORS_PATH.read_text(encoding="utf-8")))


@pytest.mark.parametrize("vector", VECTORS, ids=lambda vector: vector["name"])
def test_jcs_conformance_vectors(vector: Vector) -> None:
    assert canonical_bytes(vector["input"].encode()).hex() == vector["expected_hex"]


def test_duplicate_key_conformance_rejection() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_bytes(b'{"a":1,"a":2}')
