"""Write-only Alibaba Cloud Ops MCP adapter."""

from __future__ import annotations

from typing import Any

from src.common.env import Env, env
from src.common.mcp_client import McpToolClient, McpToolError, SseMcpToolClient
from src.common.schemas import ActionName
from src.executor.execute import WriteClientError

# Canonical ActionName values are the signed, domain-level operation names and must stay
# stable (they are embedded in the JCS envelope and the eval conformance vectors). The
# deployed alibaba-cloud-ops-mcp-server registers tools as `{SERVICE.upper()}_{Api}`, so
# the write adapter translates each allowlisted action to its exact server tool name here.
# `RDS_StopDBInstance` is singular server-side; the enum's `StopDBInstances` is only a label.
_MCP_TOOL_NAMES: dict[ActionName, str] = {
    ActionName.STOP_INSTANCES: "ECS_StopInstances",
    ActionName.DELETE_INSTANCES: "ECS_DeleteInstances",
    ActionName.STOP_DB_INSTANCES: "RDS_StopDBInstance",
}


class AlibabaOpsWriteClient:
    """Call only the executor's mutate-credential MCP server.

    Arguments arrive from the executor's trusted-field reconstruction. This class
    accepts no model-produced parameter object and exposes only the three allowlisted
    action names represented by ActionName.
    """

    def __init__(self, client: McpToolClient) -> None:
        self._client = client

    @classmethod
    def from_env(cls, settings: Env = env) -> AlibabaOpsWriteClient:
        return cls(
            SseMcpToolClient(
                settings.write_mcp_sse_url,
                settings.write_mcp_bearer.get_secret_value() or None,
            )
        )

    def call(self, action: ActionName, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_name = _MCP_TOOL_NAMES.get(action)
        if tool_name is None:
            raise WriteClientError(f"no MCP tool mapping for action: {action.value}")
        try:
            return self._client.call_tool(tool_name, arguments)
        except McpToolError as error:
            raise WriteClientError(f"write MCP call failed: {tool_name}") from error
