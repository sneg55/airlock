"""Live deterministic consultation and action receipt enrichment."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from statistics import fmean
from typing import cast

from pydantic import JsonValue

from src.agent.monitor import ReadMonitor
from src.common.schemas import Evidence, ProposedAction
from src.memory.models import (
    MemoryFact,
    MemoryStatus,
    ReverifyContext,
    normalized_tags,
)
from src.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class MemoryDisposition(StrEnum):
    NONE = "none"
    VERIFIED = "verified"
    RETRACTED = "retracted"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class MemoryOutcome:
    disposition: MemoryDisposition
    receipt: dict[str, JsonValue] | None = None
    reason: str | None = None

    @property
    def should_abstain(self) -> bool:
        return self.disposition in {
            MemoryDisposition.RETRACTED,
            MemoryDisposition.UNVERIFIED,
        }


def consult(
    resource_id: str,
    monitor: ReadMonitor,
    now: datetime,
    *,
    region: str,
    store: MemoryStore,
    max_receipt_age: timedelta,
) -> MemoryOutcome:
    """Check active memory against live state before a planner sees the resource.

    An active fact whose reads fail is unverified and requires abstention. No active
    fact proceeds without reads or a receipt. This asymmetric policy prevents a read
    outage from turning an existing belief into action authority.
    """

    if max_receipt_age <= timedelta(0):
        raise ValueError("memory receipt maximum age must be positive")
    fact = store.get(resource_id)
    if fact is None or fact.status is not MemoryStatus.ACTIVE:
        return MemoryOutcome(MemoryDisposition.NONE)
    try:
        context = _read_context(fact, monitor, region=region, now=now)
    except Exception:
        logger.exception("memory re-verification read failed for %s", resource_id)
        return MemoryOutcome(
            MemoryDisposition.UNVERIFIED,
            reason="active memory fact could not be checked against live state",
        )
    if not fact.recipe.evaluate(context):
        reason = "live state contradicted the deterministic re-verification recipe"
        store.retract(resource_id, reason)
        logger.warning("memory fact retracted for %s: %s", resource_id, reason)
        return MemoryOutcome(MemoryDisposition.RETRACTED, reason=reason)

    verified = store.mark_verified(resource_id, now)
    if not _within_freshness_bound(verified.last_verified, now, max_receipt_age):
        logger.warning("memory verification for %s fell outside freshness bound", resource_id)
        return MemoryOutcome(
            MemoryDisposition.UNVERIFIED,
            reason="memory verification fell outside the configured freshness bound",
        )
    return MemoryOutcome(
        MemoryDisposition.VERIFIED,
        receipt=_receipt(verified, max_receipt_age=max_receipt_age),
    )


def enrich_with_receipt(action: ProposedAction, receipt: dict[str, JsonValue]) -> ProposedAction:
    """Return a validated copy with the memory receipt bound into its evidence."""

    evidence = Evidence.model_validate(
        {**action.evidence.model_dump(mode="json"), "memory_receipt": receipt}
    )
    return ProposedAction.model_validate(
        {**action.model_dump(mode="json"), "evidence": evidence.model_dump(mode="json")}
    )


def _read_context(
    fact: MemoryFact,
    monitor: ReadMonitor,
    *,
    region: str,
    now: datetime,
) -> ReverifyContext:
    instance = monitor.describe_instance(region, fact.resource_id)
    if instance.resource_id != fact.resource_id:
        raise ValueError("monitor returned a different resource")
    cpu_avg = None
    window_days = None
    if fact.recipe.needs_cpu_samples:
        window_days = fact.recipe.window_days
        start = now - timedelta(days=window_days)
        samples = [
            sample
            for sample in monitor.monitoring_samples(region, fact.resource_id, start=start, end=now)
            if start <= sample.observed_at <= now
        ]
        cpu_avg = fmean(sample.cpu_percent for sample in samples) if samples else None
    return ReverifyContext(
        status=instance.status,
        tags=normalized_tags(instance.tags),
        cpu_avg=cpu_avg,
        window_days=window_days,
    )


def _within_freshness_bound(
    verified_at: datetime, now: datetime, max_receipt_age: timedelta
) -> bool:
    age = now - verified_at
    return timedelta(0) <= age <= max_receipt_age


def _receipt(fact: MemoryFact, *, max_receipt_age: timedelta) -> dict[str, JsonValue]:
    value = {
        "fact": fact.fact,
        "provenance": fact.provenance.model_dump(mode="json"),
        "recipe": fact.recipe.model_dump(mode="json"),
        "verified_at": fact.last_verified.isoformat(),
        "valid_until": (fact.last_verified + max_receipt_age).isoformat(),
        "status": "verified",
    }
    return cast(dict[str, JsonValue], value)
