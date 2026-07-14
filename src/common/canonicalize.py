"""The single RFC 8785 implementation shared by checkpoint and executor."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, cast

import rfc8785
from pydantic import BaseModel

_jcs_dumps = cast(Callable[[object], bytes], rfc8785.dumps)


class CanonicalizationError(ValueError):
    """Input cannot be represented as valid JCS JSON."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalizationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise CanonicalizationError(f"non-finite JSON number: {value}")


def _parse_json_bytes(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CanonicalizationError("JSON input is not UTF-8") from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CanonicalizationError("invalid JSON input") from error


def _json_value(obj: object) -> object:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Mapping):
        mapping = cast(Mapping[object, object], obj)
        return dict(mapping)
    return obj


def canonical_bytes(obj: object) -> bytes:
    """Return RFC 8785 JCS bytes for a Python value or raw JSON bytes."""

    value = _parse_json_bytes(obj) if isinstance(obj, bytes) else _json_value(obj)
    try:
        return _jcs_dumps(value)
    except (
        rfc8785.CanonicalizationError,
        TypeError,
        UnicodeEncodeError,
        ValueError,
    ) as error:
        raise CanonicalizationError("value cannot be canonicalized with JCS") from error


def action_hash(obj: object) -> bytes:
    """Return SHA-256 over the canonical representation of an action."""

    return hashlib.sha256(canonical_bytes(obj)).digest()
