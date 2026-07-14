from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import JsonValue
from src.agent.idle_policy import MonitoringSample
from src.agent.monitor import FakeReadMonitor, InstanceContext
from src.common.canonicalize import action_hash
from src.common.schemas import ProposedAction
from src.jury import JuryAssessment, enrich_action
from src.memory import (
    InMemoryMemoryStore,
    MemoryDisposition,
    MemoryFact,
    MemoryOutcome,
    MemoryProvenance,
    MemoryStatus,
    ReverifyContext,
    ReverifyRecipe,
    consult,
    enrich_with_receipt,
)

NOW = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
REGION = "ap-southeast-1"


def _fact(recipe: ReverifyRecipe) -> MemoryFact:
    return MemoryFact(
        resource_id="i-demo",
        fact="approved disposable test box",
        provenance=MemoryProvenance(who="operator", when=NOW, source="inventory review"),
        recipe=recipe,
        created_at=NOW - timedelta(days=2),
        last_verified=NOW - timedelta(days=1),
    )


def _monitor(*, tags: dict[str, str] | None = None, cpu: float = 4.0) -> FakeReadMonitor:
    instance = InstanceContext("account-a", REGION, "i-demo", "Running", tags or {})
    samples = [MonitoringSample(NOW - timedelta(hours=hour), cpu, 2.0) for hour in range(3)]
    return FakeReadMonitor([instance], {"i-demo": samples})


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (
            ReverifyContext(status="Running", tags={"Env": "test"}, cpu_avg=4.9, window_days=14),
            True,
        ),
        (
            ReverifyContext(status="Running", tags={"Env": "test"}, cpu_avg=5.0, window_days=14),
            False,
        ),
        (
            ReverifyContext(status="Stopped", tags={"Env": "test"}, cpu_avg=4.9, window_days=14),
            False,
        ),
        (
            ReverifyContext(status="Running", tags={"Env": "prod"}, cpu_avg=4.9, window_days=14),
            False,
        ),
        (
            ReverifyContext(status="Running", tags={"Env": "test"}, cpu_avg=4.9, window_days=13),
            False,
        ),
    ],
)
def test_recipe_evaluation_is_deterministic_at_boundaries(
    context: ReverifyContext, expected: bool
) -> None:
    recipe = ReverifyRecipe(
        required_tags={"Env": "test"},
        cpu_avg_upper_bound=5.0,
        window_days=14,
        expected_status="Running",
    )
    assert recipe.evaluate(context) is expected
    assert recipe.evaluate(context.model_copy(deep=True)) is expected


def test_consult_verified_fact_returns_receipt_and_marks_verified() -> None:
    store = InMemoryMemoryStore()
    store.put(
        _fact(
            ReverifyRecipe(
                required_tags={"Env": "test"},
                cpu_avg_upper_bound=5.0,
                expected_status="Running",
            )
        )
    )

    outcome = consult(
        "i-demo",
        _monitor(tags={"Env": "test"}),
        NOW,
        region=REGION,
        store=store,
        max_receipt_age=timedelta(hours=1),
    )

    assert outcome.disposition is MemoryDisposition.VERIFIED
    assert outcome.receipt is not None
    assert outcome.receipt["fact"] == "approved disposable test box"
    assert outcome.receipt["status"] == "verified"
    assert outcome.receipt["verified_at"] == NOW.isoformat()
    assert outcome.receipt["valid_until"] == (NOW + timedelta(hours=1)).isoformat()
    stored = store.get("i-demo")
    assert stored is not None and stored.last_verified == NOW


def test_consult_contradiction_retracts_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = InMemoryMemoryStore()
    store.put(_fact(ReverifyRecipe(required_tags={"Env": "test"})))

    with caplog.at_level(logging.WARNING, logger="src.memory.consult"):
        outcome = consult(
            "i-demo",
            _monitor(tags={"Env": "production"}),
            NOW,
            region=REGION,
            store=store,
            max_receipt_age=timedelta(hours=1),
        )

    assert outcome.disposition is MemoryDisposition.RETRACTED
    assert outcome.should_abstain is True
    stored = store.get("i-demo")
    assert stored is not None and stored.status is MemoryStatus.RETRACTED
    assert stored.retraction_reason == outcome.reason
    assert "memory fact retracted for i-demo" in caplog.text


def test_consult_without_fact_proceeds_without_read_or_receipt() -> None:
    outcome = consult(
        "i-demo",
        FakeReadMonitor([], {}),
        NOW,
        region=REGION,
        store=InMemoryMemoryStore(),
        max_receipt_age=timedelta(hours=1),
    )
    assert outcome == MemoryOutcome(MemoryDisposition.NONE)
    assert outcome.should_abstain is False
    assert outcome.receipt is None


def test_consult_read_failure_with_active_fact_abstains_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = InMemoryMemoryStore()
    store.put(_fact(ReverifyRecipe(expected_status="Running")))

    with caplog.at_level(logging.ERROR, logger="src.memory.consult"):
        outcome = consult(
            "i-demo",
            FakeReadMonitor([], {}),
            NOW,
            region=REGION,
            store=store,
            max_receipt_age=timedelta(hours=1),
        )

    assert outcome.disposition is MemoryDisposition.UNVERIFIED
    assert outcome.receipt is None
    assert outcome.should_abstain is True
    assert store.get("i-demo") is not None
    assert "memory re-verification read failed for i-demo" in caplog.text


def test_receipt_enrichment_changes_hash_and_composes_with_jury(
    action: ProposedAction,
) -> None:
    data = action.model_dump(mode="json")
    data["evidence"] = {
        **data["evidence"],
        "disagreement": None,
        "memory_receipt": None,
    }
    plain = ProposedAction.model_validate(data)
    receipt: dict[str, JsonValue] = {"fact": "test box", "status": "verified"}
    with_receipt = enrich_with_receipt(plain, receipt)
    combined = enrich_action(
        with_receipt,
        JuryAssessment(score=0.0, threshold=0.5, high_risk=False, votes=[]),
    )

    assert with_receipt is not plain
    assert action_hash(with_receipt) != action_hash(plain)
    assert combined.evidence.memory_receipt == receipt
    assert combined.evidence.disagreement is not None
    validated = ProposedAction.model_validate(combined.model_dump(mode="json"))
    assert action_hash(combined) == action_hash(validated)
