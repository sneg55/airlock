"""Read-only instance and CloudMonitor boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from src.agent.idle_policy import MonitoringSample
from src.common.env import Env, env
from src.common.mcp_client import McpToolClient, SseMcpToolClient


@dataclass(frozen=True, slots=True)
class InstanceContext:
    cloud_account_id: str
    region: str
    resource_id: str
    status: str
    tags: Mapping[str, str]


class ReadMonitor(Protocol):
    def list_instances(self, region: str) -> Sequence[InstanceContext]: ...

    def describe_instance(self, region: str, resource_id: str) -> InstanceContext: ...

    def monitoring_samples(
        self, region: str, resource_id: str, *, start: datetime, end: datetime
    ) -> Sequence[MonitoringSample]: ...

    def read_state(self, region: str, resource_id: str) -> str: ...


class FakeReadMonitor:
    """Mutable read model for deterministic policy and drift tests."""

    def __init__(
        self,
        instances: Sequence[InstanceContext],
        samples: Mapping[str, Sequence[MonitoringSample]],
    ) -> None:
        self.instances = {item.resource_id: item for item in instances}
        self.samples = {key: list(value) for key, value in samples.items()}
        self.reads: list[tuple[str, str]] = []

    def list_instances(self, region: str) -> Sequence[InstanceContext]:
        return [item for item in self.instances.values() if item.region == region]

    def describe_instance(self, region: str, resource_id: str) -> InstanceContext:
        self.reads.append((region, resource_id))
        item = self.instances[resource_id]
        if item.region != region:
            raise KeyError(f"instance not in region: {resource_id}")
        return item

    def monitoring_samples(
        self, region: str, resource_id: str, *, start: datetime, end: datetime
    ) -> Sequence[MonitoringSample]:
        self.describe_instance(region, resource_id)
        return [
            sample
            for sample in self.samples.get(resource_id, [])
            if start <= sample.observed_at <= end
        ]

    def read_state(self, region: str, resource_id: str) -> str:
        return self.describe_instance(region, resource_id).status

    def set_status(self, resource_id: str, status: str) -> None:
        current = self.instances[resource_id]
        self.instances[resource_id] = InstanceContext(
            current.cloud_account_id,
            current.region,
            current.resource_id,
            status,
            current.tags,
        )


def _unwrap_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the API payload, tolerating a raw-OpenAPI ``body`` wrapper.

    CommonAPICaller returns whatever ``client.call_api`` produced, which may nest the
    real fields under ``body`` or return them flat. Both are accepted.
    """

    body = payload.get("body")
    if isinstance(body, dict):
        return cast(dict[str, Any], body)
    return payload


def _instance_items(body: dict[str, Any]) -> list[Any]:
    """Pull the instance list out of a DescribeInstances body.

    Alibaba nests the list under ``Instances.Instance``; a flat list is also tolerated.
    """

    raw: Any = body.get("Instances", body.get("instances", []))
    if isinstance(raw, dict):
        inner = cast(dict[str, Any], raw)
        raw = inner.get("Instance", inner.get("instance", []))
    if not isinstance(raw, list):
        raise ValueError("DescribeInstances returned invalid instances")
    return cast(list[Any], raw)


def _normalize_tags(item: dict[str, Any]) -> dict[str, str]:
    """Normalize ECS tags into a flat str->str map.

    Live ECS returns ``Tags.Tag`` as a list of ``{TagKey, TagValue}``; a flat mapping is
    also tolerated for fakes.
    """

    tags = item.get("Tags", item.get("tags", {}))
    if not isinstance(tags, dict):
        raise ValueError("DescribeInstances returned invalid tags")
    tag_map = cast(dict[str, Any], tags)
    tag_list = tag_map.get("Tag")
    if isinstance(tag_list, list):
        result: dict[str, str] = {}
        for entry in cast(list[Any], tag_list):
            if isinstance(entry, dict):
                pair = cast(dict[str, Any], entry)
                key = str(pair.get("TagKey", pair.get("Key", "")))
                result[key] = str(pair.get("TagValue", pair.get("Value", "")))
        return result
    return {str(key): str(value) for key, value in tag_map.items()}


def _parse_datapoints(payload: dict[str, Any]) -> dict[datetime, float]:
    """Parse CMS DescribeMetricList datapoints into observed_at -> value.

    CMS returns ``Datapoints`` as a JSON-encoded string of objects carrying a millisecond
    ``timestamp`` and an ``Average``. An already-decoded list is also tolerated.
    """

    raw: Any = payload.get("Datapoints", payload.get("datapoints", []))
    if isinstance(raw, str):
        raw = json.loads(raw) if raw.strip() else []
    if not isinstance(raw, list):
        raise ValueError("CMS_GetMetricList returned invalid datapoints")
    result: dict[datetime, float] = {}
    for entry in cast(list[Any], raw):
        if not isinstance(entry, dict):
            raise ValueError("CMS_GetMetricList returned a non-object datapoint")
        point = cast(dict[str, Any], entry)
        timestamp = point.get("timestamp", point.get("Timestamp"))
        value = point.get("Average", point.get("average"))
        if timestamp is None or value is None:
            continue
        observed_at = datetime.fromtimestamp(int(timestamp) / 1000, tz=UTC)
        result[observed_at] = float(value)
    return result


def _epoch_millis(when: datetime) -> str:
    return str(int(when.timestamp() * 1000))


class AlibabaReadMonitor:
    """Read-credential Alibaba Cloud Ops MCP adapter.

    The injected client must point only at the read MCP SSE endpoint. That server is
    deployed with ``--visible-tools CommonAPICaller,CMS_GetMetricList`` and
    ``--services ecs,cms``. Enumeration and instance-state reads go through the generic
    ``CommonAPICaller`` (``ecs:DescribeInstances`` routes to the correct ECS endpoint);
    windowed CPU/mem/disk metrics go through the forked ``CMS_GetMetricList`` tool
    (``cms:DescribeMetricList`` against the metrics endpoint). RAM restricts the read
    credential to exactly those two actions. Response shapes and metric names are marked
    for live verification in the implementation notes.
    """

    # acs_ecs_dashboard metric names (agent-level, matching upstream CMS_Get*Data). These
    # depend on the CloudMonitor agent being installed and need live confirmation.
    _CPU_METRIC = "cpu_total"
    _MEM_METRIC = "memory_usedutilization"
    _DISK_METRIC = "diskusage_utilization"
    _PERIOD_SECONDS = "3600"

    def __init__(self, client: McpToolClient, *, cloud_account_id: str) -> None:
        self._client = client
        self._account = cloud_account_id

    @classmethod
    def from_env(cls, settings: Env = env) -> AlibabaReadMonitor:
        return cls(
            SseMcpToolClient(settings.read_mcp_sse_url),
            cloud_account_id=settings.bound_account_id,
        )

    def _describe_instances(self, parameters: dict[str, Any]) -> list[Any]:
        payload = self._client.call_tool(
            "CommonAPICaller",
            {"service": "ecs", "api": "DescribeInstances", "parameters": parameters},
        )
        return _instance_items(_unwrap_body(payload))

    def list_instances(self, region: str) -> Sequence[InstanceContext]:
        items = self._describe_instances({"RegionId": region})
        return [self._instance(item, region) for item in items]

    def describe_instance(self, region: str, resource_id: str) -> InstanceContext:
        items = self._describe_instances({"RegionId": region, "InstanceIds": [resource_id]})
        if len(items) != 1:
            raise ValueError("DescribeInstances did not return exactly one instance")
        return self._instance(items[0], region)

    def monitoring_samples(
        self, region: str, resource_id: str, *, start: datetime, end: datetime
    ) -> Sequence[MonitoringSample]:
        window = {
            "InstanceIds": [resource_id],
            "RegionId": region,
            "StartTime": _epoch_millis(start),
            "EndTime": _epoch_millis(end),
            "Period": self._PERIOD_SECONDS,
        }
        cpu = self._metric(self._CPU_METRIC, window)
        memory = self._metric(self._MEM_METRIC, window)
        disk = self._metric(self._DISK_METRIC, window)
        shared = sorted(cpu.keys() & memory.keys())
        return [MonitoringSample(at, cpu[at], memory[at], disk.get(at)) for at in shared]

    def read_state(self, region: str, resource_id: str) -> str:
        return self.describe_instance(region, resource_id).status

    def _metric(self, metric_name: str, window: dict[str, Any]) -> dict[datetime, float]:
        payload = self._client.call_tool("CMS_GetMetricList", {"MetricName": metric_name, **window})
        return _parse_datapoints(payload)

    def _instance(self, value: Any, region: str) -> InstanceContext:
        if not isinstance(value, dict):
            raise ValueError("DescribeInstances returned a non-object instance")
        item = cast(dict[str, Any], value)
        return InstanceContext(
            cloud_account_id=self._account,
            region=region,
            resource_id=str(item.get("InstanceId", item.get("resource_id", ""))),
            status=str(item.get("Status", item.get("status", ""))),
            tags=_normalize_tags(item),
        )
