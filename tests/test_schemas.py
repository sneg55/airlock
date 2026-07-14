from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from src.common.schemas import ProposedAction


def test_rejects_extra_field(action_data: dict[str, Any]) -> None:
    action_data["params"] = {"InstanceId": ["i-other"]}
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(action_data)


def test_rejects_array_resource_id(action_data: dict[str, Any]) -> None:
    action_data["resource_id"] = ["i-demo"]
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(action_data)


def test_nested_models_forbid_extra_fields(action_data: dict[str, Any]) -> None:
    action_data["precondition"]["unexpected"] = True
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(action_data)
