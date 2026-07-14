from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from src.agent.idle_policy import (
    IdleDisposition,
    IdlePolicyConfig,
    MonitoringSample,
    evaluate_idle,
)

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _samples(*, cpu: float = 4.0, memory: float = 14.0, count: int = 3) -> list[MonitoringSample]:
    return [MonitoringSample(NOW - timedelta(hours=index), cpu, memory) for index in range(count)]


def _config() -> IdlePolicyConfig:
    return IdlePolicyConfig(minimum_samples=3)


def test_idle_metrics_propose() -> None:
    decision = evaluate_idle(_samples(), {}, now=NOW, config=_config())
    assert decision.disposition is IdleDisposition.PROPOSE
    assert decision.samples == 3


def test_thin_samples_abstain() -> None:
    decision = evaluate_idle(_samples(count=2), {}, now=NOW, config=_config())
    assert decision.disposition is IdleDisposition.ABSTAIN
    assert "insufficient" in decision.reason


def test_standby_tag_abstains() -> None:
    decision = evaluate_idle(_samples(), {"lifecycle": "standby"}, now=NOW, config=_config())
    assert decision.disposition is IdleDisposition.ABSTAIN
    assert "tag" in decision.reason


@pytest.mark.parametrize(
    ("samples", "reason"),
    [
        (_samples(cpu=5.0), "average CPU"),
        (
            [
                MonitoringSample(NOW, 20.0, 1.0),
                MonitoringSample(NOW - timedelta(hours=1), 1.0, 1.0),
                MonitoringSample(NOW - timedelta(hours=2), 1.0, 1.0),
                MonitoringSample(NOW - timedelta(hours=3), 1.0, 1.0),
                MonitoringSample(NOW - timedelta(hours=4), 1.0, 1.0),
            ],
            "maximum CPU",
        ),
        (_samples(memory=15.0), "average memory"),
    ],
)
def test_thresholds_are_strict_boundaries(samples: list[MonitoringSample], reason: str) -> None:
    decision = evaluate_idle(samples, {}, now=NOW, config=_config())
    assert decision.disposition is IdleDisposition.ABSTAIN
    assert reason in decision.reason


def test_values_just_below_every_boundary_propose() -> None:
    samples = [
        MonitoringSample(NOW, 19.999, 14.999),
        MonitoringSample(NOW - timedelta(hours=1), 0.0, 14.999),
        MonitoringSample(NOW - timedelta(hours=2), 0.0, 14.999),
        MonitoringSample(NOW - timedelta(hours=3), 0.0, 14.999),
        MonitoringSample(NOW - timedelta(hours=4), 0.0, 14.999),
    ]
    decision = evaluate_idle(
        samples,
        {},
        now=NOW,
        config=IdlePolicyConfig(minimum_samples=5),
    )
    assert decision.disposition is IdleDisposition.PROPOSE
