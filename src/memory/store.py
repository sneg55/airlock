"""Resource memory persistence contract and process-local implementation."""

from __future__ import annotations

from datetime import datetime
from threading import Lock
from typing import Protocol

from src.memory.models import MemoryFact


class MemoryStore(Protocol):
    def get(self, resource_id: str) -> MemoryFact | None: ...

    def put(self, fact: MemoryFact) -> None: ...

    def retract(self, resource_id: str, reason: str) -> MemoryFact: ...

    def mark_verified(self, resource_id: str, now: datetime) -> MemoryFact: ...


class InMemoryMemoryStore:
    """Thread-safe process-local resource memory store."""

    def __init__(self) -> None:
        self._facts: dict[str, MemoryFact] = {}
        self._lock = Lock()

    def get(self, resource_id: str) -> MemoryFact | None:
        with self._lock:
            return self._facts.get(resource_id)

    def put(self, fact: MemoryFact) -> None:
        with self._lock:
            self._facts[fact.resource_id] = fact

    def retract(self, resource_id: str, reason: str) -> MemoryFact:
        with self._lock:
            fact = self._require(resource_id)
            retracted = fact.as_retracted(reason)
            self._facts[resource_id] = retracted
            return retracted

    def mark_verified(self, resource_id: str, now: datetime) -> MemoryFact:
        with self._lock:
            fact = self._require(resource_id)
            verified = fact.with_verification(now)
            self._facts[resource_id] = verified
            return verified

    def _require(self, resource_id: str) -> MemoryFact:
        fact = self._facts.get(resource_id)
        if fact is None:
            raise KeyError(f"memory fact does not exist: {resource_id}")
        return fact
