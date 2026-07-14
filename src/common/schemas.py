"""Strict schemas for actions reviewed by an Airlock operator."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and non-finite floats."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ActionName(StrEnum):
    """Write operations supported by the Cycle A executor."""

    STOP_INSTANCES = "StopInstances"
    DELETE_INSTANCES = "DeleteInstances"
    STOP_DB_INSTANCES = "StopDBInstances"


class ActionStage(StrEnum):
    STOP = "stop"
    DELETE = "delete"


class Precondition(StrictModel):
    expected_status: str
    observed_at: AwareDatetime


class Evidence(StrictModel):
    idle_window_days: int
    cpu_avg: float
    cpu_max: float
    mem_avg: float
    samples: int
    collected_at: AwareDatetime
    monitor_provenance: list[dict[str, JsonValue]]
    disagreement: dict[str, JsonValue] | None = None
    memory_receipt: dict[str, JsonValue] | None = None


class ProposedAction(StrictModel):
    """Exactly one reviewed operation against exactly one resource."""

    schema_version: str
    cloud_account_id: str
    region: str
    resource_id: str
    action: ActionName
    precondition: Precondition
    evidence: Evidence
    policy_reason: str
    stage: ActionStage
    created_at: AwareDatetime
