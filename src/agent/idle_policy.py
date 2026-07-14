"""Deterministic, config-driven idle candidate policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from statistics import fmean


@dataclass(frozen=True, slots=True)
class MonitoringSample:
    observed_at: datetime
    cpu_percent: float
    memory_percent: float
    disk_percent: float | None = None


@dataclass(frozen=True, slots=True)
class IdlePolicyConfig:
    window_days: int = 14
    cpu_avg_threshold: float = 5.0
    cpu_max_threshold: float = 20.0
    memory_avg_threshold: float = 15.0
    minimum_samples: int = 24
    excluded_tag_terms: frozenset[str] = frozenset({"protect", "standby", "production"})


DEFAULT_IDLE_POLICY = IdlePolicyConfig()


class IdleDisposition(StrEnum):
    PROPOSE = "propose"
    ABSTAIN = "abstain"


@dataclass(frozen=True, slots=True)
class IdleDecision:
    disposition: IdleDisposition
    reason: str
    cpu_avg: float | None = None
    cpu_max: float | None = None
    memory_avg: float | None = None
    samples: int = 0

    @property
    def should_propose(self) -> bool:
        return self.disposition is IdleDisposition.PROPOSE


def _has_excluded_tag(tags: Mapping[str, str], terms: frozenset[str]) -> bool:
    normalized = {part.casefold() for pair in tags.items() for part in pair}
    return bool(normalized & {term.casefold() for term in terms})


def evaluate_idle(
    samples: Sequence[MonitoringSample],
    tags: Mapping[str, str],
    *,
    now: datetime,
    config: IdlePolicyConfig = DEFAULT_IDLE_POLICY,
) -> IdleDecision:
    """Return propose only when every explicit policy condition is satisfied."""

    if _has_excluded_tag(tags, config.excluded_tag_terms):
        return IdleDecision(IdleDisposition.ABSTAIN, "protected standby or production tag")
    start = now - timedelta(days=config.window_days)
    in_window = [sample for sample in samples if start <= sample.observed_at <= now]
    if len(in_window) < config.minimum_samples:
        return IdleDecision(
            IdleDisposition.ABSTAIN,
            f"insufficient samples: {len(in_window)} < {config.minimum_samples}",
            samples=len(in_window),
        )
    cpu_avg = fmean(sample.cpu_percent for sample in in_window)
    cpu_max = max(sample.cpu_percent for sample in in_window)
    memory_avg = fmean(sample.memory_percent for sample in in_window)
    metrics = (cpu_avg, cpu_max, memory_avg, len(in_window))
    if cpu_avg >= config.cpu_avg_threshold:
        return IdleDecision(IdleDisposition.ABSTAIN, "average CPU is not below threshold", *metrics)
    if cpu_max >= config.cpu_max_threshold:
        return IdleDecision(IdleDisposition.ABSTAIN, "maximum CPU is not below threshold", *metrics)
    if memory_avg >= config.memory_avg_threshold:
        return IdleDecision(
            IdleDisposition.ABSTAIN, "average memory is not below threshold", *metrics
        )
    return IdleDecision(IdleDisposition.PROPOSE, "idle metrics are below all thresholds", *metrics)
