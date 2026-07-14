"""Small synchronous client for one isolated MCP-over-SSE endpoint."""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from typing import Any, Protocol, cast


class McpToolClient(Protocol):
    """Minimum tool-calling surface used by cloud adapters."""

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class McpToolError(RuntimeError):
    """The MCP endpoint failed or returned a non-object result."""


@dataclass(frozen=True, slots=True)
class SseMcpToolClient:
    """Connect to one MCP SSE server for each synchronous tool call.

    Read and write callers must construct separate instances with their respective
    URLs and credentials. The imported `mcp` package supplies ClientSession and the
    legacy SSE transport used by the deployed Alibaba Cloud Ops MCP server.
    """

    server_url: str
    bearer_token: str | None = None

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.server_url:
            raise McpToolError("MCP server URL is required")
        return asyncio.run(self._call_tool(name, arguments))

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        client_module = importlib.import_module("mcp")
        sse_module = importlib.import_module("mcp.client.sse")
        session_type = client_module.ClientSession
        headers = {"Authorization": f"Bearer {self.bearer_token}"} if self.bearer_token else None
        async with (
            sse_module.sse_client(self.server_url, headers=headers) as streams,
            session_type(*streams) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, arguments)
        if getattr(result, "isError", False):
            raise McpToolError(f"MCP tool failed: {name}")
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            return cast(dict[str, Any], structured)
        content = getattr(result, "content", None)
        if isinstance(content, list):
            typed_content = cast(list[Any], content)
            if len(typed_content) == 1:
                text = getattr(typed_content[0], "text", None)
                if isinstance(text, str):
                    import json

                    decoded = json.loads(text)
                    if isinstance(decoded, dict):
                        return cast(dict[str, Any], decoded)
        raise McpToolError(f"MCP tool returned no object result: {name}")
