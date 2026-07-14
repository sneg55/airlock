from __future__ import annotations

from typing import Any

import pytest
from src.common.mcp_client import McpToolError
from src.common.schemas import ActionName
from src.executor.alibaba_write import AlibabaOpsWriteClient
from src.executor.execute import WriteClientError


class RecordingClient:
    """Capture the exact tool name and arguments passed to the MCP server."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self._fail = fail

    def call_tool(self, name: str, arguments: dict[str, str]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        if self._fail:
            raise McpToolError(f"boom: {name}")
        return {"request_id": "req-1"}


@pytest.mark.parametrize(
    ("action", "expected_tool"),
    [
        (ActionName.STOP_INSTANCES, "ECS_StopInstances"),
        (ActionName.DELETE_INSTANCES, "ECS_DeleteInstances"),
        (ActionName.STOP_DB_INSTANCES, "RDS_StopDBInstance"),
    ],
)
def test_call_translates_action_to_deployed_tool_name(
    action: ActionName, expected_tool: str
) -> None:
    client = RecordingClient()
    write = AlibabaOpsWriteClient(client)

    write.call(action, {"RegionId": "ap-southeast-1", "InstanceId": "i-demo"})

    assert client.calls == [(expected_tool, {"RegionId": "ap-southeast-1", "InstanceId": "i-demo"})]


def test_every_action_name_has_a_tool_mapping() -> None:
    client = RecordingClient()
    write = AlibabaOpsWriteClient(client)

    for action in ActionName:
        write.call(action, {"RegionId": "ap-southeast-1"})

    assert {name for name, _ in client.calls} == {
        "ECS_StopInstances",
        "ECS_DeleteInstances",
        "RDS_StopDBInstance",
    }


def test_mcp_failure_is_wrapped_with_the_deployed_tool_name() -> None:
    write = AlibabaOpsWriteClient(RecordingClient(fail=True))

    with pytest.raises(WriteClientError, match="RDS_StopDBInstance"):
        write.call(ActionName.STOP_DB_INSTANCES, {"RegionId": "ap-southeast-1"})
