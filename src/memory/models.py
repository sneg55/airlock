"""Strict resource memory models and deterministic re-verification criteria."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    RETRACTED = "retracted"


class MemoryProvenance(MemoryModel):
    who: str = Field(min_length=1)
    when: AwareDatetime
    source: str = Field(min_length=1)


class ReverifyContext(MemoryModel):
    """Normalized live state consumed by a recipe's pure predicate."""

    status: str
    tags: dict[str, str]
    cpu_avg: float | None = None
    window_days: int | None = None


class ReverifyRecipe(MemoryModel):
    """A small predicate language evaluated only by deterministic Python code."""

    required_tags: dict[str, str] = Field(default_factory=dict)
    cpu_avg_upper_bound: float | None = Field(default=None, ge=0.0)
    window_days: int = Field(default=14, gt=0)
    expected_status: str | None = None

    def evaluate(self, context: ReverifyContext) -> bool:
        """Return true only when every configured criterion holds."""

        if any(context.tags.get(key) != value for key, value in self.required_tags.items()):
            return False
        if self.expected_status is not None and context.status != self.expected_status:
            return False
        if self.cpu_avg_upper_bound is not None:
            if context.cpu_avg is None or context.window_days != self.window_days:
                return False
            if context.cpu_avg >= self.cpu_avg_upper_bound:
                return False
        return True

    @property
    def needs_cpu_samples(self) -> bool:
        return self.cpu_avg_upper_bound is not None


class MemoryFact(MemoryModel):
    resource_id: str = Field(min_length=1)
    fact: str = Field(min_length=1)
    provenance: MemoryProvenance
    recipe: ReverifyRecipe
    created_at: AwareDatetime
    last_verified: AwareDatetime
    status: MemoryStatus = MemoryStatus.ACTIVE
    retraction_reason: str | None = None

    def with_verification(self, now: datetime) -> MemoryFact:
        return MemoryFact.model_validate({**self.model_dump(mode="python"), "last_verified": now})

    def as_retracted(self, reason: str) -> MemoryFact:
        return MemoryFact.model_validate(
            {
                **self.model_dump(mode="python"),
                "status": MemoryStatus.RETRACTED,
                "retraction_reason": reason,
            }
        )


def normalized_tags(tags: Mapping[str, str]) -> dict[str, str]:
    """Copy an arbitrary monitor mapping into the strict context shape."""

    return {str(key): str(value) for key, value in tags.items()}
